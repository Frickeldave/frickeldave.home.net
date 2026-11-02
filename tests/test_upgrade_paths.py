"""Regressionstests für Upgrade-Pfade und das Sicherheitsnetz (Issue #289).

Der Fehlerbericht: „immer alle Produkte gelöscht, wenn eine neue Version
eingespielt wird“. Ursache war HA's ``_StoreManager``-Cache: er kennt nur
Dateien aus ``<config>/.storage`` und meldet für den Katalog in
``<config>/frickeldave_store/`` „existiert nicht“. Diese Tests laden echte
HA-Store-Dateien der Tags 0.1.7, 0.1.8 und 0.1.9 mit einem frisch gestarteten
``HomeAssistant`` – inklusive initialisiertem Store-Manager-Cache.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from typing import Any

import pytest
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers.storage import get_internal_store_manager

from custom_components.frickeldave_store.const import (
    BACKUP_DIR_NAME,
    STORAGE_KEY,
    STORAGE_MINOR_VERSION,
    STORAGE_VERSION,
)
from custom_components.frickeldave_store.models import SCHEMA_VERSION, StoreError
from custom_components.frickeldave_store.store import CatalogStore

PRODUCTS_SCHEMA1: list[dict[str, Any]] = [
    {
        "articleNumber": "LAH3K1",
        "name": "Schild",
        "description": "Holzschild",
        "category": "LA",
        "price": 19.5,
        "size": "20x10",
        "tags": ["schild", "holz"],
        "customizable": True,
        "visible": True,
        "stock": 3,
        "salesCabinet": 1,
        "images": [{"name": "LAH3K1-01.jpg", "is_main": True}],
        "archived": False,
        "createdAt": "2025-09-01T10:00:00+00:00",
        "updatedAt": "2025-09-02T10:00:00+00:00",
        "schemaVersion": 1,
    },
    {
        "articleNumber": "3DABC1",
        "name": "Drache",
        "description": "3D-Druck",
        "category": "3D",
        "price": 12.0,
        "size": "",
        "tags": ["drache"],
        "customizable": False,
        "visible": True,
        "stock": 0,
        "salesCabinet": 2,
        "images": [],
        "archived": False,
        "createdAt": "2025-09-03T10:00:00+00:00",
        "updatedAt": "2025-09-04T10:00:00+00:00",
        "schemaVersion": 1,
    },
    {
        "articleNumber": "EPX991",
        "name": "Anhänger",
        "description": "Epoxidharz",
        "category": "EP",
        "price": 8.0,
        "size": "3cm",
        "tags": ["anhänger"],
        "customizable": True,
        "visible": False,
        "stock": 7,
        "salesCabinet": 0,
        "images": [
            {"name": "EPX991-01.jpg", "is_main": True},
            {"name": "EPX991-02.jpg", "is_main": False},
        ],
        "archived": True,
        "createdAt": "2025-09-05T10:00:00+00:00",
        "updatedAt": "2025-09-06T10:00:00+00:00",
        "schemaVersion": 1,
    },
]

BOOKINGS_SCHEMA1: list[dict[str, Any]] = [
    {
        "id": "b1",
        "articleNumber": "LAH3K1",
        "type": "in",
        "quantity": 3,
        "note": "Zugang",
        "at": "2025-09-01T10:05:00+00:00",
        "ha_user_id": "user-1",
    },
    {
        "id": "b2",
        "articleNumber": "3DABC1",
        "type": "transfer",
        "quantity": 1,
        "note": "Umlagerung",
        "at": "2025-09-02T10:05:00+00:00",
        "ha_user_id": "user-1",
    },
]

SETTINGS_SCHEMA1: dict[str, Any] = {
    "ai": {
        "endpoint": "https://api.deepseek.com/chat/completions",
        "model": "deepseek-flash",
        "prompt": "Beschreibe den Artikel.",
        "api_key": "sk-test",
    }
}


def schema1_payload() -> dict[str, Any]:
    """Payload, wie ihn 0.1.7 und 0.1.8 geschrieben haben."""

    return {
        "schemaVersion": 1,
        "products": [dict(item) for item in PRODUCTS_SCHEMA1],
        "tags": ["schild", "holz", "drache", "anhänger"],
        "bookings": [dict(item) for item in BOOKINGS_SCHEMA1],
        "settings": {"ai": dict(SETTINGS_SCHEMA1["ai"])},
        "updatedAt": "2025-09-06T10:00:00+00:00",
    }


def schema2_payload() -> dict[str, Any]:
    """Payload, wie ihn 0.1.9 geschrieben hat (Bestand je Lagerstätte)."""

    products = []
    for item in PRODUCTS_SCHEMA1:
        migrated = {
            key: value for key, value in item.items() if key not in {"stock", "salesCabinet"}
        }
        migrated["schemaVersion"] = 2
        migrated["stock"] = {"lager": item["stock"], "verkaufsschrank": item["salesCabinet"]}
        products.append(migrated)
    bookings = []
    for item in BOOKINGS_SCHEMA1:
        migrated_booking = {**item, "locationId": "lager", "targetLocationId": ""}
        if item["type"] == "transfer":
            migrated_booking["targetLocationId"] = "verkaufsschrank"
        bookings.append(migrated_booking)
    return {
        "schemaVersion": 2,
        "locations": [
            {"id": "lager", "name": "Lager"},
            {"id": "verkaufsschrank", "name": "Verkaufsschrank"},
        ],
        "products": products,
        "tags": ["schild", "holz", "drache", "anhänger"],
        "bookings": bookings,
        "settings": {
            "ai": dict(SETTINGS_SCHEMA1["ai"]),
            "publish": {"repository": "Frickeldave/frickeldave.github.io"},
        },
        "updatedAt": "2025-09-06T10:00:00+00:00",
    }


LEGACY_FILES: dict[str, tuple[dict[str, int], Callable[[], dict[str, Any]]]] = {
    "0.1.7": ({"version": 1, "minor_version": 1}, schema1_payload),
    "0.1.8": ({"version": 1, "minor_version": 1}, schema1_payload),
    "0.1.9": ({"version": 2, "minor_version": 0}, schema2_payload),
}


@pytest.fixture
async def hass_instance(tmp_path: Path) -> AsyncGenerator[HomeAssistant]:
    """Frisch gestartete ``HomeAssistant``-Instanz mit initialisiertem Store-Cache.

    ``async_load_base_functionality`` initialisiert den ``_StoreManager`` beim
    HA-Start; genau dieser Cache war die Fehlerursache. ``.storage/`` existiert
    hier bewusst, damit der Cache die Dateiliste kennt.
    """

    config_dir = tmp_path / "config"
    (config_dir / ".storage").mkdir(parents=True)
    (config_dir / ".storage" / "core.config_entries").write_text("{}", encoding="utf-8")

    hass = HomeAssistant(str(config_dir))
    hass.state = CoreState.running
    await get_internal_store_manager(hass).async_initialize()
    try:
        yield hass
    finally:
        await hass.async_stop(force=True)


def write_store_file(
    data_dir: Path,
    payload: dict[str, Any],
    *,
    version: int,
    minor_version: int,
) -> Path:
    """Store-Datei im Format von HA's ``Store`` ablegen."""

    data_dir.mkdir(parents=True, exist_ok=True)
    store_file = data_dir / STORAGE_KEY
    store_file.write_text(
        json.dumps(
            {
                "version": version,
                "minor_version": minor_version,
                "key": STORAGE_KEY,
                "data": payload,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return store_file


async def load_store(hass: HomeAssistant, data_dir: Path) -> CatalogStore:
    store = CatalogStore(hass, data_dir=data_dir)
    await store.async_load()
    return store


def read_store_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("legacy_tag", ["0.1.7", "0.1.8", "0.1.9"])
async def test_upgrade_keeps_every_product(
    hass_instance: HomeAssistant, tmp_path: Path, legacy_tag: str
) -> None:
    """0.1.7/0.1.8/0.1.9 → aktuell: kein Produkt, keine Buchung, keine Einstellung geht verloren."""

    envelope, payload_factory = LEGACY_FILES[legacy_tag]
    payload = payload_factory()
    data_dir = tmp_path / "data"
    store_file = write_store_file(data_dir, payload, **envelope)

    # Der HA-Store-Cache würde für diesen Schlüssel „existiert nicht“ melden –
    # genau daran ist die alte Implementierung gescheitert.
    manager = get_internal_store_manager(hass_instance)
    assert manager.async_fetch(STORAGE_KEY) == (False, None)

    store = await load_store(hass_instance, data_dir)

    assert len(store.product_dicts()) == len(payload["products"]) == 3
    assert set(store.article_numbers()) == {"LAH3K1", "3DABC1", "EPX991"}
    assert store.tag_names() == ["schild", "holz", "drache", "anhänger"]
    assert len(store.bookings()) == 2
    assert store.locations() == [
        {"id": "lager", "name": "Lager"},
        {"id": "verkaufsschrank", "name": "Verkaufsschrank"},
    ]
    assert store.settings["ai"]["api_key"] == "sk-test"

    # Bilder und Bestände überstehen die Migration.
    schild = store.get_raw_product("LAH3K1")
    assert schild is not None
    assert schild["images"] == [{"name": "LAH3K1-01.jpg", "is_main": True}]
    assert schild["stock"] == {"lager": 3, "verkaufsschrank": 1}

    # Auch auf Platte steht weiterhin der vollständige Katalog.
    persisted = read_store_file(store_file)
    assert persisted["version"] == STORAGE_VERSION
    assert persisted["minor_version"] == STORAGE_MINOR_VERSION
    assert persisted["data"]["schemaVersion"] == SCHEMA_VERSION
    assert len(persisted["data"]["products"]) == 3
    assert len(persisted["data"]["bookings"]) == 2

    # Und ein beliebiger Schreibvorgang nach dem Upgrade bleibt vollständig.
    await store.async_save()
    assert len(read_store_file(store_file)["data"]["products"]) == 3


@pytest.mark.parametrize("legacy_tag", ["0.1.7", "0.1.8"])
async def test_snapshot_is_written_before_migration(
    hass_instance: HomeAssistant, tmp_path: Path, legacy_tag: str
) -> None:
    """Vor der Schema-Migration liegt der unveränderte Altstand in ``backups/``."""

    envelope, payload_factory = LEGACY_FILES[legacy_tag]
    payload = payload_factory()
    data_dir = tmp_path / "data"
    store_file = write_store_file(data_dir, payload, **envelope)
    original = store_file.read_bytes()

    await load_store(hass_instance, data_dir)

    snapshots = sorted((data_dir / BACKUP_DIR_NAME).glob("*-pre-load-v1.json"))
    assert len(snapshots) == 1
    assert snapshots[0].read_bytes() == original
    assert read_store_file(store_file)["data"]["schemaVersion"] == SCHEMA_VERSION


async def test_snapshot_before_first_write_after_load(
    hass_instance: HomeAssistant, tmp_path: Path
) -> None:
    """Der erste Schreibvorgang nach dem Laden sichert den vorhandenen Stand."""

    data_dir = tmp_path / "data"
    store_file = write_store_file(
        data_dir, schema2_payload(), version=STORAGE_VERSION, minor_version=STORAGE_MINOR_VERSION
    )
    original = store_file.read_bytes()

    store = await load_store(hass_instance, data_dir)
    backup_dir = data_dir / BACKUP_DIR_NAME
    assert not list(backup_dir.glob("*.json"))

    await store.async_save()
    snapshots = sorted(backup_dir.glob("*-pre-save.json"))
    assert len(snapshots) == 1
    assert snapshots[0].read_bytes() == original

    # Ein weiterer Schreibvorgang mit identischem Altstand erzeugt keinen doppelten Snapshot.
    await store.async_save()
    assert len(list(backup_dir.glob("*-pre-save.json"))) == 1


async def test_empty_payload_never_overwrites_products(
    hass_instance: HomeAssistant, tmp_path: Path
) -> None:
    """Sicherheitsnetz: 3 Produkte auf Platte werden nicht durch 0 Produkte ersetzt."""

    data_dir = tmp_path / "data"
    store_file = write_store_file(
        data_dir, schema2_payload(), version=STORAGE_VERSION, minor_version=STORAGE_MINOR_VERSION
    )
    store = await load_store(hass_instance, data_dir)
    assert len(store.product_dicts()) == 3

    store.data["products"].clear()

    with pytest.raises(StoreError) as err:
        await store.async_save()
    assert err.value.code == "refuse_empty_overwrite"

    # Die Datei bleibt unangetastet.
    assert len(read_store_file(store_file)["data"]["products"]) == 3


async def test_empty_payload_allowed_when_explicitly_confirmed(
    hass_instance: HomeAssistant, tmp_path: Path
) -> None:
    """Ein bewusstes Leeren (``allow_empty``) bleibt möglich."""

    data_dir = tmp_path / "data"
    store_file = write_store_file(
        data_dir, schema2_payload(), version=STORAGE_VERSION, minor_version=STORAGE_MINOR_VERSION
    )
    store = await load_store(hass_instance, data_dir)
    store.data["products"].clear()
    await store.async_save(allow_empty=True)
    assert read_store_file(store_file)["data"]["products"] == []


async def test_unreadable_product_aborts_instead_of_dropping(
    hass_instance: HomeAssistant, tmp_path: Path
) -> None:
    """``coerce_payload`` verwirft keine Produkte mehr stillschweigend."""

    payload = schema2_payload()
    payload["products"][1]["category"] = "XX"
    data_dir = tmp_path / "data"
    store_file = write_store_file(
        data_dir, payload, version=STORAGE_VERSION, minor_version=STORAGE_MINOR_VERSION
    )
    original = store_file.read_bytes()

    with pytest.raises(StoreError) as err:
        await load_store(hass_instance, data_dir)
    assert err.value.code == "products_unreadable"
    assert store_file.read_bytes() == original


async def test_payload_schema_wins_over_envelope_version(
    hass_instance: HomeAssistant, tmp_path: Path
) -> None:
    """Die Migration richtet sich nach ``data.schemaVersion``, nicht nach ``version``.

    Eine Datei mit Envelope-Version 1 und bereits migriertem Payload (Schema 2)
    darf nicht erneut migriert werden.
    """

    payload = schema2_payload()
    data_dir = tmp_path / "data"
    store_file = write_store_file(data_dir, payload, version=1, minor_version=1)

    store = await load_store(hass_instance, data_dir)

    assert len(store.product_dicts()) == 3
    assert store.get_raw_product("LAH3K1")["stock"] == {  # type: ignore[index]
        "lager": 3,
        "verkaufsschrank": 1,
    }
    # Kein Migrationsschreiben: der Stand bleibt unverändert.
    assert read_store_file(store_file)["version"] == 1
    assert not list((data_dir / BACKUP_DIR_NAME).glob("*-pre-load-v*.json"))
