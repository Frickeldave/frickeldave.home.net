"""Tests der HTTP-API mit echten View-Handlern und Default Deny."""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frickeldave_store import FrickeldaveStoreRuntime
from custom_components.frickeldave_store.ai import AIError, AIService
from custom_components.frickeldave_store.const import DOMAIN
from custom_components.frickeldave_store.permissions import PermissionManager
from custom_components.frickeldave_store.products import ProductService
from custom_components.frickeldave_store.store import CatalogStore
from custom_components.frickeldave_store.views import (
    ArchiveView,
    DescriptionSuggestView,
    ExportView,
    ImagesView,
    MeView,
    ProductsView,
    ProductView,
    SettingsView,
)


class FakeConfig:
    def __init__(self, root: Path) -> None:
        self._root = root

    def path(self, *parts: str) -> str:
        return str(self._root.joinpath(*parts))

    def config_dir(self) -> str:
        return str(self._root)


class FakeHass:
    def __init__(self, root: Path) -> None:
        self.data: dict[str, Any] = {}
        self.config = FakeConfig(root)

    async def async_add_executor_job(self, func: Any, *args: Any) -> Any:
        return func(*args)


class FakeRequest:
    def __init__(
        self,
        hass: FakeHass,
        user_id: str | None,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        upload: Any | None = None,
    ) -> None:
        self.app = {"hass": hass}
        self._user = SimpleNamespace(id=user_id, name="Test") if user_id else None
        self.query = query or {}
        self._body = body
        self._upload = upload

    def get(self, key: str, default: Any = None) -> Any:
        if key == "hass_user":
            return self._user
        return default

    async def json(self) -> dict[str, Any]:
        if self._body is None:
            raise ValueError("kein JSON")
        return self._body

    async def post(self) -> Any:
        return {"file": self._upload} if self._upload is not None else {}


class FailingProvider:
    def __init__(self, error: AIError) -> None:
        self._error = error

    async def async_suggest(self, product: Any, images: Any) -> str:
        raise self._error

    async def async_close(self) -> None:
        return None


async def _setup(
    tmp_path: Path, users: dict[str, list[str]], provider: Any | None = None
) -> tuple[FakeHass, FrickeldaveStoreRuntime]:
    hass = FakeHass(tmp_path)
    store = CatalogStore(data_dir=tmp_path)
    await store.async_load()
    permissions = PermissionManager()
    permissions.load(users)
    runtime = FrickeldaveStoreRuntime(
        entry=None,  # type: ignore[arg-type]
        store=store,
        products=ProductService(store),
        permissions=permissions,
        ai=AIService(store, provider_factory=(lambda: provider) if provider else None),
        export_path=Path("export"),
    )
    hass.data[DOMAIN] = runtime
    return hass, runtime


def _payload(response: Any) -> Any:
    return json.loads(response.body.decode("utf-8"))


async def test_me_reports_roles_and_can_flags(tmp_path: Path) -> None:
    hass, _ = await _setup(tmp_path, {"user-1": ["product"], "admin": ["app_admin"]})
    response = await MeView().get(FakeRequest(hass, "user-1"))
    assert response.status == 200
    body = _payload(response)
    assert body["roles"] == ["product"]
    assert body["can"] == {"product": True, "finance": False, "app_admin": False}

    unknown = await MeView().get(FakeRequest(hass, "ghost"))
    assert _payload(unknown)["can"] == {"product": False, "finance": False, "app_admin": False}


async def test_default_deny_returns_403_without_protected_data(tmp_path: Path) -> None:
    hass, _ = await _setup(tmp_path, {"user-1": ["product"]})
    response = await ProductsView().get(FakeRequest(hass, "ghost"))
    assert response.status == 403
    body = _payload(response)
    assert body == {
        "error": {"code": "forbidden", "message": "Keine Berechtigung für diesen Bereich."}
    }
    assert b"products" not in response.body


async def test_revocation_applies_to_next_request(tmp_path: Path) -> None:
    hass, runtime = await _setup(tmp_path, {"user-1": ["product"]})
    assert (await ProductsView().get(FakeRequest(hass, "user-1"))).status == 200
    runtime.permissions.load({})
    assert (await ProductsView().get(FakeRequest(hass, "user-1"))).status == 403


async def test_product_flow_create_read_archive(tmp_path: Path) -> None:
    hass, _ = await _setup(tmp_path, {"user-1": ["product"]})
    create = await ProductsView().post(
        FakeRequest(hass, "user-1", body={"name": "Schild", "category": "LA", "price": 35})
    )
    assert create.status == 201
    product = _payload(create)
    assert product["articleNumber"].startswith("LA")

    read = await ProductView().get(FakeRequest(hass, "user-1"), product["articleNumber"])
    assert read.status == 200

    patch = await ProductView().patch(
        FakeRequest(hass, "user-1", body={"name": "Neues Schild"}),
        product["articleNumber"],
    )
    assert _payload(patch)["name"] == "Neues Schild"

    archive = await ArchiveView().post(FakeRequest(hass, "user-1"), product["articleNumber"])
    assert _payload(archive)["product"]["archived"] is True

    listing = await ProductsView().get(FakeRequest(hass, "user-1", query={"archived": "true"}))
    assert _payload(listing)["total"] == 1


async def test_invalid_filter_returns_validation_error(tmp_path: Path) -> None:
    hass, _ = await _setup(tmp_path, {"user-1": ["product"]})
    response = await ProductsView().get(FakeRequest(hass, "user-1", query={"category": "XX"}))
    assert response.status == 400
    assert _payload(response)["error"]["code"] == "validation_error"


async def test_eleventh_image_upload_rejected_via_api(tmp_path: Path) -> None:
    hass, _ = await _setup(tmp_path, {"user-1": ["product"]})
    create = await ProductsView().post(
        FakeRequest(hass, "user-1", body={"name": "Schild", "category": "LA"})
    )
    article = _payload(create)["articleNumber"]
    upload = SimpleNamespace(
        content_type="image/jpeg",
        filename="bild.jpg",
        file=io.BytesIO(b"\xff\xd8bild"),
    )
    for _ in range(10):
        response = await ImagesView().post(FakeRequest(hass, "user-1", upload=upload), article)
        assert response.status == 201
        upload = SimpleNamespace(
            content_type="image/jpeg", filename="bild.jpg", file=io.BytesIO(b"\xff\xd8bild")
        )
    rejected = await ImagesView().post(
        FakeRequest(
            hass,
            "user-1",
            upload=SimpleNamespace(
                content_type="image/jpeg", filename="elf.jpg", file=io.BytesIO(b"elf")
            ),
        ),
        article,
    )
    assert rejected.status == 409
    assert _payload(rejected)["error"]["code"] == "image_limit"
    listing = await ImagesView().get(FakeRequest(hass, "user-1"), article)
    assert len(_payload(listing)["images"]) == 10


async def test_settings_requires_admin_and_hides_api_key(tmp_path: Path) -> None:
    hass, runtime = await _setup(tmp_path, {"user-1": ["product"], "admin": ["app_admin"]})
    runtime.store.update_settings({"ai": {"api_key": "super-secret"}})

    denied = await SettingsView().get(FakeRequest(hass, "user-1"))
    assert denied.status == 403
    assert b"super-secret" not in denied.body

    allowed = await SettingsView().get(FakeRequest(hass, "admin"))
    assert allowed.status == 200
    assert b"super-secret" not in allowed.body
    body = _payload(allowed)
    assert body["ai"]["api_key_set"] is True
    assert "api_key" not in body["ai"]
    assert {item["user_id"] for item in body["users"]} == {"user-1", "admin"}


async def test_settings_patch_does_not_echo_secret(tmp_path: Path) -> None:
    hass, _ = await _setup(tmp_path, {"admin": ["app_admin"]})
    response = await SettingsView().patch(
        FakeRequest(
            hass, "admin", body={"ai": {"api_key": "neu-geheim", "model": "deepseek-flash"}}
        )
    )
    assert response.status == 200
    assert b"neu-geheim" not in response.body
    assert _payload(response)["ai"]["api_key_set"] is True


async def test_ai_rate_limit_returns_readable_error_without_mutation(tmp_path: Path) -> None:
    provider = FailingProvider(AIError("Nutzungslimit erreicht.", code="ai_rate_limited"))
    hass, runtime = await _setup(tmp_path, {"user-1": ["product"]}, provider=provider)
    create = await ProductsView().post(
        FakeRequest(hass, "user-1", body={"name": "Schild", "category": "LA"})
    )
    article = _payload(create)["articleNumber"]
    before = json.dumps(runtime.store.data, sort_keys=True, ensure_ascii=False)

    response = await DescriptionSuggestView().post(FakeRequest(hass, "user-1"), article)
    assert response.status == 429
    assert _payload(response)["error"] == {
        "code": "ai_rate_limited",
        "message": "Nutzungslimit erreicht.",
    }
    assert json.dumps(runtime.store.data, sort_keys=True, ensure_ascii=False) == before


async def test_export_writes_catalog(tmp_path: Path) -> None:
    hass, runtime = await _setup(tmp_path, {"admin": ["app_admin"]})
    await runtime.products.async_create({"name": "Schild", "category": "LA"})
    response = await ExportView().post(FakeRequest(hass, "admin"))
    assert response.status == 200
    body = _payload(response)
    assert body["products"] == 1
    assert Path(body["path"]).is_file()
    assert json.loads(Path(body["path"]).read_text(encoding="utf-8"))["products"][0][
        "articleNumber"
    ].startswith("LA")


async def test_unconfigured_runtime_returns_503(tmp_path: Path) -> None:
    hass = FakeHass(tmp_path)
    response = await ProductsView().get(FakeRequest(hass, "user-1"))
    assert response.status == 503
    assert _payload(response)["error"]["code"] == "not_loaded"


@pytest.mark.parametrize("role", ["product", "app_admin"])
async def test_unknown_user_cannot_reach_any_protected_view(tmp_path: Path, role: str) -> None:
    hass, _ = await _setup(tmp_path, {"user-1": [role]})
    for view, method in (
        (ProductsView(), "post"),
        (SettingsView(), "get"),
    ):
        handler = getattr(view, method)
        response = await handler(FakeRequest(hass, "ghost", body={"name": "x", "category": "LA"}))
        assert response.status == 403
