"""Veröffentlichung des Website-Katalogs: ``dev`` und ``prod``.

Mechanismus (abgeleitet aus den Website-Workflows):

* ``dev`` ist der Integrationsbranch. Ein Commit auf ``dev`` löst ``fd-ci`` aus;
  nach einem grünen Lauf deployt ``fd-deploy-dev`` und veröffentlicht das
  Pre-Release ``dev-v<version>``.
* ``main`` wird ausschließlich von ``fd-deploy-prd`` aktualisiert (Merge
  ``dev`` → ``main``, Pages-Deploy, stabiles Release). Direkte Commits auf
  ``main`` sind im Website-Repository unzulässig, deshalb committet auch die
  Produktion zuerst nach ``dev`` und dispatcht danach ``fd-deploy-prd``.

Alle schreibenden Aufrufe liegen hinter ``if dry_run`` bzw. hinter dem
Änderungsvergleich. Ein Dry-Run liest ausschließlich (Branch, Baum, ggf. den
entfernten Katalog) und berührt weder Store noch Repository.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import aiohttp

from .export import (
    CATALOG_RELATIVE_PATH,
    IMAGES_RELATIVE_PATH,
    catalog_bytes,
    desired_image_name,
    ordered_images,
)
from .models import Product, StoreError, normalise_text
from .store import CatalogStore, ImageStorage

_LOGGER = logging.getLogger(__name__)

PUBLISH_TARGETS: tuple[str, ...] = ("dev", "prod")
GITHUB_API_URL = "https://api.github.com"
GITHUB_TIMEOUT = 30
MAX_MESSAGE_LENGTH = 200
#: Obergrenze für die Dateiliste in der Antwort (Zählungen bleiben vollständig).
MAX_CHANGE_ENTRIES = 500

DEFAULT_COMMIT_MESSAGE = "chore(catalog): Katalog aus Home Assistant veröffentlicht (#289)"

STATUS_ADDED = "added"
STATUS_CHANGED = "changed"
STATUS_REMOVED = "removed"
STATUS_UNCHANGED = "unchanged"
FILE_STATUSES: tuple[str, ...] = (
    STATUS_ADDED,
    STATUS_CHANGED,
    STATUS_REMOVED,
    STATUS_UNCHANGED,
)

KIND_CATALOG = "catalog"
KIND_IMAGE = "image"


class PublishError(StoreError):
    """Veröffentlichung nicht möglich, mit deutscher Meldung."""

    code = "publish_failed"


class PublishNotConfigured(PublishError):
    code = "publish_not_configured"


class PublishTokenMissing(PublishError):
    code = "publish_token_missing"


class GitHubError(PublishError):
    code = "github_failed"


class GitHubUnauthorized(GitHubError):
    code = "github_unauthorized"


class GitHubNotFound(GitHubError):
    code = "github_not_found"


class GitHubConflict(GitHubError):
    code = "github_conflict"


@dataclass(frozen=True, slots=True)
class PublishSettings:
    """Ziel-Repository und Zugangsdaten (Token wird nie zurückgegeben oder geloggt)."""

    repository: str
    dev_branch: str
    prod_branch: str
    prod_workflow: str
    token: str = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.repository.strip() and self.prod_workflow.strip())

    def require_token(self) -> None:
        if not self.token:
            raise PublishTokenMissing(
                "Es ist kein GitHub-Token hinterlegt. Bitte unter Einstellungen → "
                "Frickeldave Store → Veröffentlichen ergänzen."
            )


@dataclass(slots=True)
class RemoteTree:
    """Kopf-Commit und Blob-SHAs des Ziel-Branches."""

    head_sha: str
    tree_sha: str
    blobs: dict[str, str] = field(default_factory=dict)
    truncated: bool = False


@dataclass(slots=True)
class FileChange:
    """Eine Datei im Veröffentlichungs-Diff."""

    path: str
    status: str
    kind: str
    article_number: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "status": self.status,
            "kind": self.kind,
            "articleNumber": self.article_number,
        }


def blob_sha(data: bytes) -> str:
    """Git-Blob-SHA (sha1 über ``blob <größe>\\0`` plus Inhalt)."""

    header = f"blob {len(data)}\0".encode()
    return hashlib.sha1(header + data).hexdigest()  # noqa: S324 - Git verlangt SHA-1


def publish_settings_from_store(store: CatalogStore) -> PublishSettings:
    settings = store.settings.get("publish") or {}
    return PublishSettings(
        repository=str(settings.get("repository", "") or ""),
        dev_branch=str(settings.get("dev_branch", "") or "dev"),
        prod_branch=str(settings.get("prod_branch", "") or "main"),
        prod_workflow=str(settings.get("prod_workflow", "") or ""),
        token=str(settings.get("token", "") or ""),
    )


def _is_managed_path(path: str) -> bool:
    return path == CATALOG_RELATIVE_PATH.as_posix() or path.startswith(
        f"{IMAGES_RELATIVE_PATH.as_posix()}/"
    )


def build_artifacts(
    products: Iterable[Product],
    locations: list[dict[str, str]],
    images: ImageStorage,
) -> dict[str, bytes]:
    """Alle zu veröffentlichenden Dateien als Pfad → Bytes (read-only)."""

    product_list = list(products)
    artifacts: dict[str, bytes] = {
        CATALOG_RELATIVE_PATH.as_posix(): catalog_bytes(product_list, locations=locations)
    }
    for product in sorted(product_list, key=lambda item: item.article_number):
        if product.archived:
            continue
        for index, image in enumerate(ordered_images(product), start=1):
            target_name = desired_image_name(product, index, image.name)
            source = images.path_for(product.article_number, image.name)
            if not source.is_file():
                continue
            path = (
                IMAGES_RELATIVE_PATH / product.category / product.article_number / target_name
            ).as_posix()
            artifacts[path] = source.read_bytes()
    return artifacts


def _article_from_path(path: str) -> str:
    if path == CATALOG_RELATIVE_PATH.as_posix():
        return ""
    parts = Path(path).parts
    return parts[-2] if len(parts) >= 2 else ""


def diff_artifacts(local: Mapping[str, bytes], remote: Mapping[str, str]) -> list[FileChange]:
    """Dateien vergleichen; nur verwaltete Pfade gelten als ``removed``."""

    changes: list[FileChange] = []
    for path in sorted(local):
        kind = KIND_CATALOG if path == CATALOG_RELATIVE_PATH.as_posix() else KIND_IMAGE
        article = _article_from_path(path)
        remote_sha = remote.get(path)
        if remote_sha is None:
            status = STATUS_ADDED
        elif remote_sha == blob_sha(local[path]):
            status = STATUS_UNCHANGED
        else:
            status = STATUS_CHANGED
        changes.append(FileChange(path, status, kind, article))
    for path in sorted(remote):
        if path in local or not _is_managed_path(path):
            continue
        kind = KIND_CATALOG if path == CATALOG_RELATIVE_PATH.as_posix() else KIND_IMAGE
        changes.append(FileChange(path, STATUS_REMOVED, kind, _article_from_path(path)))
    return changes


def _status_counts(changes: Iterable[FileChange]) -> dict[str, int]:
    counts = {status: 0 for status in FILE_STATUSES}
    for change in changes:
        counts[change.status] = counts.get(change.status, 0) + 1
    counts["total"] = sum(value for key, value in counts.items() if key != "total")
    return counts


def _catalog_products(raw: bytes | None) -> dict[str, dict[str, Any]]:
    if not raw:
        return {}
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, TypeError):
        return {}
    entries = document.get("products") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if isinstance(entry, dict) and entry.get("articleNumber"):
            result[str(entry["articleNumber"])] = entry
    return result


def diff_products(local_catalog: bytes, remote_catalog: bytes | None) -> dict[str, int]:
    """Produkt-Diff über Artikelnummern (Grundlage für „was würde sich ändern“)."""

    local = _catalog_products(local_catalog)
    remote = _catalog_products(remote_catalog)
    counts = {"added": 0, "changed": 0, "removed": 0, "unchanged": 0, "total": len(local)}
    for article, entry in local.items():
        if article not in remote:
            counts["added"] += 1
        elif remote[article] != entry:
            counts["changed"] += 1
        else:
            counts["unchanged"] += 1
    counts["removed"] = sum(1 for article in remote if article not in local)
    return counts


def diff_images(changes: Iterable[FileChange], local_total: int) -> dict[str, int]:
    counts = {"added": 0, "changed": 0, "removed": 0, "unchanged": 0, "total": local_total}
    for change in changes:
        if change.kind == KIND_IMAGE:
            counts[change.status] += 1
    return counts


class GitHubClientProtocol(Protocol):
    """Von :class:`PublishService` genutzte GitHub-Operationen (testbar)."""

    async def async_read_tree(self) -> RemoteTree: ...

    async def async_read_file(self, path: str) -> bytes | None: ...

    async def async_commit(
        self,
        artifacts: Mapping[str, bytes],
        changes: list[FileChange],
        *,
        head_sha: str,
        tree_sha: str,
        message: str,
    ) -> str: ...

    async def async_dispatch_workflow(
        self, workflow: str, *, ref: str, inputs: dict[str, Any]
    ) -> None: ...

    async def async_close(self) -> None: ...


class GitHubClient:
    """Minimaler GitHub-REST-Client (Git Data API + Actions-Dispatch)."""

    def __init__(
        self,
        settings: PublishSettings,
        *,
        session: aiohttp.ClientSession | None = None,
        timeout: int = GITHUB_TIMEOUT,
        api_url: str = GITHUB_API_URL,
    ) -> None:
        self._settings = settings
        self._session = session
        self._owns_session = session is None
        self._timeout = timeout
        self._api_url = api_url.rstrip("/")

    # ------------------------------------------------------------------ HTTP
    def _url(self, path: str) -> str:
        return f"{self._api_url}/repos/{self._settings.repository}/{path.lstrip('/')}"

    def _client_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    def _headers(self) -> dict[str, str]:
        # Der Token wird ausschließlich im Header verwendet: nie loggen, nie zurückgeben.
        return {
            "Authorization": f"Bearer {self._settings.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def _async_json(
        self, method: str, url: str, *, payload: dict[str, Any] | None = None
    ) -> Any:
        session = self._client_session()
        try:
            async with session.request(
                method,
                url,
                json=payload,
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=self._timeout),
            ) as response:
                if response.status == 204 or not response.content_length:
                    self._raise_for_status(response.status, {})
                    return None
                body = await response.json(content_type=None)
                self._raise_for_status(response.status, body)
                return body
        except (TimeoutError, aiohttp.ClientError) as err:
            raise GitHubError(
                "GitHub ist momentan nicht erreichbar. Bitte später erneut versuchen.",
                code="github_unavailable",
            ) from err

    async def _async_bytes(self, url: str) -> bytes | None:
        session = self._client_session()
        try:
            headers = {**self._headers(), "Accept": "application/vnd.github.raw+json"}
            async with session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=self._timeout)
            ) as response:
                if response.status == 404:
                    return None
                if response.status >= 400:
                    self._raise_for_status(response.status, {})
                return await response.read()
        except (TimeoutError, aiohttp.ClientError) as err:
            raise GitHubError(
                "GitHub ist momentan nicht erreichbar. Bitte später erneut versuchen.",
                code="github_unavailable",
            ) from err

    def _raise_for_status(self, status: int, body: Any) -> None:
        if status < 400:
            return
        message = ""
        if isinstance(body, dict):
            message = str(body.get("message", "") or "")
        _LOGGER.debug("GitHub-Antwort %s: %s", status, message)
        if status in (401, 403):
            raise GitHubUnauthorized(
                "Der GitHub-Token wurde abgelehnt oder hat keine Schreibrechte für "
                f"„{self._settings.repository}“."
            )
        if status == 404:
            raise GitHubNotFound(
                f"Repository „{self._settings.repository}“ oder Branch nicht gefunden. "
                "Bitte Repository und Token prüfen."
            )
        if status == 409 or status == 422:
            raise GitHubConflict(
                "Der Branch wurde während der Veröffentlichung geändert. "
                "Bitte den Vorgang erneut starten."
            )
        raise GitHubError(f"GitHub hat die Anfrage abgelehnt (HTTP {status}).")

    # ------------------------------------------------------------------ Lesen
    async def async_read_tree(self) -> RemoteTree:
        branch = quote(self._settings.dev_branch, safe="")
        ref = await self._async_json("GET", self._url(f"git/ref/heads/{branch}"))
        head_sha = str((ref.get("object") or {}).get("sha", ""))
        if not head_sha:
            raise GitHubError("Der Branch hat keinen lesbaren Kopf-Commit.")
        commit = await self._async_json("GET", self._url(f"git/commits/{head_sha}"))
        tree_sha = str((commit.get("tree") or {}).get("sha", ""))
        if not tree_sha:
            raise GitHubError("Der Branch hat keinen lesbaren Baum.")
        tree = await self._async_json("GET", self._url(f"git/trees/{tree_sha}?recursive=1"))
        blobs: dict[str, str] = {}
        for entry in tree.get("tree") or []:
            if isinstance(entry, dict) and entry.get("type") == "blob":
                blobs[str(entry.get("path", ""))] = str(entry.get("sha", ""))
        return RemoteTree(
            head_sha=head_sha,
            tree_sha=tree_sha,
            blobs=blobs,
            truncated=bool(tree.get("truncated", False)),
        )

    async def async_read_file(self, path: str) -> bytes | None:
        branch = quote(self._settings.dev_branch, safe="")
        return await self._async_bytes(self._url(f"contents/{path}?ref={branch}"))

    # ------------------------------------------------------------------ Schreiben
    async def async_commit(
        self,
        artifacts: Mapping[str, bytes],
        changes: list[FileChange],
        *,
        head_sha: str,
        tree_sha: str,
        message: str,
    ) -> str:
        tree_entries: list[dict[str, Any]] = []
        for change in changes:
            if change.status == STATUS_REMOVED:
                tree_entries.append(
                    {"path": change.path, "mode": "100644", "type": "blob", "sha": None}
                )
                continue
            if change.status == STATUS_UNCHANGED:
                continue
            data = artifacts[change.path]
            blob = await self._async_json(
                "POST",
                self._url("git/blobs"),
                payload={
                    "content": base64.b64encode(data).decode("ascii"),
                    "encoding": "base64",
                },
            )
            tree_entries.append(
                {
                    "path": change.path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": str(blob.get("sha", "")),
                }
            )
        tree = await self._async_json(
            "POST",
            self._url("git/trees"),
            # base_tree hält alle nicht genannten Dateien unverändert.
            payload={"base_tree": tree_sha, "tree": tree_entries},
        )
        tree_sha = str(tree.get("sha", ""))
        commit = await self._async_json(
            "POST",
            self._url("git/commits"),
            payload={"message": message, "tree": tree_sha, "parents": [head_sha]},
        )
        commit_sha = str(commit.get("sha", ""))
        branch = quote(self._settings.dev_branch, safe="")
        await self._async_json(
            "PATCH",
            self._url(f"git/refs/heads/{branch}"),
            payload={"sha": commit_sha, "force": False},
        )
        return commit_sha

    async def async_dispatch_workflow(
        self, workflow: str, *, ref: str, inputs: dict[str, Any]
    ) -> None:
        await self._async_json(
            "POST",
            self._url(f"actions/workflows/{quote(workflow, safe='')}/run"),
            payload={"ref": ref, "inputs": inputs},
        )

    async def async_close(self) -> None:
        if self._session is None or not self._owns_session:
            return
        session = self._session
        self._session = None
        await session.close()


class PublishService:
    """Baut den Veröffentlichungsplan und führt ihn (außer im Dry-Run) aus."""

    def __init__(
        self,
        store: CatalogStore,
        *,
        client_factory: Callable[[PublishSettings], GitHubClientProtocol] | None = None,
    ) -> None:
        self._store = store
        self._client_factory = client_factory

    def _client(self, settings: PublishSettings) -> GitHubClientProtocol:
        if self._client_factory is not None:
            return self._client_factory(settings)
        return GitHubClient(settings)

    async def async_publish(
        self,
        target: str,
        *,
        dry_run: bool = False,
        message: str = "",
        user_id: str = "",
    ) -> dict[str, Any]:
        if target not in PUBLISH_TARGETS:
            raise PublishError("target muss 'dev' oder 'prod' sein.", code="validation_error")
        settings = publish_settings_from_store(self._store)
        if not settings.is_configured:
            raise PublishNotConfigured(
                "Die Veröffentlichung ist nicht konfiguriert. Bitte Repository und "
                "Workflow in den Einstellungen ergänzen."
            )
        settings.require_token()
        commit_message = (
            normalise_text(message, "message", max_length=MAX_MESSAGE_LENGTH)
            or DEFAULT_COMMIT_MESSAGE
        )

        products = self._store.products()
        locations = self._store.locations()
        artifacts = await asyncio.to_thread(
            build_artifacts, products, locations, self._store.images
        )

        client = self._client(settings)
        try:
            tree = await client.async_read_tree()
            if tree.truncated:  # pragma: no cover - nur bei sehr großen Repositories
                raise GitHubError(
                    "Der Dateibaum des Repositorys ist zu groß für einen Vergleich.",
                    code="github_failed",
                )
            changes = diff_artifacts(artifacts, tree.blobs)
            counts = _status_counts(changes)

            local_catalog = artifacts[CATALOG_RELATIVE_PATH.as_posix()]
            remote_catalog: bytes | None = None
            catalog_change = next((item for item in changes if item.kind == KIND_CATALOG), None)
            if catalog_change is not None and catalog_change.status != STATUS_UNCHANGED:
                remote_catalog = await client.async_read_file(CATALOG_RELATIVE_PATH.as_posix())
            product_counts = diff_products(local_catalog, remote_catalog)
            image_counts = diff_images(changes, len(artifacts) - 1)

            result = self._result(
                target=target,
                settings=settings,
                dry_run=dry_run,
                changes=changes,
                counts=counts,
                product_counts=product_counts,
                image_counts=image_counts,
            )

            if dry_run:
                # Strukturell garantiert: bis hierher wurde ausschließlich gelesen.
                return result

            commit_sha: str | None = None
            if counts[STATUS_ADDED] or counts[STATUS_CHANGED] or counts[STATUS_REMOVED]:
                commit_sha = await client.async_commit(
                    artifacts,
                    changes,
                    head_sha=tree.head_sha,
                    tree_sha=tree.tree_sha,
                    message=commit_message,
                )
                _LOGGER.info(
                    "Katalog nach %s veröffentlicht (%s Dateien, Commit %s, Benutzer %s)",
                    settings.dev_branch,
                    counts["total"],
                    commit_sha[:7],
                    user_id or "-",
                )

            if target == "prod":
                await client.async_dispatch_workflow(
                    settings.prod_workflow,
                    ref=settings.prod_branch,
                    inputs={"dry_run": False},
                )
                result["deployment"].update(
                    {
                        "triggered": True,
                        "workflow_run": True,
                        "mode": "workflow_dispatch",
                    }
                )
            else:
                result["deployment"]["triggered"] = commit_sha is not None
            result["commit"] = commit_sha
            result["changed"] = commit_sha is not None
            result["summary"] = self._summary(
                target,
                settings,
                counts,
                product_counts,
                image_counts,
                dry_run=False,
                committed=commit_sha is not None,
            )
            return result
        finally:
            await client.async_close()

    def _result(
        self,
        *,
        target: str,
        settings: PublishSettings,
        dry_run: bool,
        changes: list[FileChange],
        counts: dict[str, int],
        product_counts: dict[str, int],
        image_counts: dict[str, int],
    ) -> dict[str, Any]:
        files = changes[:MAX_CHANGE_ENTRIES]
        deployment = {
            "triggered": False,
            "would_trigger": bool(
                target == "prod"
                or counts[STATUS_ADDED]
                or counts[STATUS_CHANGED]
                or counts[STATUS_REMOVED]
            ),
            "mode": "workflow_dispatch" if target == "prod" else "push",
            "workflow": settings.prod_workflow if target == "prod" else "fd-deploy-dev.yml",
            "branch": settings.prod_branch if target == "prod" else settings.dev_branch,
        }
        return {
            "target": target,
            "dry_run": dry_run,
            "repository": settings.repository,
            "branch": settings.dev_branch,
            "commit": None,
            "changed": False,
            "counts": counts,
            "products": product_counts,
            "images": image_counts,
            "files": [change.to_dict() for change in files],
            "files_truncated": len(changes) > MAX_CHANGE_ENTRIES,
            "deployment": deployment,
            "summary": self._summary(
                target,
                settings,
                counts,
                product_counts,
                image_counts,
                dry_run=dry_run,
                committed=False,
            ),
        }

    @staticmethod
    def _summary(
        target: str,
        settings: PublishSettings,
        counts: dict[str, int],
        product_counts: dict[str, int],
        image_counts: dict[str, int],
        *,
        dry_run: bool,
        committed: bool,
    ) -> str:
        def _describe(values: dict[str, int], noun: str) -> str:
            return (
                f"{values['added']} {noun} neu, {values['changed']} geändert, "
                f"{values['removed']} entfernt, {values['unchanged']} unverändert"
            )

        files = (
            f"{counts['added']} Dateien neu, {counts['changed']} geändert, "
            f"{counts['removed']} entfernt, {counts['unchanged']} unverändert"
        )
        parts = [
            f"{_describe(product_counts, 'Produkte')} ({product_counts['total']} gesamt)",
            f"{_describe(image_counts, 'Bilder')} ({image_counts['total']} gesamt)",
            files,
        ]
        if dry_run:
            if target == "prod":
                parts.append(
                    "Dry-Run: Es wird nichts geschrieben. Beim Veröffentlichen würde "
                    f"zuerst ein Commit auf {settings.dev_branch} gepusht und danach "
                    f"{settings.prod_workflow} ausgelöst (main wird nur über diesen "
                    "Workflow aktualisiert)."
                )
            else:
                parts.append(
                    "Dry-Run: Es wird nichts geschrieben. Beim Veröffentlichen würde ein "
                    f"Commit auf {settings.dev_branch} gepusht und damit fd-ci sowie "
                    "fd-deploy-dev ausgelöst."
                )
            return "; ".join(parts) + "."
        if target == "prod":
            prefix = "Katalog auf dev committet" if committed else "Keine Dateiänderungen auf dev"
            return "; ".join(parts) + f". {prefix}; {settings.prod_workflow} wurde ausgelöst."
        if committed:
            return (
                "; ".join(parts)
                + f". Commit auf {settings.dev_branch} gepusht; fd-ci und fd-deploy-dev "
                "wurden damit ausgelöst."
            )
        return "; ".join(parts) + ". Keine Änderungen: nichts committet, kein Deployment."


__all__ = [
    "FILE_STATUSES",
    "GitHubClient",
    "GitHubError",
    "PublishError",
    "PublishNotConfigured",
    "PublishService",
    "PublishSettings",
    "PublishTokenMissing",
    "RemoteTree",
    "blob_sha",
    "build_artifacts",
    "diff_artifacts",
    "diff_images",
    "diff_products",
    "publish_settings_from_store",
]
