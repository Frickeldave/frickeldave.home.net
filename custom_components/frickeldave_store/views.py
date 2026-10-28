"""HTTP-API der Integration (JSON, Default Deny je Anfrage).

Fehlerformat: ``{"error": {"code": ..., "message": ...}}``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from . import (
    FrickeldaveStoreRuntime,
    _async_apply_entry_config,
    async_set_data_path,
    runtime_for,
)
from .ai import AIError
from .const import (
    API_PREFIX,
    DEFAULT_CATALOG_URL,
    DEFAULT_IMAGE_BASE_URL,
    DOMAIN,
)
from .export import async_export_catalog
from .importer import async_import_catalog
from .models import (
    ConflictError,
    NotFoundError,
    PermissionDenied,
    StoreError,
    ValidationError,
)
from .permissions import (
    ALL_ROLES,
    ROLE_APP_ADMIN,
    ROLE_LABELS,
    ROLE_PRODUCT,
    format_user_grants,
    normalise_roles,
)
from .products import ProductFilter
from .storage import data_path_from_entry, storage_payload

_LOGGER = logging.getLogger(__name__)

STATUS_BY_CODE: dict[str, int] = {
    "validation_error": 400,
    "category_immutable": 400,
    "archive_endpoint_required": 400,
    "unsupported_media_type": 415,
    "file_too_large": 413,
    "not_found": 404,
    "conflict": 409,
    "image_limit": 409,
    "forbidden": 403,
    "ai_not_configured": 400,
    "ai_rate_limited": 429,
    "ai_unavailable": 502,
    "ai_unauthorized": 502,
    "ai_request_failed": 502,
    "ai_invalid_response": 502,
    "schema_too_new": 503,
    "not_loaded": 503,
    "catalog_unavailable": 502,
    "catalog_invalid": 422,
}


class NotLoadedError(StoreError):
    code = "not_loaded"


def _error_response(err: StoreError) -> web.Response:
    status = STATUS_BY_CODE.get(err.code, 400)
    return web.json_response({"error": err.as_dict}, status=status)


def _hass(request: web.Request) -> HomeAssistant:
    hass: HomeAssistant = request.app["hass"]
    return hass


def _runtime(request: web.Request) -> FrickeldaveStoreRuntime:
    runtime = runtime_for(_hass(request))
    if runtime is None:
        raise NotLoadedError("Die Integration ist nicht eingerichtet.")
    return runtime


def _user(request: web.Request) -> Any:
    user = request.get("hass_user")
    if user is None:
        raise PermissionDenied("Nicht authentifiziert.")
    return user


def _authorize(request: web.Request, role: str) -> tuple[FrickeldaveStoreRuntime, Any]:
    runtime = _runtime(request)
    user = _user(request)
    runtime.permissions.require(str(user.id), role)
    return runtime, user


async def _json_body(request: web.Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except ValueError as err:
        raise ValidationError("Ungültiges JSON im Anfragekörper.") from err
    if not isinstance(payload, dict):
        raise ValidationError("Anfragekörper muss ein JSON-Objekt sein.")
    return payload


async def _optional_json_body(request: web.Request) -> dict[str, Any]:
    """Körper ist optional: ohne Inhalt gilt ``{}`` (alle Felder haben Standardwerte)."""

    if not request.can_read_body:
        return {}
    return await _json_body(request)


def _optional_url(payload: dict[str, Any], key: str) -> str | None:
    if key not in payload:
        return None
    value = payload[key]
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{key} muss eine absolute https-URL sein.")
    return value.strip()


async def _async_auth_users(hass: HomeAssistant) -> list[Any]:
    """Alle HA-Benutzer; ohne Auth-Backend eine leere Liste (keine Namen)."""

    auth = getattr(hass, "auth", None)
    if auth is None:
        return []
    try:
        return list(await auth.async_get_users())
    except Exception:  # pragma: no cover - Auth-Backend nicht verfügbar
        _LOGGER.debug("HA-Benutzer konnten nicht gelesen werden", exc_info=True)
        return []


async def _async_auth_user(hass: HomeAssistant, user_id: str) -> Any | None:
    auth = getattr(hass, "auth", None)
    if auth is None:
        return None
    try:
        return await auth.async_get_user(user_id)
    except Exception:  # pragma: no cover - Auth-Backend nicht verfügbar
        return None


def _user_name(user: Any | None) -> str:
    if user is None:
        return ""
    return str(getattr(user, "name", "") or "")


async def _async_users_payload(
    hass: HomeAssistant, runtime: FrickeldaveStoreRuntime
) -> dict[str, Any]:
    """Antwort für ``GET /users`` und nach jeder Änderung.

    Namen kommen ausschließlich aus dem HA-Auth-Backend. Unbekannte Freigaben
    behalten ``name: ""`` und bleiben bearbeit- sowie entfernbar.
    """

    auth_users = await _async_auth_users(hass)
    by_id = {str(getattr(user, "id", "")): user for user in auth_users}
    grants = runtime.permissions.grants()
    users = [
        {
            "user_id": user_id,
            "name": _user_name(by_id.get(user_id)),
            "roles": list(grant.roles),
            "role_labels": [ROLE_LABELS[role] for role in grant.roles],
        }
        for user_id, grant in sorted(grants.items())
    ]
    available = sorted(
        (
            {"user_id": user_id, "name": _user_name(user)}
            for user_id, user in by_id.items()
            if user_id and user_id not in grants and bool(getattr(user, "is_active", True))
        ),
        key=lambda item: (item["name"].casefold(), item["user_id"]),
    )
    return {
        "users": users,
        "available_users": available,
        "roles": [{"id": role, "label": ROLE_LABELS[role]} for role in ALL_ROLES],
    }


def _grant_map(runtime: FrickeldaveStoreRuntime) -> dict[str, list[str]]:
    return {user_id: list(grant.roles) for user_id, grant in runtime.permissions.grants().items()}


def _ensure_admin_remains(
    grants: dict[str, list[str]], user_id: str, new_roles: list[str] | None
) -> None:
    """Den letzten ``app_admin`` schützen (kein Aussperren der Verwaltung)."""

    current = grants.get(user_id, [])
    if ROLE_APP_ADMIN not in current:
        return
    if new_roles is not None and ROLE_APP_ADMIN in new_roles:
        return
    if sum(1 for roles in grants.values() if ROLE_APP_ADMIN in roles) <= 1:
        raise ConflictError(
            "Der letzte Benutzer mit der Rolle 'Anwendungsverwaltung' kann nicht "
            "entfernt oder herabgestuft werden."
        )


async def _async_persist_grants(
    hass: HomeAssistant,
    runtime: FrickeldaveStoreRuntime,
    grants: dict[str, list[str]],
) -> None:
    """Freigaben in die Config-Entry-Options schreiben und sofort anwenden."""

    entry = runtime.entry
    if entry is None:  # pragma: no cover - Runtime ohne Entry nur in Tests
        raise NotLoadedError("Die Integration ist nicht eingerichtet.")
    options = dict(getattr(entry, "options", None) or {})
    options["users"] = format_user_grants(grants)
    hass.config_entries.async_update_entry(entry, options=options)
    await _async_apply_entry_config(hass, entry, runtime)


class MeView(HomeAssistantView):
    """Eigene Identität und effektive Rechte (keine geschützten Daten)."""

    name = "api:frickeldave_store:me"
    url = f"{API_PREFIX}/me"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        try:
            runtime = _runtime(request)
            user = _user(request)
            return web.json_response(runtime.permissions.snapshot(str(user.id)))
        except StoreError as err:
            return _error_response(err)


class ProductsView(HomeAssistantView):
    name = "api:frickeldave_store:products"
    url = f"{API_PREFIX}/products"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        try:
            runtime, _ = _authorize(request, ROLE_PRODUCT)
            filters = ProductFilter.from_query(dict(request.query))
            products, total = await runtime.products.async_list(filters)
            return web.json_response(
                {"products": [item.to_dict() for item in products], "total": total}
            )
        except StoreError as err:
            return _error_response(err)

    async def post(self, request: web.Request) -> web.Response:
        try:
            runtime, user = _authorize(request, ROLE_PRODUCT)
            payload = await _json_body(request)
            product = await runtime.products.async_create(payload, actor=str(user.id))
            return web.json_response(product.to_dict(), status=201)
        except StoreError as err:
            return _error_response(err)


class ProductView(HomeAssistantView):
    name = "api:frickeldave_store:product"
    url = f"{API_PREFIX}/products/{{article_number}}"
    requires_auth = True

    async def get(self, request: web.Request, article_number: str) -> web.Response:
        try:
            runtime, _ = _authorize(request, ROLE_PRODUCT)
            product = await runtime.products.async_get(article_number)
            return web.json_response(product.to_dict())
        except StoreError as err:
            return _error_response(err)

    async def patch(self, request: web.Request, article_number: str) -> web.Response:
        try:
            runtime, _ = _authorize(request, ROLE_PRODUCT)
            payload = await _json_body(request)
            product = await runtime.products.async_update(article_number, payload)
            return web.json_response(product.to_dict())
        except StoreError as err:
            return _error_response(err)


class ArchiveView(HomeAssistantView):
    name = "api:frickeldave_store:archive"
    url = f"{API_PREFIX}/products/{{article_number}}/archive"
    requires_auth = True

    async def post(self, request: web.Request, article_number: str) -> web.Response:
        try:
            runtime, _ = _authorize(request, ROLE_PRODUCT)
            product = await runtime.products.async_archive(article_number)
            return web.json_response({"product": product.to_dict()})
        except StoreError as err:
            return _error_response(err)


class ImagesView(HomeAssistantView):
    name = "api:frickeldave_store:images"
    url = f"{API_PREFIX}/products/{{article_number}}/images"
    requires_auth = True

    async def get(self, request: web.Request, article_number: str) -> web.Response:
        try:
            runtime, _ = _authorize(request, ROLE_PRODUCT)
            images = await runtime.products.async_list_images(article_number)
            return web.json_response({"images": images})
        except StoreError as err:
            return _error_response(err)

    async def post(self, request: web.Request, article_number: str) -> web.Response:
        try:
            runtime, _ = _authorize(request, ROLE_PRODUCT)
            post = await request.post()
            upload = post.get("file") or post.get("image")
            upload_file = getattr(upload, "file", None)
            if upload is None or isinstance(upload, str) or upload_file is None:
                raise ValidationError(
                    "Multipart-Feld 'file' mit Bilddatei ist erforderlich.",
                    code="validation_error",
                )
            data = upload_file.read()
            result = await runtime.products.async_add_image(
                article_number,
                filename=str(getattr(upload, "filename", "") or ""),
                content_type=str(getattr(upload, "content_type", "") or ""),
                data=data,
            )
            return web.json_response(result, status=201)
        except StoreError as err:
            return _error_response(err)


class ImageFileView(HomeAssistantView):
    name = "api:frickeldave_store:image_file"
    url = f"{API_PREFIX}/products/{{article_number}}/images/{{name}}"
    requires_auth = True

    async def get(self, request: web.Request, article_number: str, name: str) -> web.StreamResponse:
        try:
            runtime, _ = _authorize(request, ROLE_PRODUCT)
            path, content_type = await runtime.products.async_read_image(article_number, name)
            if not path.is_file():
                raise NotFoundError(f"Bild {name} nicht gefunden.")
            body = await _hass(request).async_add_executor_job(path.read_bytes)
            return web.Response(body=body, content_type=content_type)
        except StoreError as err:
            return _error_response(err)


class ImageOrderView(HomeAssistantView):
    name = "api:frickeldave_store:image_order"
    url = f"{API_PREFIX}/products/{{article_number}}/images/order"
    requires_auth = True

    async def post(self, request: web.Request, article_number: str) -> web.Response:
        try:
            runtime, _ = _authorize(request, ROLE_PRODUCT)
            payload = await _json_body(request)
            order = payload.get("order")
            if not isinstance(order, list) or not all(isinstance(item, str) for item in order):
                raise ValidationError("order muss eine Liste von Bildnamen sein.")
            images = await runtime.products.async_set_image_order(article_number, order)
            return web.json_response(images)
        except StoreError as err:
            return _error_response(err)


class ImageMainView(HomeAssistantView):
    name = "api:frickeldave_store:image_main"
    url = f"{API_PREFIX}/products/{{article_number}}/images/main"
    requires_auth = True

    async def post(self, request: web.Request, article_number: str) -> web.Response:
        try:
            runtime, _ = _authorize(request, ROLE_PRODUCT)
            payload = await _json_body(request)
            name = payload.get("name")
            if not isinstance(name, str) or not name:
                raise ValidationError("name ist ein Pflichtfeld.")
            images = await runtime.products.async_set_main_image(article_number, name)
            return web.json_response(images)
        except StoreError as err:
            return _error_response(err)


class ImageDeleteView(HomeAssistantView):
    name = "api:frickeldave_store:image_delete"
    url = f"{API_PREFIX}/products/{{article_number}}/images/{{name}}"
    requires_auth = True

    async def delete(self, request: web.Request, article_number: str, name: str) -> web.Response:
        try:
            runtime, _ = _authorize(request, ROLE_PRODUCT)
            images = await runtime.products.async_delete_image(article_number, name)
            return web.json_response(images)
        except StoreError as err:
            return _error_response(err)


class TagsView(HomeAssistantView):
    name = "api:frickeldave_store:tags"
    url = f"{API_PREFIX}/tags"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        try:
            runtime, _ = _authorize(request, ROLE_PRODUCT)
            return web.json_response({"tags": await runtime.products.async_list_tags()})
        except StoreError as err:
            return _error_response(err)

    async def post(self, request: web.Request) -> web.Response:
        try:
            runtime, _ = _authorize(request, ROLE_PRODUCT)
            payload = await _json_body(request)
            tag = await runtime.products.async_create_tag(payload)
            tags = await runtime.products.async_list_tags()
            return web.json_response({"tags": tags, "name": tag}, status=201)
        except StoreError as err:
            return _error_response(err)


class TagView(HomeAssistantView):
    name = "api:frickeldave_store:tag"
    url = f"{API_PREFIX}/tags/{{name}}"
    requires_auth = True

    async def delete(self, request: web.Request, name: str) -> web.Response:
        try:
            runtime, _ = _authorize(request, ROLE_PRODUCT)
            await runtime.products.async_delete_tag(name)
            return web.json_response({"tags": await runtime.products.async_list_tags()})
        except StoreError as err:
            return _error_response(err)


class StockView(HomeAssistantView):
    name = "api:frickeldave_store:stock"
    url = f"{API_PREFIX}/products/{{article_number}}/stock"
    requires_auth = True

    async def get(self, request: web.Request, article_number: str) -> web.Response:
        try:
            runtime, _ = _authorize(request, ROLE_PRODUCT)
            bookings = await runtime.products.async_list_bookings(article_number)
            return web.json_response({"bookings": bookings})
        except StoreError as err:
            return _error_response(err)

    async def post(self, request: web.Request, article_number: str) -> web.Response:
        try:
            runtime, user = _authorize(request, ROLE_PRODUCT)
            payload = await _json_body(request)
            result = await runtime.products.async_book_stock(
                article_number, payload, ha_user_id=str(user.id)
            )
            return web.json_response(result, status=201)
        except StoreError as err:
            return _error_response(err)


class DescriptionSuggestView(HomeAssistantView):
    name = "api:frickeldave_store:description_suggest"
    url = f"{API_PREFIX}/products/{{article_number}}/description/suggest"
    requires_auth = True

    async def post(self, request: web.Request, article_number: str) -> web.Response:
        try:
            runtime, _ = _authorize(request, ROLE_PRODUCT)
            suggestion = await runtime.ai.async_suggest_description(article_number)
            return web.json_response({"suggestion": suggestion})
        except AIError as err:
            return _error_response(err)
        except StoreError as err:
            return _error_response(err)


class UsersView(HomeAssistantView):
    """Benutzerfreigaben lesen (nur Anwendungsverwaltung)."""

    name = "api:frickeldave_store:users"
    url = f"{API_PREFIX}/users"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        try:
            runtime, _ = _authorize(request, ROLE_APP_ADMIN)
            return web.json_response(await _async_users_payload(_hass(request), runtime))
        except StoreError as err:
            return _error_response(err)


class UserView(HomeAssistantView):
    """Freigabe eines Benutzers anlegen/ändern oder entfernen."""

    name = "api:frickeldave_store:user"
    url = f"{API_PREFIX}/users/{{user_id}}"
    requires_auth = True

    async def put(self, request: web.Request, user_id: str) -> web.Response:
        try:
            runtime, _ = _authorize(request, ROLE_APP_ADMIN)
            hass = _hass(request)
            payload = await _json_body(request)
            unknown = set(payload) - {"roles"}
            if unknown:
                raise ValidationError(f"Unbekannte Felder: {', '.join(sorted(unknown))}")
            raw_roles = payload.get("roles")
            if not isinstance(raw_roles, list) or any(
                not isinstance(role, str) for role in raw_roles
            ):
                raise ValidationError("roles muss eine Liste von Rollen sein.")
            roles = normalise_roles(raw_roles)
            if not roles:
                raise ValidationError("Mindestens eine Rolle ist erforderlich.")
            target = user_id.strip()
            if not target:
                raise ValidationError("user_id darf nicht leer sein.")

            grants = _grant_map(runtime)
            if target not in grants and await _async_auth_user(hass, target) is None:
                raise NotFoundError("Unbekannter Home-Assistant-Benutzer.")
            _ensure_admin_remains(grants, target, roles)
            grants[target] = roles
            await _async_persist_grants(hass, runtime, grants)
            return web.json_response(await _async_users_payload(hass, runtime))
        except StoreError as err:
            return _error_response(err)

    async def delete(self, request: web.Request, user_id: str) -> web.Response:
        try:
            runtime, _ = _authorize(request, ROLE_APP_ADMIN)
            hass = _hass(request)
            target = user_id.strip()
            grants = _grant_map(runtime)
            if target not in grants:
                raise NotFoundError("Dieser Benutzer hat keine Freigabe.")
            _ensure_admin_remains(grants, target, None)
            del grants[target]
            await _async_persist_grants(hass, runtime, grants)
            return web.json_response(await _async_users_payload(hass, runtime))
        except StoreError as err:
            return _error_response(err)


def _settings_payload(hass: HomeAssistant, runtime: FrickeldaveStoreRuntime) -> dict[str, Any]:
    """Gemeinsames Antwortformat für GET und PATCH ``/settings``."""

    payload = runtime.store.public_settings()
    payload["users"] = [
        {"user_id": grant.user_id, "name": grant.name, "roles": list(grant.roles)}
        for grant in runtime.permissions.grants().values()
    ]
    payload["export_path"] = str(runtime.export_path)
    payload["storage"] = storage_payload(hass, data_path_from_entry(runtime.entry))
    return payload


class SettingsView(HomeAssistantView):
    name = "api:frickeldave_store:settings"
    url = f"{API_PREFIX}/settings"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        try:
            runtime, _ = _authorize(request, ROLE_APP_ADMIN)
            return web.json_response(_settings_payload(_hass(request), runtime))
        except StoreError as err:
            return _error_response(err)

    async def patch(self, request: web.Request) -> web.Response:
        try:
            hass = _hass(request)
            runtime, _ = _authorize(request, ROLE_APP_ADMIN)
            payload = await _json_body(request)
            unknown = set(payload) - {"ai", "storage"}
            if unknown:
                raise ValidationError(
                    f"Unbekannte Felder: {', '.join(sorted(unknown))}. "
                    "Benutzerfreigaben werden über den Options-Flow verwaltet.",
                )

            changes = dict(payload)
            storage_changes = changes.pop("storage", None)
            new_data_path: str | None = None
            if storage_changes is not None:
                if not isinstance(storage_changes, dict):
                    raise ValidationError("storage muss ein Objekt sein.")
                unknown_storage = set(storage_changes) - {"data_path"}
                if unknown_storage:
                    raise ValidationError(
                        f"Unbekannte storage-Felder: {', '.join(sorted(unknown_storage))}"
                    )
                if "data_path" not in storage_changes:
                    raise ValidationError("storage.data_path fehlt.")
                new_data_path = str(storage_changes["data_path"])

            ai_changes = changes.get("ai")
            if isinstance(ai_changes, dict) and not ai_changes.get("api_key"):
                ai_changes.pop("api_key", None)
            runtime.store.update_settings(changes)
            await runtime.store.async_save()

            reload_required = False
            if new_data_path is not None:
                entry = runtime.entry
                if entry is None:  # pragma: no cover - Runtime ohne Entry nur in Tests
                    raise NotLoadedError("Die Integration ist nicht eingerichtet.")
                reload_required = await async_set_data_path(hass, entry, new_data_path)

            response = _settings_payload(hass, runtime)
            response["reload_required"] = reload_required
            return web.json_response(response)
        except StoreError as err:
            return _error_response(err)


class ExportView(HomeAssistantView):
    name = "api:frickeldave_store:export"
    url = f"{API_PREFIX}/export"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        try:
            runtime, _ = _authorize(request, ROLE_APP_ADMIN)
            target = Path(_hass(request).config.path(str(runtime.export_path)))
            result = await async_export_catalog(
                runtime.store.products(), runtime.store.images, target
            )
            return web.json_response(result)
        except StoreError as err:
            return _error_response(err)


class CatalogImportView(HomeAssistantView):
    """Bestehenden Website-Katalog importieren (idempotent, ohne Überschreiben)."""

    name = "api:frickeldave_store:import_catalog"
    url = f"{API_PREFIX}/import/catalog"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        try:
            payload = await _optional_json_body(request)
            unknown = set(payload) - {"source_url", "image_base_url", "import_images"}
            if unknown:
                raise ValidationError(f"Unbekannte Felder: {', '.join(sorted(unknown))}")
            # Eigene Quellen können interne Adressen ansprechen: nur App-Admins.
            custom = "source_url" in payload or "image_base_url" in payload
            runtime, _ = _authorize(request, ROLE_APP_ADMIN if custom else ROLE_PRODUCT)
            source_url = _optional_url(payload, "source_url")
            image_base_url = _optional_url(payload, "image_base_url")
            import_images = payload.get("import_images", True)
            if not isinstance(import_images, bool):
                raise ValidationError("import_images muss ein Wahrheitswert sein.")
            result = await async_import_catalog(
                runtime.store,
                source_url=source_url or DEFAULT_CATALOG_URL,
                image_base_url=image_base_url or DEFAULT_IMAGE_BASE_URL,
                import_images=import_images,
            )
            return web.json_response(result.to_dict())
        except StoreError as err:
            return _error_response(err)


VIEWS: tuple[type[HomeAssistantView], ...] = (
    MeView,
    ProductsView,
    ProductView,
    ArchiveView,
    ImagesView,
    ImageFileView,
    ImageOrderView,
    ImageMainView,
    ImageDeleteView,
    TagsView,
    TagView,
    StockView,
    DescriptionSuggestView,
    UsersView,
    UserView,
    SettingsView,
    ExportView,
    CatalogImportView,
)


async def async_register_views(hass: HomeAssistant) -> None:
    """Alle API-Routen einmalig registrieren."""

    key = f"{DOMAIN}_views_registered"
    if hass.data.get(key):
        return
    for view_class in VIEWS:
        hass.http.register_view(view_class())
    hass.data[key] = True
    _LOGGER.debug("Frickeldave Store: %s HTTP-Routen registriert", len(VIEWS))
