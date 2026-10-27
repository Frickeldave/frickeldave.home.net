"""Tests für Persistenz, Migration und Snapshots."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.frickeldave_store.const import STORAGE_KEY
from custom_components.frickeldave_store.models import SCHEMA_VERSION, StoreError
from custom_components.frickeldave_store.store import CatalogStore, FileBackend


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
