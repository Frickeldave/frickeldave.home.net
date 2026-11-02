"""Tests für die Veröffentlichung nach dev/prod und den Dry-Run."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from image_helpers import jpeg_bytes

from custom_components.frickeldave_store import FrickeldaveStoreRuntime
from custom_components.frickeldave_store.const import DOMAIN, STORAGE_KEY
from custom_components.frickeldave_store.permissions import PermissionManager
from custom_components.frickeldave_store.products import ProductService
from custom_components.frickeldave_store.publish import (
    FileChange,
    GitHubError,
    PublishArtifactsUnavailable,
    PublishEmptyCatalog,
    PublishNotConfigured,
    PublishService,
    PublishSettings,
    PublishTokenMissing,
    RemoteTree,
    blob_sha,
)
from custom_components.frickeldave_store.store import CatalogStore
from custom_components.frickeldave_store.views import PublishView


class FakeGitHub:
    """Zeichnet alle Aufrufe auf; Schreibmethoden zählen als Wirkung."""

    def __init__(
        self,
        settings: PublishSettings,
        *,
        remote: dict[str, str] | None = None,
        files: dict[str, bytes] | None = None,
    ) -> None:
        self.settings = settings
        self.remote = dict(remote or {})
        self.files = dict(files or {})
        self.commits: list[dict[str, Any]] = []
        self.dispatches: list[dict[str, Any]] = []
        self.reads: list[str] = []
        self.closed = False
        self.apply_commit = True

    async def async_read_tree(self) -> RemoteTree:
        return RemoteTree(head_sha="head-sha", tree_sha="base-tree", blobs=dict(self.remote))

    async def async_read_file(self, path: str) -> bytes | None:
        self.reads.append(path)
        return self.files.get(path)

    async def async_commit(
        self,
        artifacts: dict[str, bytes],
        changes: list[Any],
        *,
        head_sha: str,
        tree_sha: str,
        message: str,
    ) -> str:
        self.commits.append(
            {
                "artifacts": dict(artifacts),
                "changes": list(changes),
                "head_sha": head_sha,
                "tree_sha": tree_sha,
                "message": message,
            }
        )
        if self.apply_commit:
            for change in changes:
                if change.status == "removed":
                    self.remote.pop(change.path, None)
                else:
                    self.remote[change.path] = blob_sha(artifacts[change.path])
        return f"commit-{len(self.commits)}"

    async def async_dispatch_workflow(
        self, workflow: str, *, ref: str, inputs: dict[str, Any]
    ) -> None:
        self.dispatches.append({"workflow": workflow, "ref": ref, "inputs": inputs})

    async def async_close(self) -> None:
        self.closed = True


async def _store_with_product(tmp_path: Path) -> tuple[CatalogStore, str]:
    store = CatalogStore(data_dir=tmp_path)
    await store.async_load()
    store.update_settings(
        {
            "publish": {
                "repository": "Frickeldave/frickeldave.github.io",
                "dev_branch": "dev",
                "prod_branch": "main",
                "prod_workflow": "fd-deploy-prd.yml",
                "token": "ghp-test",
            }
        }
    )
    service = ProductService(store)
    product = await service.async_create(
        {"name": "Schild", "category": "LA", "stock": {"lager": 2, "verkaufsschrank": 1}}
    )
    await service.async_add_image(
        product.article_number,
        filename="bild.jpg",
        content_type="image/jpeg",
        data=jpeg_bytes((64, 48)),
    )
    await store.async_save()
    return store, product.article_number


def _catalog_path() -> str:
    return "public/data/handmade.json"


@pytest.fixture
def store_snapshot() -> Any:
    def _snapshot(store: CatalogStore, data_dir: Path) -> tuple[str, bytes | None]:
        return (
            json.dumps(store.data, sort_keys=True, ensure_ascii=False),
            (data_dir / STORAGE_KEY).read_bytes() if (data_dir / STORAGE_KEY).is_file() else None,
        )

    return _snapshot


async def test_dry_run_reports_changes_without_any_write(tmp_path: Path) -> None:
    store, article = await _store_with_product(tmp_path)
    fake = FakeGitHub(
        PublishSettings(
            "Frickeldave/frickeldave.github.io", "dev", "main", "fd-deploy-prd.yml", "ghp-test"
        )
    )
    service = PublishService(store, client_factory=lambda settings: fake)
    before = (
        json.dumps(store.data, sort_keys=True, ensure_ascii=False),
        (tmp_path / STORAGE_KEY).read_bytes(),
    )

    result = await service.async_publish("dev", dry_run=True)

    after = (
        json.dumps(store.data, sort_keys=True, ensure_ascii=False),
        (tmp_path / STORAGE_KEY).read_bytes(),
    )
    assert before == after, "Dry-Run darf den Store nicht verändern"
    assert fake.commits == []
    assert fake.dispatches == []
    assert result["dry_run"] is True
    assert result["commit"] is None
    assert result["changed"] is False
    assert result["deployment"]["triggered"] is False
    assert result["deployment"]["would_trigger"] is True
    assert result["counts"]["added"] == 2  # Katalog + ein Bild
    assert result["products"]["added"] == 1
    assert result["images"]["added"] == 1
    assert "Dry-Run" in result["summary"]
    assert "nichts geschrieben" in result["summary"]
    catalog_file = next(item for item in result["files"] if item["path"] == _catalog_path())
    assert catalog_file["status"] == "added"
    assert catalog_file["kind"] == "catalog"
    image_file = next(item for item in result["files"] if item["kind"] == "image")
    assert image_file["articleNumber"] == article
    assert image_file["path"].startswith("src/assets/handmade/LA/")


async def test_publish_dev_commits_once_and_is_idempotent(tmp_path: Path) -> None:
    store, _ = await _store_with_product(tmp_path)
    fake = FakeGitHub(
        PublishSettings(
            "Frickeldave/frickeldave.github.io", "dev", "main", "fd-deploy-prd.yml", "ghp-test"
        )
    )
    service = PublishService(store, client_factory=lambda settings: fake)

    first = await service.async_publish("dev", message="chore(catalog): test (#289)")
    assert first["changed"] is True
    assert first["commit"] == "commit-1"
    assert first["deployment"]["triggered"] is True
    assert first["deployment"]["mode"] == "push"
    assert fake.dispatches == []
    commit = fake.commits[0]
    assert commit["head_sha"] == "head-sha"
    assert commit["tree_sha"] == "base-tree"
    assert commit["message"] == "chore(catalog): test (#289)"
    assert _catalog_path() in commit["artifacts"]

    second = await service.async_publish("dev")
    assert second["changed"] is False
    assert second["commit"] is None
    assert second["deployment"]["triggered"] is False
    assert len(fake.commits) == 1, "unveränderter Stand darf keinen zweiten Commit erzeugen"
    assert "Keine Änderungen" in second["summary"]


async def test_publish_prod_commits_to_dev_then_dispatches_deploy_prd(tmp_path: Path) -> None:
    store, _ = await _store_with_product(tmp_path)
    fake = FakeGitHub(
        PublishSettings(
            "Frickeldave/frickeldave.github.io", "dev", "main", "fd-deploy-prd.yml", "ghp-test"
        )
    )
    service = PublishService(store, client_factory=lambda settings: fake)

    result = await service.async_publish("prod")

    assert result["target"] == "prod"
    assert result["branch"] == "dev", "main wird nur über fd-deploy-prd aktualisiert"
    assert result["deployment"]["triggered"] is True
    assert result["deployment"]["mode"] == "workflow_dispatch"
    assert fake.dispatches == [
        {"workflow": "fd-deploy-prd.yml", "ref": "main", "inputs": {"dry_run": False}}
    ]
    assert len(fake.commits) == 1
    assert "fd-deploy-prd.yml wurde ausgelöst" in result["summary"]

    again = await service.async_publish("prod")
    assert again["changed"] is False
    assert len(fake.dispatches) == 2, "Produktion darf auch ohne Dateiänderung promoten"


async def test_publish_reports_removed_images(tmp_path: Path) -> None:
    store, _ = await _store_with_product(tmp_path)
    stale = "src/assets/handmade/LA/OTHER/OTHER-01.jpg"
    fake = FakeGitHub(
        PublishSettings(
            "Frickeldave/frickeldave.github.io", "dev", "main", "fd-deploy-prd.yml", "ghp-test"
        ),
        remote={stale: "deadbeef"},
    )
    service = PublishService(store, client_factory=lambda settings: fake)

    result = await service.async_publish("dev", dry_run=True)

    removed = [item for item in result["files"] if item["status"] == "removed"]
    assert [item["path"] for item in removed] == [stale]
    assert result["counts"]["removed"] == 1


async def test_publish_without_token_fails_with_german_message(tmp_path: Path) -> None:
    store, _ = await _store_with_product(tmp_path)
    store.update_settings({"publish": {"token": ""}})

    def _factory(settings: PublishSettings) -> FakeGitHub:  # pragma: no cover - darf nicht laufen
        raise AssertionError("ohne Token darf kein Client entstehen")

    service = PublishService(store, client_factory=_factory)
    with pytest.raises(PublishTokenMissing) as err:
        await service.async_publish("dev")
    assert "kein GitHub-Token" in err.value.message
    assert err.value.code == "publish_token_missing"


async def test_publish_requires_configuration(tmp_path: Path) -> None:
    store, _ = await _store_with_product(tmp_path)
    # Defekter Bestand (z. B. nach manuellem Eingriff in die Store-Datei).
    store.data["settings"]["publish"]["repository"] = ""
    service = PublishService(store, client_factory=lambda settings: FakeGitHub(settings))
    with pytest.raises(PublishNotConfigured):
        await service.async_publish("dev")


async def test_publish_rejects_unknown_target(tmp_path: Path) -> None:
    store, _ = await _store_with_product(tmp_path)
    service = PublishService(store, client_factory=lambda settings: FakeGitHub(settings))
    with pytest.raises(Exception) as err:
        await service.async_publish("staging")
    assert getattr(err.value, "code", "") == "validation_error"


async def test_github_token_is_never_returned_in_settings(tmp_path: Path) -> None:
    store, _ = await _store_with_product(tmp_path)
    payload = store.public_settings()
    assert payload["publish"]["token_set"] is True
    assert "token" not in payload["publish"]
    assert "ghp-test" not in json.dumps(payload)


def _request(hass: Any, user_id: str | None, body: dict[str, Any] | None = None) -> Any:
    return SimpleNamespace(
        app={"hass": hass},
        get=lambda key, default=None: (
            SimpleNamespace(id=user_id, name="Test") if key == "hass_user" and user_id else default
        ),
        json=lambda: _async_value(body),
        can_read_body=body is not None,
    )


async def _async_value(value: Any) -> Any:
    return value


async def test_publish_view_requires_app_admin_and_returns_plan(tmp_path: Path) -> None:
    class FakeConfig:
        def path(self, *parts: str) -> str:
            return str(tmp_path.joinpath(*parts))

    hass = SimpleNamespace(data={}, config=FakeConfig())
    store, _ = await _store_with_product(tmp_path)
    permissions = PermissionManager()
    permissions.load({"admin": ["app_admin"], "worker": ["product"]})
    fake = FakeGitHub(PublishSettings("repo/x", "dev", "main", "fd-deploy-prd.yml", "ghp-test"))
    runtime = FrickeldaveStoreRuntime(
        entry=None,  # type: ignore[arg-type]
        store=store,
        products=ProductService(store),
        permissions=permissions,
        ai=None,  # type: ignore[arg-type]
        export_path=Path("export"),
        publish=PublishService(store, client_factory=lambda settings: fake),
    )
    hass.data[DOMAIN] = runtime

    denied = await PublishView().post(_request(hass, "worker", {"target": "dev"}))
    assert denied.status == 403

    response = await PublishView().post(_request(hass, "admin", {"target": "dev", "dry_run": True}))
    assert response.status == 200
    body = json.loads(response.body.decode("utf-8"))
    assert body["dry_run"] is True
    assert body["counts"]["total"] == 2
    assert fake.commits == []

    invalid = await PublishView().post(_request(hass, "admin", {"target": "staging"}))
    assert invalid.status == 400


class FakeHTTPResponse:
    """aiohttp-Antwort mit echtem Body; ``content_length=None`` = chunked."""

    def __init__(
        self, status: int, payload: Any = None, *, content_length: int | None = None
    ) -> None:
        self.status = status
        self._payload = payload
        self.content_length = content_length

    async def json(self, content_type: str | None = None) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    async def read(self) -> bytes:
        if isinstance(self._payload, (bytes, bytearray)):
            return bytes(self._payload)
        if self._payload is None:
            return b""
        return json.dumps(self._payload).encode("utf-8")

    async def __aenter__(self) -> FakeHTTPResponse:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


class FakeHTTPSession:
    def __init__(self, responses: list[FakeHTTPResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def _next(self, method: str, url: str, **kwargs: Any) -> FakeHTTPResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self._responses.pop(0)

    def request(self, method: str, url: str, **kwargs: Any) -> FakeHTTPResponse:
        return self._next(method, url, **kwargs)

    def get(self, url: str, **kwargs: Any) -> FakeHTTPResponse:
        return self._next("GET", url, **kwargs)

    async def close(self) -> None:
        return None


async def test_github_client_commit_uses_base_tree_and_keeps_other_files() -> None:
    from custom_components.frickeldave_store.publish import GitHubClient

    settings = PublishSettings("owner/repo", "dev", "main", "fd-deploy-prd.yml", "ghp-secret")
    session = FakeHTTPSession(
        [
            FakeHTTPResponse(201, {"sha": "blob-new"}),
            FakeHTTPResponse(201, {"sha": "blob-new-2"}),
            FakeHTTPResponse(201, {"sha": "tree-new"}),
            FakeHTTPResponse(201, {"sha": "commit-new"}),
            FakeHTTPResponse(200, {"ref": "refs/heads/dev"}),
        ]
    )
    client = GitHubClient(settings, session=session)  # type: ignore[arg-type]
    changes = [
        FileChange("public/data/handmade.json", "changed", "catalog"),
        FileChange("src/assets/handmade/LA/LA0001/LA0001-01.jpg", "added", "image", "LA0001"),
        FileChange("src/assets/handmade/LA/LA0002/LA0002-01.jpg", "removed", "image", "LA0002"),
        FileChange("src/assets/handmade/LA/LA0003/LA0003-01.jpg", "unchanged", "image", "LA0003"),
    ]
    artifacts = {
        "public/data/handmade.json": b"{}",
        "src/assets/handmade/LA/LA0001/LA0001-01.jpg": b"bild",
    }

    commit = await client.async_commit(
        artifacts, changes, head_sha="head", tree_sha="base-tree", message="test"
    )

    assert commit == "commit-new"
    blobs = [call for call in session.calls if call["url"].endswith("/git/blobs")]
    assert len(blobs) == 2, "nur geänderte und neue Dateien werden hochgeladen"
    assert blobs[0]["json"]["encoding"] == "base64"

    tree_call = next(call for call in session.calls if call["url"].endswith("/git/trees"))
    assert tree_call["json"]["base_tree"] == "base-tree"
    entries = {entry["path"]: entry for entry in tree_call["json"]["tree"]}
    assert entries["src/assets/handmade/LA/LA0002/LA0002-01.jpg"]["sha"] is None
    assert "src/assets/handmade/LA/LA0003/LA0003-01.jpg" not in entries
    assert entries["public/data/handmade.json"]["sha"] == "blob-new"
    assert entries["src/assets/handmade/LA/LA0001/LA0001-01.jpg"]["sha"] == "blob-new-2"

    commit_call = next(call for call in session.calls if call["url"].endswith("/git/commits"))
    assert commit_call["json"] == {
        "message": "test",
        "tree": "tree-new",
        "parents": ["head"],
    }
    ref_call = session.calls[-1]
    assert ref_call["method"] == "PATCH"
    assert ref_call["url"].endswith("/git/refs/heads/dev")
    assert ref_call["json"] == {"sha": "commit-new", "force": False}
    assert session.calls[0]["headers"]["Authorization"] == "Bearer ghp-secret"


async def test_github_client_maps_unauthorized_and_missing_file() -> None:
    from custom_components.frickeldave_store.publish import GitHubClient, GitHubUnauthorized

    settings = PublishSettings("owner/repo", "dev", "main", "fd-deploy-prd.yml", "wrong")
    session = FakeHTTPSession(
        [
            FakeHTTPResponse(401, {"message": "Bad credentials"}),
            FakeHTTPResponse(404, b""),
        ]
    )
    client = GitHubClient(settings, session=session)  # type: ignore[arg-type]
    with pytest.raises(GitHubUnauthorized) as err:
        await client.async_read_tree()
    assert "abgelehnt" in err.value.message

    missing = await client.async_read_file("public/data/handmade.json")
    assert missing is None


async def test_github_client_reads_recursive_tree() -> None:
    from custom_components.frickeldave_store.publish import GitHubClient

    settings = PublishSettings("owner/repo", "dev", "main", "fd-deploy-prd.yml", "ghp-secret")
    session = FakeHTTPSession(
        [
            FakeHTTPResponse(200, {"object": {"sha": "head-1"}}),
            FakeHTTPResponse(200, {"tree": {"sha": "tree-1"}}),
            FakeHTTPResponse(
                200,
                {
                    "tree": [
                        {"path": "public/data/handmade.json", "type": "blob", "sha": "abc"},
                        {"path": "src/assets/handmade", "type": "tree", "sha": "dir"},
                    ],
                    "truncated": False,
                },
            ),
        ]
    )
    client = GitHubClient(settings, session=session)  # type: ignore[arg-type]
    tree = await client.async_read_tree()
    assert tree.head_sha == "head-1"
    assert tree.tree_sha == "tree-1"
    assert tree.blobs == {"public/data/handmade.json": "abc"}


def _publish_settings() -> PublishSettings:
    return PublishSettings("o/r", "dev", "main", "fd-deploy-prd.yml", "ghp-test")


async def _empty_store(tmp_path: Path) -> CatalogStore:
    store = CatalogStore(data_dir=tmp_path)
    await store.async_load()
    store.update_settings(
        {
            "publish": {
                "repository": "o/r",
                "dev_branch": "dev",
                "prod_branch": "main",
                "prod_workflow": "fd-deploy-prd.yml",
                "token": "ghp-test",
            }
        }
    )
    return store


async def test_empty_catalog_blocks_publish_with_german_message(tmp_path: Path) -> None:
    store = await _empty_store(tmp_path)
    fake = FakeGitHub(_publish_settings(), remote={_catalog_path(): "alter-stand"})
    service = PublishService(store, client_factory=lambda settings: fake)

    with pytest.raises(PublishEmptyCatalog) as err:
        await service.async_publish("dev")

    assert err.value.code == "publish_empty_catalog"
    assert "keine Produkte" in err.value.message
    assert "löschen" in err.value.message
    assert fake.commits == [], "leerer Store darf den Website-Katalog nicht löschen"


async def test_empty_catalog_dry_run_previews_with_warning(tmp_path: Path) -> None:
    store = await _empty_store(tmp_path)
    fake = FakeGitHub(_publish_settings(), remote={_catalog_path(): "alter-stand"})
    service = PublishService(store, client_factory=lambda settings: fake)

    result = await service.async_publish("dev", dry_run=True)

    assert result["dry_run"] is True
    assert result["commit"] is None
    assert result["products"]["total"] == 0
    assert result["warning"], "Dry-Run muss den leeren Katalog ankündigen"
    assert "keine Produkte" in result["warning"]
    assert "Hinweis:" in result["summary"]
    assert fake.commits == []


async def test_empty_catalog_allow_empty_publishes_the_lock_away(tmp_path: Path) -> None:
    store = await _empty_store(tmp_path)
    fake = FakeGitHub(
        _publish_settings(),
        remote={
            _catalog_path(): "alter-stand",
            "src/assets/handmade/LA/LA0001/LA0001-01.jpg": "altes-bild",
        },
    )
    service = PublishService(store, client_factory=lambda settings: fake)

    result = await service.async_publish("dev", allow_empty=True)

    assert result["changed"] is True
    assert len(fake.commits) == 1
    removed = {change.path for change in fake.commits[0]["changes"] if change.status == "removed"}
    assert "src/assets/handmade/LA/LA0001/LA0001-01.jpg" in removed
    assert result["warning"] and "allow_empty" in result["warning"]


async def test_non_empty_catalog_has_no_warning(tmp_path: Path) -> None:
    store, _ = await _store_with_product(tmp_path)
    service = PublishService(store, client_factory=lambda settings: FakeGitHub(_publish_settings()))

    result = await service.async_publish("dev", dry_run=True)

    assert result["warning"] is None


async def test_missing_image_file_is_a_structured_error(tmp_path: Path) -> None:
    store, article = await _store_with_product(tmp_path)
    image_name = store.get_product(article).images[0].name
    store.images.path_for(article, image_name).unlink()
    service = PublishService(store, client_factory=lambda settings: FakeGitHub(_publish_settings()))

    with pytest.raises(PublishArtifactsUnavailable) as err:
        await service.async_publish("dev", dry_run=True)

    assert err.value.code == "publish_artifacts_unavailable"
    assert article in err.value.message
    assert "nicht lesbar" in err.value.message or "fehlt" in err.value.message


async def test_unreadable_image_file_is_a_structured_error(tmp_path: Path) -> None:
    store, article = await _store_with_product(tmp_path)
    image_name = store.get_product(article).images[0].name
    path = store.images.path_for(article, image_name)
    path.chmod(0o000)
    service = PublishService(store, client_factory=lambda settings: FakeGitHub(_publish_settings()))

    try:
        with pytest.raises(PublishArtifactsUnavailable) as err:
            await service.async_publish("dev")
    finally:
        path.chmod(0o644)

    assert err.value.code == "publish_artifacts_unavailable"


async def test_commit_rejects_change_without_artifact() -> None:
    from custom_components.frickeldave_store.publish import GitHubClient

    client = GitHubClient(_publish_settings(), session=FakeHTTPSession([]))  # type: ignore[arg-type]
    changes = [FileChange("public/data/handmade.json", "changed", "catalog")]

    with pytest.raises(PublishArtifactsUnavailable) as err:
        await client.async_commit({}, changes, head_sha="head", tree_sha="base", message="test")

    assert err.value.code == "publish_artifacts_unavailable"


async def test_github_html_response_maps_to_github_unavailable() -> None:
    from custom_components.frickeldave_store.publish import GitHubClient

    html = b"<html><body>502 Bad Gateway</body></html>"
    session = FakeHTTPSession([FakeHTTPResponse(502, html)])
    client = GitHubClient(_publish_settings(), session=session)  # type: ignore[arg-type]

    with pytest.raises(GitHubError) as err:
        await client.async_read_tree()

    assert err.value.code == "github_unavailable"
    assert "nicht erreichbar" in err.value.message

    # 200 mit HTML (Proxy/Captive-Portal) ist ebenfalls keine gültige Antwort.
    session = FakeHTTPSession([FakeHTTPResponse(200, html)])
    client = GitHubClient(_publish_settings(), session=session)  # type: ignore[arg-type]
    with pytest.raises(GitHubError) as err:
        await client.async_read_tree()
    assert err.value.code == "github_unavailable"
    assert "Störungsseite" in err.value.message


async def test_github_empty_response_is_structured_not_attributeerror() -> None:
    from custom_components.frickeldave_store.publish import GitHubClient

    session = FakeHTTPSession([FakeHTTPResponse(200, None)])
    client = GitHubClient(_publish_settings(), session=session)  # type: ignore[arg-type]

    with pytest.raises(GitHubError) as err:
        await client.async_read_tree()

    assert err.value.code == "github_failed"
    assert "erwarteten Format" in err.value.message


async def test_github_chunked_json_without_content_length_is_read() -> None:
    """Regression #289: ``content_length`` ist bei chunked Antworten ``None``."""

    from custom_components.frickeldave_store.publish import GitHubClient

    response = FakeHTTPResponse(200, {"object": {"sha": "head-1"}})
    assert response.content_length is None, "Fixture muss chunked abbilden"
    session = FakeHTTPSession([response])
    client = GitHubClient(_publish_settings(), session=session)  # type: ignore[arg-type]

    body = await client._async_json("GET", "https://api.github.com/repos/o/r/git/ref/heads/dev")

    assert body == {"object": {"sha": "head-1"}}


async def test_dry_run_reads_chunked_github_responses_end_to_end(tmp_path: Path) -> None:
    """Der Lesepfad (Branch → Commit → Baum → Katalog) mit chunked Antworten."""

    from custom_components.frickeldave_store.publish import GitHubClient

    store, _ = await _store_with_product(tmp_path)
    catalog = json.dumps({"products": []}).encode("utf-8")
    session = FakeHTTPSession(
        [
            FakeHTTPResponse(200, {"object": {"sha": "head-1"}}),
            FakeHTTPResponse(200, {"tree": {"sha": "tree-1"}}),
            FakeHTTPResponse(
                200,
                {
                    "tree": [
                        {"path": _catalog_path(), "type": "blob", "sha": "anderer-stand"},
                    ],
                    "truncated": False,
                },
            ),
            FakeHTTPResponse(200, catalog),
        ]
    )
    service = PublishService(
        store,
        client_factory=lambda settings: GitHubClient(settings, session=session),  # type: ignore[arg-type]
    )

    result = await service.async_publish("dev", dry_run=True)

    assert result["dry_run"] is True
    assert result["commit"] is None
    assert result["counts"]["total"] == 2
    assert result["products"]["added"] == 1


async def test_catalog_with_only_archived_products_is_blocked(tmp_path: Path) -> None:
    store, article = await _store_with_product(tmp_path)
    await ProductService(store).async_archive(article)
    fake = FakeGitHub(_publish_settings(), remote={_catalog_path(): "alter-stand"})
    service = PublishService(store, client_factory=lambda settings: fake)

    with pytest.raises(PublishEmptyCatalog) as err:
        await service.async_publish("dev")

    assert err.value.code == "publish_empty_catalog"
    assert fake.commits == []


async def test_malformed_publish_settings_are_reported_not_a_500(tmp_path: Path) -> None:
    store, _ = await _store_with_product(tmp_path)
    store.data["settings"]["publish"] = "kaputt"
    service = PublishService(store, client_factory=lambda settings: FakeGitHub(settings))

    with pytest.raises(PublishNotConfigured) as err:
        await service.async_publish("dev")

    assert err.value.code == "publish_not_configured"
