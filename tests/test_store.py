"""Tests für Persistenz, Migration und Snapshots."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.frickeldave_store.const import STORAGE_KEY
from custom_components.frickeldave_store.models import (
    SCHEMA_VERSION,
    ConflictError,
    StoreError,
    ValidationError,
)
from custom_components.frickeldave_store.store import (
    DEFAULT_IMAGE_MAX_BYTES,
    MAX_IMAGE_MAX_BYTES,
    MIN_IMAGE_MAX_BYTES,
    CatalogStore,
    FileBackend,
)


async def test_roundtrip_and_atomic_write(tmp_path: Path) -> None:
    store = CatalogStore(data_dir=tmp_path)
    await store.async_load()
    product = await _add_product(store)
    await store.async_save()

    store_file = tmp_path / STORAGE_KEY
    assert store_file.is_file()
    payload = json.loads(store_file.read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == SCHEMA_VERSION
    assert payload["products"][0]["articleNumber"] == product["articleNumber"]
    assert not list(tmp_path.glob("*.tmp"))

    reloaded = CatalogStore(data_dir=tmp_path)
    await reloaded.async_load()
    assert reloaded.products()[0].article_number == product["articleNumber"]


async def _add_product(store: CatalogStore) -> dict[str, object]:
    from custom_components.frickeldave_store.products import ProductService

    product = await ProductService(store).async_create({"name": "Test", "category": "LA"})
    return product.to_dict()


async def test_migration_from_unversioned_payload_creates_snapshot(tmp_path: Path) -> None:
    store_file = tmp_path / STORAGE_KEY
    store_file.parent.mkdir(parents=True, exist_ok=True)
    legacy = {
        "products": [
            {
                "articleNumber": "LAH3K3",
                "name": "Schild",
                "description": "Alt",
                "tags": ["Holz"],
                "price": 35,
                "size": "25cm",
                "visible": True,
                "customizable": True,
                "salesCabinet": 0,
                "stock": 0,
                "category": "LA",
            }
        ]
    }
    store_file.write_text(json.dumps(legacy), encoding="utf-8")

    store = CatalogStore(data_dir=tmp_path)
    await store.async_load()

    assert store.data["schemaVersion"] == SCHEMA_VERSION
    assert store.products()[0].article_number == "LAH3K3"
    snapshots = sorted((tmp_path / "backups").glob("*.json"))
    assert len(snapshots) == 1
    assert "pre-load-v0" in snapshots[0].name
    assert json.loads(snapshots[0].read_text(encoding="utf-8")) == legacy

    # Der migrierte Stand wurde persistiert und ist idempotent.
    migrated = json.loads(store_file.read_text(encoding="utf-8"))
    assert migrated["schemaVersion"] == SCHEMA_VERSION
    again = CatalogStore(data_dir=tmp_path)
    await again.async_load()
    assert len(sorted((tmp_path / "backups").glob("*.json"))) == 1


async def test_newer_schema_is_rejected(tmp_path: Path) -> None:
    store_file = tmp_path / STORAGE_KEY
    store_file.write_text(json.dumps({"schemaVersion": 99, "products": []}), encoding="utf-8")
    store = CatalogStore(data_dir=tmp_path)
    with pytest.raises(StoreError) as err:
        await store.async_load()
    assert err.value.code == "schema_too_new"


async def test_file_backend_load_missing_returns_none(tmp_path: Path) -> None:
    assert await FileBackend(tmp_path / "nope.json").async_load() is None


def test_image_storage_paths(tmp_path: Path) -> None:
    store = CatalogStore(data_dir=tmp_path)
    path = store.images.path_for("LAH3K3", "LAH3K3-01.jpg")
    assert path == tmp_path / "images" / "LAH3K3" / "LAH3K3-01.jpg"
    assert store.images.path_for("LAH3K3", "../../etc/passwd").parent.name == "LAH3K3"


async def test_v1_migration_maps_stock_to_default_locations(tmp_path: Path) -> None:
    store_file = tmp_path / STORAGE_KEY
    legacy = {
        "schemaVersion": 1,
        "products": [
            {
                "articleNumber": "LAH3K3",
                "name": "Schild",
                "category": "LA",
                "price": 35,
                "stock": 4,
                "salesCabinet": 2,
            }
        ],
        "tags": ["Holz"],
        "bookings": [
            {
                "id": "b1",
                "articleNumber": "LAH3K3",
                "type": "in",
                "quantity": 4,
                "at": "2026-01-01T00:00:00Z",
            },
            {
                "id": "b2",
                "articleNumber": "LAH3K3",
                "type": "transfer",
                "quantity": 2,
                "at": "2026-01-02T00:00:00Z",
            },
        ],
        "settings": {
            "ai": {"endpoint": "x", "model": "m", "prompt": "p", "api_key": "secret"},
        },
    }
    store_file.write_text(json.dumps(legacy), encoding="utf-8")

    store = CatalogStore(data_dir=tmp_path)
    await store.async_load()

    assert store.locations() == [
        {"id": "lager", "name": "Lager"},
        {"id": "verkaufsschrank", "name": "Verkaufsschrank"},
    ]
    product = store.get_raw_product("LAH3K3")
    assert product is not None
    assert product["stock"] == {"lager": 4, "verkaufsschrank": 2}

    bookings = {item["id"]: item for item in store.bookings()}
    assert bookings["b1"]["locationId"] == "lager"
    assert bookings["b2"]["locationId"] == "lager"
    assert bookings["b2"]["targetLocationId"] == "verkaufsschrank"

    snapshots = sorted((tmp_path / "backups").glob("*.json"))
    assert len(snapshots) == 1
    assert "pre-load-v1" in snapshots[0].name
    assert json.loads(store_file.read_text(encoding="utf-8"))["schemaVersion"] == 2
    # Bestand bleibt über einen Reload hinweg erhalten (idempotent).
    reloaded = CatalogStore(data_dir=tmp_path)
    await reloaded.async_load()
    assert reloaded.get_raw_product("LAH3K3")["stock"] == {  # type: ignore[index]
        "lager": 4,
        "verkaufsschrank": 2,
    }


async def test_rename_keeps_stock_and_delete_requires_empty_location(tmp_path: Path) -> None:
    store = CatalogStore(data_dir=tmp_path)
    await store.async_load()
    product = await _add_product(store)
    raw = store.get_raw_product(str(product["articleNumber"]))
    assert raw is not None
    raw["stock"] = {"lager": 3, "verkaufsschrank": 2}
    await store.async_save()

    renamed = store.set_locations(
        [
            {"id": "lager", "name": "Werkstatt"},
            {"id": "verkaufsschrank", "name": "Verkaufsschrank"},
        ]
    )
    assert renamed[0] == {"id": "lager", "name": "Werkstatt"}
    assert store.get_raw_product(str(product["articleNumber"]))["stock"] == {  # type: ignore[index]
        "lager": 3,
        "verkaufsschrank": 2,
    }

    with pytest.raises(ConflictError) as err:
        store.set_locations([{"id": "lager", "name": "Werkstatt"}])
    assert "Verkaufsschrank" in err.value.message
    assert "2 Stück" in err.value.message
    assert store.locations()[1]["id"] == "verkaufsschrank"

    # Positioneller Weg (Options-Flow): Zeile 1 benennt um, Zeile 2 bleibt.
    positional = store.set_locations(["Werkstatt", "Verkaufsschrank"], match_by="position")
    assert positional[0]["id"] == "lager"

    # Eine neue, leere Lagerstätte darf hinzukommen und wieder verschwinden.
    with_new = store.set_locations(["Werkstatt", "Verkaufsschrank", "Auto"], match_by="position")
    assert with_new[2]["id"] == "auto"
    assert store.get_raw_product(str(product["articleNumber"]))["stock"]["auto"] == 0  # type: ignore[index]

    raw = store.get_raw_product(str(product["articleNumber"]))
    assert raw is not None
    raw["stock"]["verkaufsschrank"] = 0
    store.set_locations([{"id": "lager", "name": "Werkstatt"}, {"id": "auto", "name": "Auto"}])
    assert set(store.locations()[0]) == {"id", "name"}
    assert [item["id"] for item in store.locations()] == ["lager", "auto"]
    contents = store.get_raw_product(str(product["articleNumber"]))["stock"]  # type: ignore[index]
    assert "verkaufsschrank" not in contents

    # Eigene IDs überleben einen Reload (Coercion darf sie nicht auf Defaults zurücksetzen).
    await store.async_save()
    reloaded = CatalogStore(data_dir=tmp_path)
    await reloaded.async_load()
    assert reloaded.locations() == [
        {"id": "lager", "name": "Werkstatt"},
        {"id": "auto", "name": "Auto"},
    ]
    assert reloaded.get_raw_product(str(product["articleNumber"]))["stock"] == {  # type: ignore[index]
        "lager": 3,
        "auto": 0,
    }


def test_default_image_max_bytes_is_500_kib(tmp_path: Path) -> None:
    store = CatalogStore(data_dir=tmp_path)

    assert DEFAULT_IMAGE_MAX_BYTES == 512000
    assert store.image_max_bytes == 512000
    assert store.public_settings()["images"] == {"max_bytes": 512000}


async def test_update_image_max_bytes(tmp_path: Path) -> None:
    store = CatalogStore(data_dir=tmp_path)
    await store.async_load()

    settings = store.update_settings({"images": {"max_bytes": 614400}})

    assert settings["images"] == {"max_bytes": 614400}
    assert store.image_max_bytes == 614400
    assert store.public_settings()["images"] == {"max_bytes": 614400}

    await store.async_save()
    reloaded = CatalogStore(data_dir=tmp_path)
    await reloaded.async_load()
    assert reloaded.image_max_bytes == 614400


@pytest.mark.parametrize(
    "value",
    [MIN_IMAGE_MAX_BYTES - 1, MAX_IMAGE_MAX_BYTES + 1, 0, -1, True, "512000", 512000.0],
)
async def test_update_image_max_bytes_rejects_invalid_values(tmp_path: Path, value: object) -> None:
    store = CatalogStore(data_dir=tmp_path)
    await store.async_load()

    with pytest.raises(ValidationError):
        store.update_settings({"images": {"max_bytes": value}})

    assert store.image_max_bytes == DEFAULT_IMAGE_MAX_BYTES


async def test_update_image_max_bytes_rejects_unknown_fields(tmp_path: Path) -> None:
    store = CatalogStore(data_dir=tmp_path)
    await store.async_load()

    with pytest.raises(ValidationError):
        store.update_settings({"images": {"max_bytes": 512000, "quality": 80}})
    with pytest.raises(ValidationError):
        store.update_settings({"images": []})


async def test_old_payload_without_images_section_loads(tmp_path: Path) -> None:
    store_file = tmp_path / STORAGE_KEY
    store_file.parent.mkdir(parents=True, exist_ok=True)
    legacy = {
        "schemaVersion": SCHEMA_VERSION,
        "settings": {"ai": {"endpoint": "https://example.invalid", "model": "x", "prompt": ""}},
    }
    store_file.write_text(json.dumps(legacy), encoding="utf-8")

    store = CatalogStore(data_dir=tmp_path)
    await store.async_load()

    assert store.image_max_bytes == DEFAULT_IMAGE_MAX_BYTES
    assert store.settings["images"] == {"max_bytes": DEFAULT_IMAGE_MAX_BYTES}
    assert store.public_settings()["images"] == {"max_bytes": DEFAULT_IMAGE_MAX_BYTES}
