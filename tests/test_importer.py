"""Tests für den Katalogimport (gemockte aiohttp-Session, nie echtes Netzwerk)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import aiohttp
import pytest

import custom_components.frickeldave_store.views as views_module
from custom_components.frickeldave_store import FrickeldaveStoreRuntime
from custom_components.frickeldave_store.ai import AIService
from custom_components.frickeldave_store.const import (
    DEFAULT_CATALOG_URL,
    DEFAULT_IMAGE_BASE_URL,
    DOMAIN,
)
from custom_components.frickeldave_store.importer import (
    ARTICLE_NUMBER_RE,
    CatalogInvalid,
    CatalogUnavailable,
    ImportResult,
    async_import_catalog,
    validate_import_url,
)
from custom_components.frickeldave_store.models import Product, ValidationError
from custom_components.frickeldave_store.permissions import PermissionManager
from custom_components.frickeldave_store.products import ProductService
from custom_components.frickeldave_store.store import CatalogStore
from custom_components.frickeldave_store.views import CatalogImportView

CATEGORIES = ("3D", "EP", "LA", "SB", "WO")


# --------------------------------------------------------------------------- Fakes
class FakeResponse:
    """Minimaler Ersatz für ``aiohttp.ClientResponse`` (async context manager)."""

    def __init__(
        self,
        status: int,
        body: bytes = b"",
        content_type: str = "image/jpeg",
        *,
        session: FakeSession | None = None,
    ) -> None:
        self.status = status
        self.headers = {"Content-Type": content_type} if content_type else {}
        self._body = body
        self._session = session

    async def read(self) -> bytes:
        await asyncio.sleep(0)
        return self._body

    async def __aenter__(self) -> FakeResponse:
        if self._session is not None:
            self._session.enter()
        return self

    async def __aexit__(self, *exc: object) -> bool:
        if self._session is not None:
            self._session.exit()
        return False


class FakeSession:
    """Antwortet auf Katalog- und Bild-URLs aus vorbereiteten Daten."""

    def __init__(
        self,
        catalog: Any = None,
        *,
        images: dict[str, tuple[int, bytes] | BaseException] | None = None,
        image_base: str = DEFAULT_IMAGE_BASE_URL,
        catalog_status: int = 200,
        catalog_body: bytes | None = None,
        catalog_error: BaseException | None = None,
        image_status: int | None = None,
    ) -> None:
        self.catalog = catalog
        self.images = images or {}
        self.image_base = image_base.rstrip("/")
        self.catalog_status = catalog_status
        self.catalog_body = catalog_body
        self.catalog_error = catalog_error
        self.image_status = image_status
        self.requests: list[str] = []
        self.active = 0
        self.max_active = 0

    def enter(self) -> None:
        self.active += 1
        self.max_active = max(self.max_active, self.active)

    def exit(self) -> None:
        self.active -= 1

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append(url)
        if url.startswith(self.image_base):
            return self._image_response(url)
        if self.catalog_error is not None:
            raise self.catalog_error
        if self.catalog_body is not None:
            return FakeResponse(
                self.catalog_status, self.catalog_body, "application/json", session=self
            )
        body = json.dumps(self.catalog).encode("utf-8")
        return FakeResponse(self.catalog_status, body, "application/json", session=self)

    def _image_response(self, url: str) -> FakeResponse:
        entry = self.images.get(url)
        if isinstance(entry, BaseException):
            raise entry
        if entry is not None:
            status, body = entry
            return FakeResponse(status, body, "image/jpeg", session=self)
        if self.image_status is not None:
            return FakeResponse(self.image_status, session=self)
        return FakeResponse(404, b"404: Not Found", "text/plain", session=self)


class FakeConfig:
    def __init__(self, root: Path) -> None:
        self._root = root

    def path(self, *parts: str) -> str:
        return str(self._root.joinpath(*parts))


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
        body: dict[str, Any] | None = None,
        can_read_body: bool | None = None,
    ) -> None:
        self.app = {"hass": hass}
        self._user = SimpleNamespace(id=user_id, name="Test") if user_id else None
        self._body = body
        self.can_read_body = body is not None if can_read_body is None else can_read_body
        self.query: dict[str, str] = {}

    def get(self, key: str, default: Any = None) -> Any:
        if key == "hass_user":
            return self._user
        return default

    async def json(self) -> dict[str, Any]:
        if self._body is None:
            raise ValueError("kein JSON")
        return self._body


# --------------------------------------------------------------------------- Helfer
def catalog_payload(count: int = 63) -> dict[str, Any]:
    """Katalog im Live-Schema mit ``count`` eindeutigen Produkten.

    Feldmuster pro Index ``i``: ``visible`` gilt für ``i % 7 != 0``,
    ``customizable`` für ``i % 4 != 0``, ``stock = i % 3``, ``salesCabinet = i % 2``.
    """

    products: list[dict[str, Any]] = []
    for index in range(count):
        category = CATEGORIES[index % len(CATEGORIES)]
        products.append(
            {
                "articleNumber": f"{category}{index:04d}",
                "category": category,
                "name": f"Produkt {index}",
                "description": f"Beschreibung {index}",
                "price": 10.5 + index,
                "size": "10cm",
                "tags": ["Deko"] if index % 2 == 0 else ["Deko", "Hochzeit"],
                "visible": index % 7 != 0,
                "customizable": index % 4 != 0,
                "stock": index % 3,
                "salesCabinet": index % 2,
            }
        )
    return {"$schema": "http://json-schema.org/draft-04/schema#", "products": products}


def jpeg(tag: bytes = b"") -> bytes:
    """Minimaler JPEG-Header plus Markierung – der Import prüft die Signatur."""

    return b"\xff\xd8\xff\xe0" + tag


def image_url(category: str, article: str, name: str) -> str:
    return f"{DEFAULT_IMAGE_BASE_URL}/{category}/{article}/{name}"


async def make_store(tmp_path: Path) -> CatalogStore:
    store = CatalogStore(data_dir=tmp_path)
    await store.async_load()
    return store


def store_state(store: CatalogStore) -> str:
    return json.dumps(store.data, sort_keys=True, ensure_ascii=False)


def payload(response: Any) -> Any:
    return json.loads(response.body.decode("utf-8"))


# --------------------------------------------------------------------------- Import
async def test_import_creates_full_catalog_with_field_mapping(tmp_path: Path) -> None:
    store = await make_store(tmp_path)
    session = FakeSession(catalog_payload())

    result = await async_import_catalog(store, session=session, import_images=False)

    assert result.to_dict() == {
        "created": 63,
        "skipped": 0,
        "failed": 0,
        "images_imported": 0,
        "products_total": 63,
        "errors": [],
    }
    assert len(store.product_dicts()) == 63
    assert session.requests == [DEFAULT_CATALOG_URL]

    first = store.get_raw_product("3D0000")
    assert first is not None
    assert first["name"] == "Produkt 0"
    assert first["description"] == "Beschreibung 0"
    assert first["category"] == "3D"
    assert first["price"] == 10.5
    assert first["size"] == "10cm"
    assert first["tags"] == ["Deko"]
    assert first["stock"] == 0
    assert first["salesCabinet"] == 0
    assert first["visible"] is False
    assert first["customizable"] is False
    assert first["archived"] is False
    assert first["schemaVersion"] == 1
    assert first["images"] == []
    assert first["createdAt"] == first["updatedAt"]
    assert first["createdAt"].endswith("Z")

    second = store.get_raw_product("EP0001")
    assert second is not None
    assert second["stock"] == 1
    assert second["salesCabinet"] == 1
    assert second["visible"] is True
    assert second["customizable"] is True
    assert second["tags"] == ["Deko", "Hochzeit"]

    third = store.get_raw_product("LA0002")
    assert third is not None
    assert third["stock"] == 2
    assert third["salesCabinet"] == 0

    assert store.get_raw_product("LA0007")["visible"] is False  # type: ignore[index]
    assert store.get_raw_product("SB0003")["salesCabinet"] == 1  # type: ignore[index]

    # Tags landen zusätzlich in der Tag-Liste des Stores.
    assert set(store.tag_names()) == {"Deko", "Hochzeit"}


async def test_second_run_creates_nothing_and_never_overwrites(tmp_path: Path) -> None:
    store = await make_store(tmp_path)
    document = catalog_payload()
    await async_import_catalog(store, session=FakeSession(document), import_images=False)
    store.get_raw_product("LA0002")["name"] = "Lokal geändert"  # type: ignore[index]

    result = await async_import_catalog(store, session=FakeSession(document), import_images=False)

    assert result.created == 0
    assert result.skipped == 63
    assert result.failed == 0
    articles = [item["articleNumber"] for item in store.product_dicts()]
    assert len(articles) == 63
    assert len(set(articles)) == 63
    assert store.get_raw_product("LA0002")["name"] == "Lokal geändert"  # type: ignore[index]


@pytest.mark.parametrize(
    ("kwargs", "error_type"),
    [
        ({"catalog_body": b"<html>kein json</html>"}, CatalogInvalid),
        ({"catalog_body": b'{"produkte": []}'}, CatalogInvalid),
        ({"catalog_body": b"[]"}, CatalogInvalid),
        ({"catalog_status": 500}, CatalogUnavailable),
        ({"catalog_error": TimeoutError()}, CatalogUnavailable),
        ({"catalog_error": aiohttp.ClientError("kaputt")}, CatalogUnavailable),
    ],
)
async def test_broken_catalog_aborts_without_touching_the_store(
    tmp_path: Path, kwargs: dict[str, Any], error_type: type[Exception]
) -> None:
    store = await make_store(tmp_path)
    before = store_state(store)

    with pytest.raises(error_type):
        await async_import_catalog(store, session=FakeSession(**kwargs))

    assert store_state(store) == before
    assert store.product_dicts() == []
    assert not (tmp_path / "frickeldave_store.data").exists()


async def test_image_discovery_stops_at_first_missing_index(tmp_path: Path) -> None:
    store = await make_store(tmp_path)
    session = FakeSession(
        catalog_payload(1),
        images={
            image_url("3D", "3D0000", "3D0000-01.jpg"): (200, jpeg(b"-1")),
            image_url("3D", "3D0000", "3D0000-02.jpg"): (200, jpeg(b"-2")),
        },
    )

    result = await async_import_catalog(store, session=session)

    assert result.created == 1
    assert result.failed == 0
    assert result.images_imported == 2
    assert result.errors == []
    product = store.get_raw_product("3D0000")
    assert product is not None
    assert product["images"] == [
        {"name": "3D0000-01.jpg", "is_main": True},
        {"name": "3D0000-02.jpg", "is_main": False},
    ]
    # Nach dem fehlenden Index 03 wird nicht weiter gesucht (erst jpg, dann jpeg).
    assert image_url("3D", "3D0000", "3D0000-03.jpg") in session.requests
    assert image_url("3D", "3D0000", "3D0000-03.jpeg") in session.requests
    assert image_url("3D", "3D0000", "3D0000-04.jpg") not in session.requests
    assert store.images.path_for("3D0000", "3D0000-01.jpg").read_bytes() == jpeg(b"-1")


async def test_jpeg_extension_is_used_when_jpg_is_missing(tmp_path: Path) -> None:
    store = await make_store(tmp_path)
    session = FakeSession(
        catalog_payload(1),
        images={image_url("3D", "3D0000", "3D0000-01.jpeg"): (200, jpeg(b"-jpeg"))},
    )

    result = await async_import_catalog(store, session=session)

    assert result.images_imported == 1
    product = store.get_raw_product("3D0000")
    assert product is not None
    assert product["images"] == [{"name": "3D0000-01.jpeg", "is_main": True}]


async def test_missing_images_do_not_abort_the_product_import(tmp_path: Path) -> None:
    store = await make_store(tmp_path)

    result = await async_import_catalog(store, session=FakeSession(catalog_payload(3)))

    assert result.created == 3
    assert result.failed == 0
    assert result.images_imported == 0
    assert result.errors == []
    assert all(item["images"] == [] for item in store.product_dicts())


async def test_image_error_is_collected_and_import_continues(tmp_path: Path) -> None:
    store = await make_store(tmp_path)
    session = FakeSession(
        catalog_payload(2),
        images={
            image_url("3D", "3D0000", "3D0000-01.jpg"): (500, b""),
            image_url("EP", "EP0001", "EP0001-01.jpg"): (200, jpeg(b"-ep")),
        },
    )

    result = await async_import_catalog(store, session=session)

    assert result.created == 2
    assert result.failed == 0
    assert result.images_imported == 1
    assert result.errors == [
        {
            "articleNumber": "3D0000",
            "message": "Bild 3D0000-01.jpg konnte nicht geladen werden (HTTP 500).",
        }
    ]
    assert store.get_raw_product("3D0000")["images"] == []  # type: ignore[index]
    assert store.get_raw_product("EP0001")["images"] == [  # type: ignore[index]
        {"name": "EP0001-01.jpg", "is_main": True}
    ]


async def test_non_image_response_is_reported(tmp_path: Path) -> None:
    store = await make_store(tmp_path)
    session = FakeSession(
        catalog_payload(1),
        images={image_url("3D", "3D0000", "3D0000-01.jpg"): (200, b"<html>Fehlerseite</html>")},
    )

    result = await async_import_catalog(store, session=session)

    assert result.created == 1
    assert result.images_imported == 0
    assert result.errors == [
        {
            "articleNumber": "3D0000",
            "message": "Bild 3D0000-01.jpg: kein gültiges Bild (JPEG, PNG, WebP oder GIF).",
        }
    ]
    assert not store.images.path_for("3D0000", "3D0000-01.jpg").exists()


async def test_oversized_image_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import custom_components.frickeldave_store.importer as importer_module

    monkeypatch.setattr(importer_module, "MAX_IMPORT_IMAGE_BYTES", 8)
    store = await make_store(tmp_path)
    session = FakeSession(
        catalog_payload(1),
        images={image_url("3D", "3D0000", "3D0000-01.jpg"): (200, jpeg(b"-zu-gross"))},
    )

    result = await async_import_catalog(store, session=session)

    assert result.created == 1
    assert result.images_imported == 0
    assert "größer als 25 MB" in result.errors[0]["message"]


async def test_image_network_timeout_is_collected(tmp_path: Path) -> None:
    store = await make_store(tmp_path)
    session = FakeSession(
        catalog_payload(1),
        images={image_url("3D", "3D0000", "3D0000-01.jpg"): TimeoutError()},
    )

    result = await async_import_catalog(store, session=session)

    assert result.created == 1
    assert result.errors[0]["articleNumber"] == "3D0000"
    assert "Zeitüberschreitung" in result.errors[0]["message"]


async def test_at_most_ten_images_are_imported(tmp_path: Path) -> None:
    store = await make_store(tmp_path)
    images = {
        image_url("3D", "3D0000", f"3D0000-{index:02d}.jpg"): (200, jpeg(f"-{index}".encode()))
        for index in range(1, 13)
    }
    session = FakeSession(catalog_payload(1), images=images)

    result = await async_import_catalog(store, session=session)

    assert result.images_imported == 10
    product = store.get_raw_product("3D0000")
    assert product is not None
    assert [item["name"] for item in product["images"]] == [
        f"3D0000-{index:02d}.jpg" for index in range(1, 11)
    ]
    assert image_url("3D", "3D0000", "3D0000-11.jpg") not in session.requests


async def test_image_concurrency_is_bounded(tmp_path: Path) -> None:
    store = await make_store(tmp_path)
    document = catalog_payload(20)
    images = {
        image_url(item["category"], item["articleNumber"], f"{item['articleNumber']}-01.jpg"): (
            200,
            jpeg(),
        )
        for item in document["products"]
    }
    session = FakeSession(document, images=images)

    await async_import_catalog(store, session=session)

    assert 1 < session.max_active <= 5


async def test_duplicates_and_invalid_products_are_reported(tmp_path: Path) -> None:
    store = await make_store(tmp_path)
    valid = catalog_payload(1)["products"][0]
    document = {
        "products": [
            valid,
            {**valid, "name": "Doppelt"},
            {**valid, "articleNumber": "XX9999", "category": "XX"},
            {**valid, "articleNumber": "LA0002", "name": "   "},
            {**valid, "articleNumber": "zu-lang-und-klein"},
            {**valid, "articleNumber": "LA0003", "stock": -1},
            {**valid, "articleNumber": "LA0004", "price": "keine Zahl"},
            "kein objekt",
        ]
    }

    result = await async_import_catalog(store, session=FakeSession(document), import_images=False)

    assert result.products_total == 8
    assert result.created == 1
    assert result.failed == 7
    assert result.skipped == 0
    messages = {(item["articleNumber"], item["message"]) for item in result.errors}
    assert ("3D0000", "Doppelte Artikelnummer im Katalog.") in messages
    assert ("XX9999", "Kategorie muss eine von 3D, EP, LA, SB, WO sein.") in messages
    assert ("LA0002", "Name ist ein Pflichtfeld.") in messages
    assert (
        "ZU-LANG-UND-KLEIN",
        "Artikelnummer muss aus 1–12 Großbuchstaben oder Ziffern bestehen.",
    ) in messages
    assert ("LA0003", "stock darf nicht negativ sein.") in messages
    assert ("LA0004", "Preis muss eine Zahl sein.") in messages
    assert ("", "Produkt muss ein JSON-Objekt sein.") in messages
    assert len(store.product_dicts()) == 1


async def test_legacy_article_numbers_are_preserved(tmp_path: Path) -> None:
    store = await make_store(tmp_path)
    document = {
        "products": [
            {
                "articleNumber": "lax",
                "category": "la",
                "name": "Leonardo Weinglas",
                "description": "",
                "price": 12,
                "size": "",
                "tags": [],
                "visible": True,
                "customizable": False,
                "stock": 1,
                "salesCabinet": 0,
            }
        ]
    }

    result = await async_import_catalog(store, session=FakeSession(document), import_images=False)

    assert result.created == 1
    product = store.get_raw_product("LAX")
    assert product is not None
    assert product["articleNumber"] == "LAX"
    assert product["category"] == "LA"
    assert ARTICLE_NUMBER_RE.match("LAX")


async def test_existing_products_are_skipped_and_images_untouched(tmp_path: Path) -> None:
    store = await make_store(tmp_path)
    store.put_product(Product(article_number="3D0000", name="Bestand", category="3D"))
    session = FakeSession(
        catalog_payload(2),
        images={image_url("3D", "3D0000", "3D0000-01.jpg"): (200, jpeg(b"-neu"))},
    )

    result = await async_import_catalog(store, session=session)

    assert result.created == 1
    assert result.skipped == 1
    assert result.images_imported == 0
    existing = store.get_raw_product("3D0000")
    assert existing is not None
    assert existing["name"] == "Bestand"
    assert existing["images"] == []
    assert image_url("3D", "3D0000", "3D0000-01.jpg") not in session.requests


# --------------------------------------------------------------------------- URLs
@pytest.mark.parametrize(
    "url",
    [
        "http://frickeldave.de/data/handmade.json",
        "https://example.com/data/handmade.json",
        "https://127.0.0.1/data/handmade.json",
        "https://raw.githubusercontent.com.evil.example/x.jpg",
        "https://user:pass@frickeldave.de/data/handmade.json",
        "https://frickeldave.de:8443/data/handmade.json",
        "ftp://frickeldave.de/data/handmade.json",
        "",
        "   ",
    ],
)
def test_custom_urls_outside_the_allowlist_are_rejected(url: str) -> None:
    with pytest.raises(ValidationError) as info:
        validate_import_url(url, label="source_url")
    assert info.value.code == "validation_error"


@pytest.mark.parametrize(
    "url",
    [
        DEFAULT_CATALOG_URL,
        DEFAULT_IMAGE_BASE_URL,
        "https://frickeldave.de/data/handmade.json",
        "https://raw.githubusercontent.com/Frickeldave/frickeldave.github.io/dev/src/assets/handmade",
    ],
)
def test_default_and_allowed_urls_pass(url: str) -> None:
    assert validate_import_url(url, label="source_url") == url


# --------------------------------------------------------------------------- Endpoint
async def _setup_runtime(
    tmp_path: Path, users: dict[str, list[str]]
) -> tuple[FakeHass, FrickeldaveStoreRuntime]:
    hass = FakeHass(tmp_path)
    store = await make_store(tmp_path)
    permissions = PermissionManager()
    permissions.load(users)
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


def _patch_import(monkeypatch: pytest.MonkeyPatch, session: FakeSession) -> None:
    """Echten Importer mit gemockter aiohttp-Session verwenden."""

    async def _run(store: CatalogStore, **kwargs: Any) -> ImportResult:
        return await async_import_catalog(store, session=session, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(views_module, "async_import_catalog", _run)


async def test_import_endpoint_with_defaults_needs_product_role(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hass, runtime = await _setup_runtime(tmp_path, {"user-1": ["product"]})
    _patch_import(monkeypatch, FakeSession(catalog_payload(2)))

    response = await CatalogImportView().post(FakeRequest(hass, "user-1"))

    assert response.status == 200
    assert payload(response) == {
        "created": 2,
        "skipped": 0,
        "failed": 0,
        "images_imported": 0,
        "products_total": 2,
        "errors": [],
    }
    assert len(runtime.store.product_dicts()) == 2


async def test_import_endpoint_accepts_empty_and_partial_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hass, _ = await _setup_runtime(tmp_path, {"user-1": ["product"]})
    _patch_import(monkeypatch, FakeSession(catalog_payload(1)))

    with_body = await CatalogImportView().post(
        FakeRequest(hass, "user-1", body={"import_images": False})
    )
    assert with_body.status == 200
    assert payload(with_body)["created"] == 1

    no_body = await CatalogImportView().post(FakeRequest(hass, "user-1", can_read_body=False))
    assert no_body.status == 200
    assert payload(no_body)["skipped"] == 1


async def test_import_endpoint_custom_source_requires_app_admin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hass, _ = await _setup_runtime(tmp_path, {"user-1": ["product"], "admin": ["app_admin"]})
    _patch_import(monkeypatch, FakeSession(catalog_payload(1)))

    denied = await CatalogImportView().post(
        FakeRequest(hass, "user-1", body={"source_url": "https://frickeldave.de/x.json"})
    )
    assert denied.status == 403
    assert payload(denied)["error"]["code"] == "forbidden"

    denied_images = await CatalogImportView().post(
        FakeRequest(hass, "user-1", body={"image_base_url": DEFAULT_IMAGE_BASE_URL})
    )
    assert denied_images.status == 403

    allowed = await CatalogImportView().post(
        FakeRequest(hass, "admin", body={"source_url": "https://frickeldave.de/x.json"})
    )
    assert allowed.status == 200
    assert payload(allowed)["created"] == 1


async def test_import_endpoint_rejects_foreign_host_for_admin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hass, runtime = await _setup_runtime(tmp_path, {"admin": ["app_admin"]})
    _patch_import(monkeypatch, FakeSession(catalog_payload(1)))

    response = await CatalogImportView().post(
        FakeRequest(hass, "admin", body={"source_url": "https://127.0.0.1/handmade.json"})
    )

    assert response.status == 400
    assert payload(response)["error"]["code"] == "validation_error"
    assert runtime.store.product_dicts() == []


async def test_import_endpoint_unknown_user_is_forbidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hass, _ = await _setup_runtime(tmp_path, {"user-1": ["product"], "admin": ["app_admin"]})
    _patch_import(monkeypatch, FakeSession(catalog_payload(1)))

    response = await CatalogImportView().post(FakeRequest(hass, "ghost"))
    assert response.status == 403
    assert b"created" not in response.body

    custom = await CatalogImportView().post(
        FakeRequest(hass, "ghost", body={"source_url": "https://frickeldave.de/x.json"})
    )
    assert custom.status == 403


async def test_import_endpoint_rejects_unknown_fields_and_bad_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hass, _ = await _setup_runtime(tmp_path, {"user-1": ["product"], "admin": ["app_admin"]})
    _patch_import(monkeypatch, FakeSession(catalog_payload(1)))

    unknown = await CatalogImportView().post(
        FakeRequest(hass, "user-1", body={"quelle": "https://frickeldave.de/x"})
    )
    assert unknown.status == 400
    assert payload(unknown)["error"]["code"] == "validation_error"

    bad_flag = await CatalogImportView().post(
        FakeRequest(hass, "user-1", body={"import_images": "ja"})
    )
    assert bad_flag.status == 400

    empty_url = await CatalogImportView().post(
        FakeRequest(hass, "admin", body={"source_url": "   "})
    )
    assert empty_url.status == 400
    assert payload(empty_url)["error"]["code"] == "validation_error"


async def test_import_endpoint_reports_catalog_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hass, _ = await _setup_runtime(tmp_path, {"user-1": ["product"]})
    _patch_import(monkeypatch, FakeSession(catalog_body=b"kein json"))

    response = await CatalogImportView().post(FakeRequest(hass, "user-1"))
    assert response.status == 422
    assert payload(response)["error"]["code"] == "catalog_invalid"

    _patch_import(monkeypatch, FakeSession(catalog_status=503))
    unavailable = await CatalogImportView().post(FakeRequest(hass, "user-1"))
    assert unavailable.status == 502
    assert payload(unavailable)["error"]["code"] == "catalog_unavailable"
