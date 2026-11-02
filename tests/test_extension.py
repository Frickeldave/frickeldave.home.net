"""Tests des öffentlichen Erweiterungsvertrags (Contract 1, Issue #289)."""

from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pytest
from homeassistant.components import frontend
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.frickeldave_store import FrickeldaveStoreRuntime, views
from custom_components.frickeldave_store.ai import AIService
from custom_components.frickeldave_store.const import DOMAIN, VERSION
from custom_components.frickeldave_store.extension import (
    CONTRACT_VERSION,
    EVENT_FEATURES_CHANGED,
    FEATURE_IMPORT,
    FEATURE_PUBLISH,
    ExtensionError,
    ExtensionRegistration,
    FeatureRequest,
    FeatureResponse,
    FeatureSpec,
    StoreFacade,
    async_get_facade,
    async_get_feature_status,
    async_register_extension,
    async_unregister_extension,
)
from custom_components.frickeldave_store.importer import ImageUpload, ImportItem
from custom_components.frickeldave_store.models import Image, PermissionDenied, Product
from custom_components.frickeldave_store.permissions import PermissionManager
from custom_components.frickeldave_store.products import ProductService
from custom_components.frickeldave_store.store import CatalogStore
from custom_components.frickeldave_store.views import FeaturesView, MeView, SettingsView

EXTENSION = "frickeldave_store_sync"


class FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def async_fire(self, event_type: str, data: Any = None) -> None:
        self.events.append((event_type, dict(data or {})))


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
        self.bus = FakeBus()

    async def async_add_executor_job(self, func: Any, *args: Any) -> Any:
        return func(*args)


class FakeRequest:
    def __init__(
        self,
        hass: FakeHass,
        user_id: str | None,
        *,
        body: dict[str, Any] | None = None,
        name: str = "Test",
    ) -> None:
        self.app = {"hass": hass}
        self._user = SimpleNamespace(id=user_id, name=name) if user_id else None
        self._body = body
        self.can_read_body = body is not None

    def get(self, key: str, default: Any = None) -> Any:
        if key == "hass_user":
            return self._user
        return default

    async def json(self) -> dict[str, Any]:
        if self._body is None:
            raise ValueError("kein JSON")
        return self._body


async def _setup(
    tmp_path: Path, users: dict[str, list[str]] | None = None
) -> tuple[FakeHass, FrickeldaveStoreRuntime]:
    hass = FakeHass(tmp_path)
    store = CatalogStore(data_dir=tmp_path)
    await store.async_load()
    permissions = PermissionManager()
    permissions.load(users or {"admin": ["app_admin"], "user-1": ["product"]})
    runtime = FrickeldaveStoreRuntime(
        entry=None,  # type: ignore[arg-type]
        store=store,
        products=ProductService(store),
        permissions=permissions,
        ai=AIService(store),
        export_path=Path("export"),
    )
    hass.data[DOMAIN] = runtime
    return hass, runtime


def _payload(response: Any) -> Any:
    return json.loads(response.body.decode("utf-8"))


def _spec(name: str, **kwargs: Any) -> FeatureSpec:
    data: dict[str, Any] = {"name": name, "title": name.title()}
    data.update(kwargs)
    return FeatureSpec(**data)


def _registration(**kwargs: Any) -> ExtensionRegistration:
    data: dict[str, Any] = {"name": EXTENSION, "version": "0.1.0"}
    data.update(kwargs)
    return ExtensionRegistration(**data)


async def _async_handler(request: FeatureRequest) -> FeatureResponse:
    return FeatureResponse({"feature": request.feature, "user": request.user_name})


# --------------------------------------------------------------- Registrierung
async def test_register_extension_publishes_feature_status(tmp_path: Path) -> None:
    hass, _ = await _setup(tmp_path)
    registration = _registration(
        features={
            FEATURE_PUBLISH: _spec(
                FEATURE_PUBLISH, required_role="app_admin", handler=_async_handler
            ),
            "sync": _spec("sync", title="Synchronisation", provides_panel_routes=True),
        }
    )

    result = await async_register_extension(hass, registration)
    assert result is registration

    status = await async_get_feature_status(hass)
    assert status[FEATURE_PUBLISH].provider == EXTENSION
    assert status[FEATURE_PUBLISH].available is True
    assert status[FEATURE_PUBLISH].contract_version == CONTRACT_VERSION
    assert status[FEATURE_IMPORT].provider == "frickeldave_store"
    assert status["sync"].reason == ""

    event_type, data = hass.bus.events[-1]
    assert event_type == EVENT_FEATURES_CHANGED
    assert data["changed"] == EXTENSION
    assert set(data["features"]) == {FEATURE_PUBLISH, FEATURE_IMPORT, "sync"}


async def test_register_extension_rejects_other_contract(tmp_path: Path) -> None:
    hass, _ = await _setup(tmp_path)

    with pytest.raises(ExtensionError) as excinfo:
        await async_register_extension(hass, _registration(contract_version=CONTRACT_VERSION + 1))
    assert excinfo.value.code == "contract_mismatch"
    assert str(CONTRACT_VERSION) in excinfo.value.message
    assert hass.bus.events == []
    status = await async_get_feature_status(hass)
    assert set(status) == {FEATURE_PUBLISH, FEATURE_IMPORT}


@pytest.mark.parametrize(
    ("features", "reason"),
    [
        ({FEATURE_PUBLISH: _spec(FEATURE_PUBLISH, required_role="root")}, "root"),
        ({FEATURE_PUBLISH: FeatureSpec(name="publish", title="")}, "Titel"),
        ({FEATURE_PUBLISH: FeatureSpec(name="anders", title="Publish")}, "anders"),
    ],
)
async def test_register_extension_rejects_invalid_specs(
    tmp_path: Path, features: dict[str, FeatureSpec], reason: str
) -> None:
    hass, _ = await _setup(tmp_path)

    with pytest.raises(ExtensionError) as excinfo:
        await async_register_extension(hass, _registration(features=features))
    assert excinfo.value.code == "invalid_registration"
    assert reason in excinfo.value.message


async def test_duplicate_registration_is_idempotent_but_conflicts(tmp_path: Path) -> None:
    hass, _ = await _setup(tmp_path)
    await async_register_extension(hass, _registration())
    assert len(hass.bus.events) == 1

    again = await async_register_extension(hass, _registration())
    assert again.name == EXTENSION
    assert len(hass.bus.events) == 1

    with pytest.raises(ExtensionError) as excinfo:
        await async_register_extension(hass, _registration(version="0.2.0"))
    assert excinfo.value.code == "already_registered"
    assert excinfo.value.status == 409


async def test_unregister_extension_removes_status_and_fires_event(tmp_path: Path) -> None:
    hass, _ = await _setup(tmp_path)
    await async_register_extension(
        hass, _registration(features={FEATURE_PUBLISH: _spec(FEATURE_PUBLISH)})
    )
    await async_unregister_extension(hass, EXTENSION)

    assert hass.bus.events[-1][1]["changed"] == EXTENSION
    status = await async_get_feature_status(hass)
    assert status[FEATURE_PUBLISH].provider == "frickeldave_store"

    with pytest.raises(ExtensionError) as excinfo:
        await async_unregister_extension(hass, EXTENSION)
    assert excinfo.value.code == "not_registered"


# --------------------------------------------------------------------- /me
async def test_me_features_without_extension(tmp_path: Path) -> None:
    hass, _ = await _setup(tmp_path)
    body = _payload(await MeView().get(FakeRequest(hass, "user-1")))

    assert body["features"] == {
        FEATURE_PUBLISH: {
            "available": True,
            "reason": "",
            "provider": "frickeldave_store",
            "contract_version": CONTRACT_VERSION,
        },
        FEATURE_IMPORT: {
            "available": True,
            "reason": "",
            "provider": "frickeldave_store",
            "contract_version": CONTRACT_VERSION,
        },
    }
    assert body["roles"] == ["product"]


async def test_me_and_settings_features_with_extension(tmp_path: Path) -> None:
    hass, _ = await _setup(tmp_path)
    await async_register_extension(
        hass,
        _registration(
            version="0.1.0",
            features={
                FEATURE_PUBLISH: _spec(
                    FEATURE_PUBLISH,
                    title="Veröffentlichen",
                    required_role="app_admin",
                    provides_panel_routes=True,
                    ready=False,
                    reason="GitHub-Token fehlt.",
                )
            },
        ),
    )

    me = _payload(await MeView().get(FakeRequest(hass, "user-1")))
    assert me["features"][FEATURE_PUBLISH] == {
        "available": False,
        "reason": "GitHub-Token fehlt.",
        "provider": EXTENSION,
        "contract_version": CONTRACT_VERSION,
    }
    assert me["features"][FEATURE_IMPORT]["provider"] == "frickeldave_store"

    settings = _payload(await SettingsView().get(FakeRequest(hass, "admin")))
    assert settings["features"] == me["features"]
    assert settings["version"] == VERSION
    details = settings["feature_details"]
    assert details[FEATURE_PUBLISH] == {
        "name": FEATURE_PUBLISH,
        "title": "Veröffentlichen",
        "required_role": "app_admin",
        "provides_panel_routes": True,
        "available": False,
        "reason": "GitHub-Token fehlt.",
        "provider": EXTENSION,
        "contract_version": CONTRACT_VERSION,
    }
    assert details[FEATURE_IMPORT]["provider"] == "frickeldave_store"
    assert details[FEATURE_IMPORT]["title"] == "Katalogimport"
    assert details[FEATURE_IMPORT]["required_role"] == "product"


# ------------------------------------------------------------ Feature-Route
class StubPublish:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def async_publish(self, target: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"target": target, **kwargs})
        return {"target": target, "dry_run": kwargs["dry_run"], "products": 0}


async def test_feature_route_falls_back_to_builtin_publish(tmp_path: Path) -> None:
    hass, runtime = await _setup(tmp_path)
    stub = StubPublish()
    runtime.publish = stub  # type: ignore[assignment]

    response = await FeaturesView().post(
        FakeRequest(hass, "admin", body={"target": "dev", "dry_run": True}), FEATURE_PUBLISH
    )
    assert response.status == 200
    assert _payload(response) == {"target": "dev", "dry_run": True, "products": 0}
    assert stub.calls[0]["user_id"] == "admin"


async def test_feature_route_fallback_keeps_permissions_and_validation(tmp_path: Path) -> None:
    hass, _ = await _setup(tmp_path)

    denied = await FeaturesView().post(FakeRequest(hass, "user-1", body={}), FEATURE_PUBLISH)
    assert denied.status == 403
    assert _payload(denied)["error"]["code"] == "forbidden"

    invalid = await FeaturesView().post(
        FakeRequest(hass, "admin", body={"target": "staging"}), FEATURE_PUBLISH
    )
    assert invalid.status == 400
    assert _payload(invalid)["error"]["code"] == "validation_error"


async def test_feature_route_falls_back_to_builtin_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hass, _ = await _setup(tmp_path)
    calls: list[dict[str, Any]] = []

    async def fake_import(store: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return SimpleNamespace(to_dict=lambda: {"created": 0, "dry_run": kwargs["dry_run"]})

    monkeypatch.setattr(views, "async_import_catalog", fake_import)

    response = await FeaturesView().post(
        FakeRequest(hass, "user-1", body={"dry_run": True}), FEATURE_IMPORT
    )
    assert response.status == 200
    assert _payload(response) == {"created": 0, "dry_run": True}
    assert calls[0]["source_url"].startswith("https://")

    custom = await FeaturesView().post(
        FakeRequest(
            hass, "user-1", body={"source_url": "https://frickeldave.de/data/handmade.json"}
        ),
        FEATURE_IMPORT,
    )
    assert custom.status == 403


async def test_feature_route_unknown_without_extension(tmp_path: Path) -> None:
    hass, _ = await _setup(tmp_path)
    response = await FeaturesView().post(FakeRequest(hass, "admin", body={}), "sync")
    assert response.status == 404
    assert _payload(response)["error"]["code"] == "feature_unknown"


async def test_feature_route_dispatches_to_registration(tmp_path: Path) -> None:
    hass, _ = await _setup(tmp_path)
    seen: list[FeatureRequest] = []

    async def handler(request: FeatureRequest) -> FeatureResponse:
        seen.append(request)
        return FeatureResponse(
            {"user": request.user_id, "can": request.facade.can("app_admin")}, status=201
        )

    async def sync_handler(request: FeatureRequest) -> FeatureResponse:
        return FeatureResponse({"feature": request.feature})

    await async_register_extension(
        hass,
        _registration(
            features={
                FEATURE_PUBLISH: _spec(FEATURE_PUBLISH, required_role="app_admin", handler=handler)
            },
            panel_actions={"sync_dialog": sync_handler},
        ),
    )

    denied = await FeaturesView().post(FakeRequest(hass, "user-1", body={}), FEATURE_PUBLISH)
    assert denied.status == 403

    response = await FeaturesView().post(FakeRequest(hass, "admin", body={}), FEATURE_PUBLISH)
    assert response.status == 201
    assert _payload(response) == {"user": "admin", "can": True}
    assert seen[0].user_name == "Test"

    action = await FeaturesView().post(FakeRequest(hass, "admin", body={}), "sync_dialog")
    assert action.status == 200
    assert _payload(action) == {"feature": "sync_dialog"}
    assert "sync_dialog" not in _payload(await MeView().get(FakeRequest(hass, "admin")))["features"]


async def test_feature_route_reports_unavailable_and_errors(tmp_path: Path) -> None:
    hass, _ = await _setup(tmp_path)

    async def failing(request: FeatureRequest) -> FeatureResponse:
        raise ExtensionError("invalid_request", "target fehlt.")

    async def wrong_type(request: FeatureRequest) -> Any:
        return {"ok": True}

    await async_register_extension(
        hass,
        _registration(
            features={
                "gesperrt": _spec("gesperrt", ready=False, reason="Nicht eingerichtet."),
                "kaputt": _spec("kaputt", handler=failing),
                "falsch": _spec("falsch", handler=wrong_type),
            }
        ),
    )

    unavailable = await FeaturesView().post(FakeRequest(hass, "admin", body={}), "gesperrt")
    assert unavailable.status == 409
    assert _payload(unavailable)["error"] == {
        "code": "feature_unavailable",
        "message": "Nicht eingerichtet.",
    }

    error = await FeaturesView().post(FakeRequest(hass, "admin", body={}), "kaputt")
    assert error.status == 400
    assert _payload(error)["error"]["code"] == "invalid_request"

    wrong = await FeaturesView().post(FakeRequest(hass, "admin", body={}), "falsch")
    assert wrong.status == 502
    assert _payload(wrong)["error"]["code"] == "handler_failed"


async def test_feature_route_needs_running_base(tmp_path: Path) -> None:
    hass = FakeHass(tmp_path)
    response = await FeaturesView().post(FakeRequest(hass, "admin", body={}), "sync")
    assert response.status == 503
    assert _payload(response)["error"]["code"] == "not_loaded"


# ------------------------------------------------------------------- Fassade
async def _facade(
    tmp_path: Path, users: dict[str, list[str]] | None = None, user_id: str = "user-1"
) -> tuple[FakeHass, FrickeldaveStoreRuntime, StoreFacade]:
    hass, runtime = await _setup(tmp_path, users)
    return hass, runtime, StoreFacade(hass, runtime, user_id=user_id, user_name="Test")


async def test_facade_reads_products_locations_and_numbers(tmp_path: Path) -> None:
    _, runtime, facade = await _facade(tmp_path)
    product = Product(
        article_number="LAABCD",
        name="Schild",
        category="LA",
        price=12.5,
        tags=["holz"],
        stock={"lager": 2},
    )
    runtime.store.put_product(product)
    archived = Product(article_number="LAAAAA", name="Alt", category="LA", archived=True)
    runtime.store.put_product(archived)
    await runtime.store.async_save()

    products = await facade.async_products()
    assert [item.article_number for item in products] == ["LAAAAA", "LAABCD"]
    assert products[1].tags == ("holz",)
    assert products[1].archived is False
    assert products[0].archived is True
    assert isinstance(products[1].stock, MappingProxyType)

    locations = await facade.async_locations()
    assert [item["id"] for item in locations] == ["lager", "verkaufsschrank"]
    with pytest.raises(TypeError):
        locations[0]["name"] = "anders"  # type: ignore[index]

    assert await facade.async_article_numbers() == frozenset({"LAABCD", "LAAAAA"})
    assert facade.contract_version == CONTRACT_VERSION


async def test_facade_permissions(tmp_path: Path) -> None:
    _, _, facade = await _facade(tmp_path)
    assert facade.can("product") is True
    assert facade.can("app_admin") is False
    facade.require("product")
    with pytest.raises(PermissionDenied):
        facade.require("app_admin")


async def test_facade_settings_are_read_only(tmp_path: Path) -> None:
    _, runtime, facade = await _facade(tmp_path)
    runtime.store.update_settings({"ai": {"api_key": "geheim"}})

    settings = facade.settings
    assert isinstance(settings, MappingProxyType)
    assert settings["ai"]["api_key_set"] is True
    assert "api_key" not in settings["ai"]
    with pytest.raises(TypeError):
        settings["ai"]["model"] = "anders"  # type: ignore[index]


async def test_facade_snapshot_returns_name_not_path(tmp_path: Path) -> None:
    _, runtime, facade = await _facade(tmp_path)
    runtime.store.put_product(Product(article_number="LAABCD", name="Schild", category="LA"))
    await runtime.store.async_save()

    name = await facade.async_create_snapshot("pre-sync")
    assert name is not None
    assert "/" not in name
    assert (tmp_path / "backups" / name).is_file()


async def test_facade_build_export_contains_catalog_and_images(tmp_path: Path) -> None:
    _, runtime, facade = await _facade(tmp_path)
    product = Product(
        article_number="LAABCD",
        name="Schild",
        category="LA",
        price=9.0,
        images=[Image(name="LAABCD-01.jpg", is_main=True)],
    )
    runtime.store.put_product(product)
    await runtime.store.images.async_write("LAABCD", "LAABCD-01.jpg", b"jpegdata")

    bundle = await facade.async_build_export()
    assert bundle.catalog_path == "public/data/handmade.json"
    assert bundle.product_count == 1
    assert bundle.image_count == 1
    assert bundle.empty_catalog is False
    assert b"LAABCD" in bundle.catalog_bytes
    artifact = bundle.artifacts[0]
    assert artifact.kind == "image"
    assert artifact.path == "src/assets/handmade/LAABCD/LAABCD-01.jpg"
    assert artifact.data == b"jpegdata"

    visible_only = await facade.async_build_export(visible_only=True)
    assert visible_only.product_count == 1


async def test_facade_import_batch_creates_and_overwrites(tmp_path: Path) -> None:
    _, runtime, facade = await _facade(tmp_path)
    runtime.store.put_product(
        Product(article_number="LABEST", name="Alt", category="LA", price=1.0)
    )
    await runtime.store.async_save()

    created = {
        "articleNumber": "LANEU1",
        "name": "Neu",
        "category": "LA",
        "price": 20.0,
    }
    result = await facade.async_apply_import_batch([created], overwrite=False)
    assert result.created == ("LANEU1",)
    assert result.updated == ()
    assert result.failed == 0
    assert result.snapshot is not None
    assert "1 neu angelegt" in result.summary
    assert runtime.store.get_product("LANEU1").name == "Neu"

    updated = {
        "articleNumber": "LABEST",
        "name": "Neu benannt",
        "category": "LA",
        "price": 5.0,
    }
    untouched = await facade.async_apply_import_batch([updated], overwrite=False)
    assert untouched.updated == ()
    assert runtime.store.get_product("LABEST").name == "Alt"

    overwritten = await facade.async_apply_import_batch([updated], overwrite=True)
    assert overwritten.updated == ("LABEST",)
    assert runtime.store.get_product("LABEST").name == "Neu benannt"

    invalid = await facade.async_apply_import_batch([{"name": "ohne Kategorie"}])
    assert invalid.failed == 1
    assert invalid.errors[0].article_number == ""


async def test_facade_import_batch_writes_images(tmp_path: Path) -> None:
    _, runtime, facade = await _facade(tmp_path)
    item = ImportItem(
        article_number="LAIMG1",
        payload={"name": "Mit Bild", "category": "LA", "price": 3.0},
        images=(ImageUpload(name="LAIMG1-01.jpg", data=b"bild", is_main=True),),
    )

    result = await facade.async_apply_import_batch([item])
    assert result.created == ("LAIMG1",)
    assert result.images_written == 1
    product = runtime.store.get_product("LAIMG1")
    assert [image.name for image in product.images] == ["LAIMG1-01.jpg"]
    assert product.images[0].is_main is True
    assert (tmp_path / "images" / "LAIMG1" / "LAIMG1-01.jpg").read_bytes() == b"bild"


async def test_facade_import_batch_rejects_unsafe_image_name(tmp_path: Path) -> None:
    _, _, facade = await _facade(tmp_path)
    item = ImportItem(
        article_number="LAPATH",
        payload={"name": "Boese", "category": "LA"},
        images=(ImageUpload(name="../escape.jpg", data=b"x"),),
    )
    with pytest.raises(Exception) as excinfo:
        await facade.async_apply_import_batch([item])
    assert "Bildname" in str(excinfo.value)


async def test_async_get_facade(tmp_path: Path) -> None:
    hass, runtime = await _setup(tmp_path)
    facade = await async_get_facade(hass, user_id="user-1")
    assert facade is not None
    assert facade.user_id == "user-1"
    assert facade.user_name == ""

    empty = FakeHass(tmp_path)
    assert await async_get_facade(empty) is None


# ------------------------------------------------- Registrierung nach Panel-Setup
async def test_registration_after_panel_setup_is_harmless(
    hass: Any, enable_custom_integrations: Any
) -> None:
    assert await async_setup_component(hass, "http", {})
    assert await async_setup_component(hass, "frontend", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"users": "user-1: app_admin"},
        options={"data_path": "config"},
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    panels_before = set(hass.data[frontend.DATA_PANELS])

    fired: list[Any] = []
    unsub = hass.bus.async_listen(EVENT_FEATURES_CHANGED, fired.append)
    await async_register_extension(
        hass, _registration(features={FEATURE_PUBLISH: _spec(FEATURE_PUBLISH)})
    )
    await async_unregister_extension(hass, EXTENSION)
    unsub()

    assert len(fired) == 2
    assert set(hass.data[frontend.DATA_PANELS]) == panels_before
    status = await async_get_feature_status(hass)
    assert status[FEATURE_PUBLISH].provider == "frickeldave_store"
