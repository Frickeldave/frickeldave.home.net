"""Persistenzschicht: HA-Store, Bildablage, Migrationen und Snapshots.

Metadaten liegen als JSON unter ``<data_dir>/frickeldave_store.data`` (HA-``Store``-
Helper, atomar, versioniert), Bilder unter ``<data_dir>/images/<artikelnummer>/``
und Snapshots unter ``<data_dir>/backups/``. ``data_dir`` wird von
:mod:`custom_components.frickeldave_store.storage` aufgelöst und ist standardmäßig
``config/frickeldave_store`` (im HA-Backup enthalten).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .const import (
    BACKUP_DIR_NAME,
    DATA_DIR_NAME,
    DEFAULT_AI_ENDPOINT,
    DEFAULT_AI_MODEL,
    DEFAULT_AI_PROMPT,
    DEFAULT_PUBLISH_DEV_BRANCH,
    DEFAULT_PUBLISH_PROD_BRANCH,
    DEFAULT_PUBLISH_PROD_WORKFLOW,
    DEFAULT_PUBLISH_REPOSITORY,
    IMAGES_DIR_NAME,
    STORAGE_KEY,
    STORAGE_MINOR_VERSION,
    STORAGE_VERSION,
)
from .models import (
    IMAGE_SUFFIXES,
    SCHEMA_VERSION,
    ConflictError,
    Image,
    NotFoundError,
    Product,
    StockBooking,
    StoreError,
    ValidationError,
    default_locations,
    location_totals,
    normalise_stock_map,
    normalise_tags,
    to_iso,
    validate_locations,
)

_LOGGER = logging.getLogger(__name__)

#: Erlaubte Bildformate für Uploads.
ALLOWED_CONTENT_TYPES: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

MAX_IMAGE_BYTES = 10 * 1024 * 1024


def default_settings() -> dict[str, Any]:
    """Standard-Einstellungen inklusive KI-Anbieter und Veröffentlichung."""

    return {
        "ai": {
            "endpoint": DEFAULT_AI_ENDPOINT,
            "model": DEFAULT_AI_MODEL,
            "prompt": DEFAULT_AI_PROMPT,
            "api_key": "",
        },
        "publish": {
            "repository": DEFAULT_PUBLISH_REPOSITORY,
            "dev_branch": DEFAULT_PUBLISH_DEV_BRANCH,
            "prod_branch": DEFAULT_PUBLISH_PROD_BRANCH,
            "prod_workflow": DEFAULT_PUBLISH_PROD_WORKFLOW,
            "token": "",
        },
    }


def new_payload() -> dict[str, Any]:
    """Leerer, gültiger Store-Inhalt."""

    return {
        "schemaVersion": SCHEMA_VERSION,
        "locations": default_locations(),
        "products": [],
        "tags": [],
        "bookings": [],
        "settings": default_settings(),
        "updatedAt": to_iso(),
    }


def _schema_version(payload: dict[str, Any]) -> int:
    """Payload-Schema lesen; unversionierte Altbestände gelten als Schema 0."""

    try:
        return int(payload.get("schemaVersion", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _product_count(payload: dict[str, Any] | None) -> int:
    """Anzahl der Produkte in einem (rohen) Payload."""

    if not payload:
        return 0
    products = payload.get("products")
    if isinstance(products, (list, tuple)):
        return len(products)
    return 0


def _migrate_0_to_1(payload: dict[str, Any]) -> dict[str, Any]:
    """Schema 0 (unversioniert) auf Schema 1 heben."""

    products: list[dict[str, Any]] = []
    for raw in payload.get("products", []) or []:
        product = Product.from_dict(raw)
        products.append(product.to_dict())
    return {
        "schemaVersion": 1,
        "locations": default_locations(),
        "products": products,
        "tags": normalise_tags(payload.get("tags", [])),
        "bookings": [
            StockBooking.from_dict(item).to_dict() for item in payload.get("bookings", [])
        ],
        "settings": {**default_settings(), **(payload.get("settings") or {})},
        "updatedAt": payload.get("updatedAt") or to_iso(),
    }


def _migrate_1_to_2(payload: dict[str, Any]) -> dict[str, Any]:
    """Bestände von ``stock``/``salesCabinet`` auf zwei Lagerstätten abbilden.

    Verlustfrei: ``stock`` wird Bestand der ersten Lagerstätte („Lager“),
    ``salesCabinet`` der zweiten („Verkaufsschrank“). Buchungen erhalten die
    Lagerstätte, auf die sie sich bisher implizit bezogen haben.
    """

    locations = default_locations()
    location_ids = [item["id"] for item in locations]
    products = [
        Product.from_dict(raw, location_ids=location_ids).to_dict()
        for raw in payload.get("products", []) or []
    ]
    bookings: list[dict[str, Any]] = []
    for raw in payload.get("bookings", []) or []:
        booking = StockBooking.from_dict(raw)
        if not booking.location_id:
            booking.location_id = location_ids[0]
        if booking.type == "transfer" and not booking.target_location_id and len(location_ids) > 1:
            booking.target_location_id = location_ids[1]
        bookings.append(booking.to_dict())
    return {
        "schemaVersion": 2,
        "locations": locations,
        "products": products,
        "tags": normalise_tags(payload.get("tags", [])),
        "bookings": bookings,
        "settings": {**default_settings(), **(payload.get("settings") or {})},
        "updatedAt": payload.get("updatedAt") or to_iso(),
    }


def migrate_payload(payload: dict[str, Any], from_version: int) -> dict[str, Any]:
    """Payload idempotent auf :data:`SCHEMA_VERSION` migrieren."""

    if from_version > SCHEMA_VERSION:
        raise StoreError(
            f"Store-Schema {from_version} ist neuer als die Integration "
            f"({SCHEMA_VERSION}). Bitte die Integration aktualisieren.",
            code="schema_too_new",
        )
    data = dict(payload)
    version = from_version
    while version < SCHEMA_VERSION:
        if version == 0:
            data = _migrate_0_to_1(data)
        elif version == 1:
            data = _migrate_1_to_2(data)
        version += 1
        data["schemaVersion"] = version
    return coerce_payload(data)


def coerce_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Fehlende Schlüssel ergänzen und Grundtypen sicherstellen.

    Nicht lesbare Produkte werden **nicht** mehr stillschweigend verworfen
    (Issue #289): ein einzelner Skip hätte den Katalog beim anschließenden
    Speichern verkleinert. Stattdessen schlägt der Ladevorgang laut fehl und die
    Store-Datei bleibt unangetastet.
    """

    base = new_payload()
    if not payload:
        return base
    settings = payload.get("settings") or {}
    ai_settings = {**base["settings"]["ai"], **(settings.get("ai") or {})}
    publish_settings = {**base["settings"]["publish"], **(settings.get("publish") or {})}
    locations = _coerce_locations(payload.get("locations"))
    location_ids = [str(item["id"]) for item in locations]
    raw_products = payload.get("products") or []
    products: list[dict[str, Any]] = []
    skipped: list[str] = []
    for raw in raw_products:
        try:
            products.append(
                Product.from_dict(raw, location_ids=location_ids, strict=False).to_dict()
            )
        except (StoreError, ValueError, TypeError, AttributeError) as err:
            identifier = raw.get("articleNumber") if isinstance(raw, dict) else raw
            _LOGGER.error("Produkt nicht lesbar (%s): %s", identifier, err)
            skipped.append(str(identifier))
    if skipped:
        raise StoreError(
            f"{len(skipped)} von {len(raw_products)} Produkten sind nicht lesbar "
            f"({', '.join(skipped[:5])}). Der Store wird nicht überschrieben; bitte die "
            "Store-Datei prüfen oder einen Snapshot aus backups/ einspielen.",
            code="products_unreadable",
        )
    bookings: list[dict[str, Any]] = []
    for raw in payload.get("bookings", []) or []:
        booking = StockBooking.from_dict(raw)
        if not booking.location_id:
            booking.location_id = location_ids[0]
        if booking.type == "transfer" and not booking.target_location_id and len(location_ids) > 1:
            booking.target_location_id = location_ids[1]
        bookings.append(booking.to_dict())
    return {
        "schemaVersion": SCHEMA_VERSION,
        "locations": locations,
        "products": products,
        "tags": normalise_tags(payload.get("tags", [])),
        "bookings": bookings,
        "settings": {**settings, "ai": ai_settings, "publish": publish_settings},
        "updatedAt": payload.get("updatedAt") or to_iso(),
    }


def _coerce_locations(raw: Any) -> list[dict[str, str]]:
    """Gespeicherte Lagerstätten prüfen; defekte Angaben fallen auf den Standard zurück."""

    try:
        return validate_locations(raw if raw else default_locations())
    except ValidationError as err:
        _LOGGER.warning("Lagerstätten ungültig (%s), Standard wird verwendet", err.message)
        return default_locations()


class StorageBackend(Protocol):
    """Abstraktion über HA-Store und einfacher Dateiablage (CLI/Tests)."""

    @property
    def path(self) -> Path:
        """Dateipfad der Metadaten (für Snapshots)."""

    async def async_load(self) -> dict[str, Any] | None: ...

    async def async_save(self, data: dict[str, Any]) -> None: ...


@dataclass
class FileBackend:
    """Atomare JSON-Dateiablage ohne Home-Assistant-Abhängigkeit."""

    _path: Path

    @property
    def path(self) -> Path:
        return self._path

    async def async_load(self) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._load_sync)

    def _load_sync(self) -> dict[str, Any] | None:
        if not self._path.exists():
            return None
        with self._path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        return loaded if isinstance(loaded, dict) else None

    async def async_save(self, data: dict[str, Any]) -> None:
        await asyncio.to_thread(self._save_sync, data)

    def _save_sync(self, data: dict[str, Any]) -> None:
        write_json_atomic(self._path, data)


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    """JSON atomar schreiben (temporäre Datei + ``os.replace``)."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


class HassStoreBackend:
    """Datei-Backend im HA-Datenordner – ohne ``homeassistant.helpers.storage``.

    Issue #289: ``Store`` konsultiert vor dem Lesen den internen
    ``_StoreManager``-Cache. Der Cache kennt nur Dateien aus
    ``<config>/.storage`` und meldet für jeden anderen Schlüssel „existiert
    nicht“ (``async_fetch`` → ``(False, None)``). Der Katalog liegt aber in
    ``<config>/frickeldave_store/``: ``Store.async_load()`` lieferte deshalb
    ``None``, die Integration startete mit einem leeren Katalog und der nächste
    ``async_save`` überschrieb die echte Datei mit null Produkten.

    Dieses Backend liest und schreibt die Datei selbst. Das Format bleibt
    identisch zum HA-Store (``{"version", "minor_version", "key", "data"}``),
    damit bestehende Dateien und Snapshots unverändert funktionieren. Maßgeblich
    für die Migration ist ``data.schemaVersion``; die Envelope-Version wird nur
    protokolliert.
    """

    def __init__(self, hass: Any, data_dir: Path, snapshot_dir: Path) -> None:
        self._hass = hass
        self._data_dir = Path(data_dir)
        self._snapshot_dir = Path(snapshot_dir)

    @property
    def path(self) -> Path:
        return self._data_dir / STORAGE_KEY

    async def async_load(self) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._load_sync)

    async def async_save(self, data: dict[str, Any]) -> None:
        envelope = {
            "version": STORAGE_VERSION,
            "minor_version": STORAGE_MINOR_VERSION,
            "key": STORAGE_KEY,
            "data": data,
        }
        await asyncio.to_thread(write_json_atomic, self.path, envelope)

    def _load_sync(self) -> dict[str, Any] | None:
        path = self.path
        if not path.is_file():
            return None
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as err:
            raise StoreError(
                f"Store-Datei '{path}' ist nicht lesbar: {err}. Die Datei bleibt "
                "unverändert; bitte einen Snapshot aus backups/ einspielen.",
                code="storage_unreadable",
            ) from err
        if not isinstance(loaded, dict):
            raise StoreError(
                f"Store-Datei '{path}' enthält kein JSON-Objekt.",
                code="storage_unreadable",
            )
        payload = loaded.get("data")
        if not isinstance(payload, dict):
            # Datei ohne HA-Umschlag (ältere Ablage oder kopierter Snapshot).
            payload = {
                key: value
                for key, value in loaded.items()
                if key not in {"version", "minor_version", "key"}
            }
        stored_version = loaded.get("version")
        schema_version = payload.get("schemaVersion")
        if (
            stored_version is not None
            and schema_version is not None
            and str(stored_version) != str(schema_version)
        ):
            _LOGGER.info(
                "Store-Datei meldet Version %s, Payload-Schema %s – maßgeblich ist "
                "das Payload-Schema.",
                stored_version,
                schema_version,
            )
        return payload

    def _snapshot_sync(self, reason: str) -> Path | None:
        return copy_snapshot(self.path, self._snapshot_dir, reason)


def copy_snapshot(
    source: Path, target_dir: Path, reason: str, *, skip_if_identical: bool = False
) -> Path | None:
    """Aktuelle Store-Datei nach ``backups/`` kopieren (vor Migration/Restore).

    ``skip_if_identical`` verhindert, dass wiederholte Starts dutzende gleiche
    Snapshots ablegen (der Inhalt der Datei ändert sich beim Laden nicht).
    """

    if not source.exists():
        return None
    target_dir.mkdir(parents=True, exist_ok=True)
    if skip_if_identical:
        data = source.read_bytes()
        for existing in sorted(target_dir.glob(f"*-{reason}.json"), reverse=True):
            if existing.read_bytes() == data:
                return existing
    stamp = to_iso().replace(":", "-")
    target = target_dir / f"{stamp}-{reason}.json"
    shutil.copy2(source, target)
    return target


class ImageStorage:
    """Bilddateien unter ``images/<artikelnummer>/``."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    def product_dir(self, article_number: str) -> Path:
        return self._base_dir / article_number

    def path_for(self, article_number: str, name: str) -> Path:
        safe = Path(name).name
        return self.product_dir(article_number) / safe

    async def async_list(self, article_number: str) -> list[str]:
        return await asyncio.to_thread(self._list_sync, article_number)

    def _list_sync(self, article_number: str) -> list[str]:
        directory = self.product_dir(article_number)
        if not directory.is_dir():
            return []
        return sorted(
            child.name
            for child in directory.iterdir()
            if child.is_file() and child.suffix.casefold() in IMAGE_SUFFIXES
        )

    async def async_write(self, article_number: str, name: str, data: bytes) -> Path:
        return await asyncio.to_thread(self._write_sync, article_number, name, data)

    def _write_sync(self, article_number: str, name: str, data: bytes) -> Path:
        target = self.path_for(article_number, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, tmp_name = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
        return target

    async def async_read(self, article_number: str, name: str) -> bytes:
        return await asyncio.to_thread(self.path_for(article_number, name).read_bytes)

    async def async_delete(self, article_number: str, name: str) -> bool:
        return await asyncio.to_thread(self._delete_sync, article_number, name)

    def _delete_sync(self, article_number: str, name: str) -> bool:
        target = self.path_for(article_number, name)
        if not target.is_file():
            return False
        target.unlink()
        directory = target.parent
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
        return True

    async def async_copy_to(self, article_number: str, source_name: str, target: Path) -> None:
        await asyncio.to_thread(self._copy_sync, article_number, source_name, target)

    def _copy_sync(self, article_number: str, source_name: str, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.path_for(article_number, source_name), target)


class CatalogStore:
    """Fachlicher Zugriff auf den Store-Inhalt inklusive Bildablage."""

    def __init__(
        self,
        hass: Any | None = None,
        *,
        data_dir: Path | None = None,
        backend: StorageBackend | None = None,
    ) -> None:
        if data_dir is None:
            if hass is None:
                raise ValueError("data_dir oder hass ist erforderlich")
            data_dir = Path(hass.config.path(DATA_DIR_NAME))
        self.data_dir = Path(data_dir)
        self.images = ImageStorage(self.data_dir / IMAGES_DIR_NAME)
        self.backup_dir = self.data_dir / BACKUP_DIR_NAME
        if backend is None:
            if hass is None:
                backend = FileBackend(self.data_dir / STORAGE_KEY)
            else:
                backend = HassStoreBackend(hass, self.data_dir, self.backup_dir)
        self._backend = backend
        self._data: dict[str, Any] = new_payload()
        self._lock = asyncio.Lock()
        self._listeners: list[Callable[[], None]] = []
        #: Produktanzahl des zuletzt gelesenen bzw. geschriebenen Stands.
        #: Sicherheitsnetz gegen stillen Datenverlust (Issue #289).
        self._disk_product_count = 0
        #: ``True``, solange vor dem ersten Schreiben noch ein Snapshot fehlt.
        self._needs_pre_save_snapshot = False

    # ------------------------------------------------------------------ Laden/Speichern
    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Listener für Panel-Updates registrieren; gibt die Abmeldefunktion zurück."""

        self._listeners.append(listener)

        def _remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    async def async_load(self) -> None:
        raw = await self._backend.async_load()
        self._disk_product_count = _product_count(raw)
        # Ein vorhandener Stand wird vor dem ersten Schreiben gesichert.
        self._needs_pre_save_snapshot = raw is not None
        if raw is None:
            self._data = new_payload()
            return
        version = _schema_version(raw)
        if version > SCHEMA_VERSION:
            raise StoreError(
                f"Gespeichertes Schema {version} ist neuer als {SCHEMA_VERSION}.",
                code="schema_too_new",
            )
        if version < SCHEMA_VERSION:
            await asyncio.to_thread(self.async_snapshot_sync, f"pre-load-v{version}")
            self._needs_pre_save_snapshot = False
            self._data = migrate_payload(raw, version)
            await self.async_save()
        else:
            self._data = coerce_payload(raw)

    async def async_save(self, *, allow_empty: bool = False) -> None:
        """Store schreiben.

        Sicherheitsnetz (Issue #289): ein Stand mit null Produkten überschreibt
        niemals eine Datei, die Produkte enthält – außer der Aufrufer bestätigt
        das ausdrücklich (``allow_empty=True``). Die Datei bleibt im Zweifel
        unverändert.
        """

        self._data["schemaVersion"] = SCHEMA_VERSION
        self._data["updatedAt"] = to_iso()
        if not allow_empty and self._disk_product_count > 0 and not self._data["products"]:
            _LOGGER.error(
                "Schreiben abgebrochen: Store enthielt %s Produkte, neuer Stand hat 0. "
                "Die Store-Datei bleibt unverändert.",
                self._disk_product_count,
            )
            raise StoreError(
                "Sicherheitsnetz: Der gespeicherte Katalog enthält "
                f"{self._disk_product_count} Produkte, der neue Stand 0. "
                "Es wurde nichts geschrieben; die Store-Datei bleibt unverändert.",
                code="refuse_empty_overwrite",
            )
        if self._needs_pre_save_snapshot:
            await asyncio.to_thread(self._snapshot_before_first_write)
            self._needs_pre_save_snapshot = False
        await self._backend.async_save(self._data)
        self._disk_product_count = len(self._data["products"])
        self._notify()

    def _snapshot_before_first_write(self) -> Path | None:
        return copy_snapshot(
            self._backend.path, self.backup_dir, "pre-save", skip_if_identical=True
        )

    async def async_snapshot(self, reason: str = "manual") -> Path | None:
        return await asyncio.to_thread(self.async_snapshot_sync, reason)

    def async_snapshot_sync(self, reason: str) -> Path | None:
        return copy_snapshot(self._backend.path, self.backup_dir, reason)

    def _notify(self) -> None:
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:  # pragma: no cover - Listener dürfen den Store nicht brechen
                _LOGGER.exception("Store-Listener fehlgeschlagen")

    # ------------------------------------------------------------------ Produkte
    def product_dicts(self) -> list[dict[str, Any]]:
        return self._data["products"]

    def products(self) -> list[Product]:
        return [Product.from_dict(item) for item in self._data["products"]]

    def get_product(self, article_number: str, *, include_archived: bool = False) -> Product:
        raw = self.get_raw_product(article_number)
        if raw is None or (raw.get("archived") and not include_archived):
            raise NotFoundError(f"Produkt {article_number} nicht gefunden.")
        return Product.from_dict(raw)

    def get_raw_product(self, article_number: str) -> dict[str, Any] | None:
        key = article_number.upper()
        for item in self._data["products"]:
            if item.get("articleNumber") == key:
                return item
        return None

    def put_product(self, product: Product) -> Product:
        product.updated_at = to_iso()
        raw = product.to_dict()
        for index, item in enumerate(self._data["products"]):
            if item.get("articleNumber") == product.article_number:
                self._data["products"][index] = raw
                break
        else:
            self._data["products"].append(raw)
        return product

    def article_numbers(self) -> set[str]:
        return {str(item.get("articleNumber", "")) for item in self._data["products"]}

    # ------------------------------------------------------------------ Tags
    def tag_names(self) -> list[str]:
        return list(self._data["tags"])

    def add_tag(self, name: str) -> str:
        cleaned = " ".join(name.split())
        if not cleaned:
            raise ValidationError("Tag-Name darf nicht leer sein.")
        if cleaned.casefold() not in {tag.casefold() for tag in self._data["tags"]}:
            self._data["tags"].append(cleaned)
            self._data["tags"].sort(key=str.casefold)
        return cleaned

    def remove_tag(self, name: str) -> None:
        key = name.casefold()
        tags = [tag for tag in self._data["tags"] if tag.casefold() != key]
        if len(tags) == len(self._data["tags"]):
            raise NotFoundError(f"Tag {name} nicht gefunden.")
        self._data["tags"] = tags
        now = to_iso()
        for item in self._data["products"]:
            if any(str(tag).casefold() == key for tag in item.get("tags", [])):
                item["tags"] = [tag for tag in item.get("tags", []) if str(tag).casefold() != key]
                item["updatedAt"] = now

    # ------------------------------------------------------------------ Lagerstätten
    def locations(self) -> list[dict[str, str]]:
        """Konfigurierte Lagerstätten in Anzeigereihenfolge (Kopie)."""

        return [
            {"id": str(item["id"]), "name": str(item["name"])} for item in self._data["locations"]
        ]

    def location_ids(self) -> list[str]:
        return [item["id"] for item in self.locations()]

    def location_names(self) -> list[str]:
        return [item["name"] for item in self.locations()]

    def location_index(self, location_id: str) -> int | None:
        for index, item in enumerate(self._data["locations"]):
            if item["id"] == location_id:
                return index
        return None

    def require_location(self, location_id: str) -> str:
        """Lagerstätte prüfen und ihre ID zurückgeben (deutsche Fehlermeldung)."""

        key = str(location_id or "").strip()
        if not key:
            raise ValidationError("locationId ist ein Pflichtfeld.")
        if self.location_index(key) is None:
            names = ", ".join(self.location_names())
            raise ValidationError(f"Unbekannte Lagerstätte: {key!r}. Vorhanden: {names}.")
        return key

    def default_location_id(self) -> str:
        return str(self._data["locations"][0]["id"])

    def location_usage(self) -> dict[str, tuple[int, int]]:
        """Je Lagerstätte: (Produkte mit Bestand, Summe der Stücke)."""

        return {
            item["id"]: location_totals(self.product_dicts(), item["id"])
            for item in self._data["locations"]
        }

    def locations_payload(self) -> list[dict[str, Any]]:
        """Antwortformat für Panel und Einstellungen (inkl. Bestandskennzahlen)."""

        usage = self.location_usage()
        return [
            {
                "id": item["id"],
                "name": item["name"],
                "position": index,
                "stock_total": usage[item["id"]][1],
                "products_with_stock": usage[item["id"]][0],
            }
            for index, item in enumerate(self._data["locations"])
        ]

    def validate_location_change(
        self, entries: Any, *, match_by: str = "id"
    ) -> list[dict[str, str]]:
        """Neue Lagerstätten prüfen, ohne etwas zu verändern.

        Eine Löschung wird abgelehnt, solange dort Bestand gebucht ist – sonst
        gingen Stückzahlen beim nächsten Speichern verloren.
        """

        locations = validate_locations(entries, existing_ids=self.location_ids(), match_by=match_by)
        new_ids = {item["id"] for item in locations}
        removed = [item for item in self.locations() if item["id"] not in new_ids]
        for item in removed:
            affected, pieces = location_totals(self.product_dicts(), item["id"])
            if pieces > 0:
                raise ConflictError(
                    f"Die Lagerstätte „{item['name']}“ kann nicht gelöscht werden: "
                    f"{affected} Produkte haben dort noch {pieces} Stück Bestand. "
                    "Bitte den Bestand zuerst auf eine andere Lagerstätte umbuchen."
                )
        return locations

    def set_locations(self, entries: Any, *, match_by: str = "id") -> list[dict[str, str]]:
        """Lagerstätten übernehmen; IDs bleiben beim Umbenennen erhalten."""

        locations = self.validate_location_change(entries, match_by=match_by)
        new_ids = [entry["id"] for entry in locations]
        removed_ids = {item["id"] for item in self.locations()} - set(new_ids)
        for item in self.product_dicts():
            stock = item.get("stock")
            if not isinstance(stock, dict):
                stock = normalise_stock_map(stock, new_ids)
                item["stock"] = stock
            for location_id in removed_ids:
                # Nur zulässig, wenn dort kein Bestand gebucht ist (siehe Prüfung oben).
                stock.pop(location_id, None)
            for location_id in new_ids:
                stock.setdefault(location_id, 0)
        self._data["locations"] = locations
        return self.locations()

    # ------------------------------------------------------------------ Buchungen
    def bookings(self, article_number: str | None = None) -> list[dict[str, Any]]:
        if article_number is None:
            return list(self._data["bookings"])
        key = article_number.upper()
        return [item for item in self._data["bookings"] if item.get("articleNumber") == key]

    def add_booking(self, booking: StockBooking) -> StockBooking:
        self._data["bookings"].append(booking.to_dict())
        return booking

    # ------------------------------------------------------------------ Einstellungen
    @property
    def settings(self) -> dict[str, Any]:
        return self._data["settings"]

    def update_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        ai_changes = changes.get("ai")
        if ai_changes is not None:
            if not isinstance(ai_changes, dict):
                raise ValidationError("ai muss ein Objekt sein.")
            allowed = {"endpoint", "model", "prompt", "api_key"}
            unknown = set(ai_changes) - allowed
            if unknown:
                raise ValidationError(f"Unbekannte ai-Felder: {', '.join(sorted(unknown))}")
            self._data["settings"]["ai"].update(ai_changes)
        publish_changes = changes.get("publish")
        if publish_changes is not None:
            if not isinstance(publish_changes, dict):
                raise ValidationError("publish muss ein Objekt sein.")
            allowed_publish = {"repository", "dev_branch", "prod_branch", "prod_workflow", "token"}
            unknown_publish = set(publish_changes) - allowed_publish
            if unknown_publish:
                raise ValidationError(
                    f"Unbekannte publish-Felder: {', '.join(sorted(unknown_publish))}"
                )
            for key, value in publish_changes.items():
                if key == "token":
                    self._data["settings"]["publish"]["token"] = str(value or "")
                    continue
                text = str(value or "").strip()
                if not text:
                    raise ValidationError(f"publish.{key} darf nicht leer sein.")
                self._data["settings"]["publish"][key] = text
        return self._data["settings"]

    def public_settings(self) -> dict[str, Any]:
        """Einstellungen ohne Geheimnisse (API-Key und Token nur als Flag)."""

        ai_settings = self._data["settings"]["ai"]
        publish_settings = self._data["settings"].get("publish", {})
        return {
            "ai": {
                "endpoint": ai_settings.get("endpoint", ""),
                "model": ai_settings.get("model", ""),
                "prompt": ai_settings.get("prompt", ""),
                "api_key_set": bool(ai_settings.get("api_key")),
            },
            "publish": {
                "repository": publish_settings.get("repository", ""),
                "dev_branch": publish_settings.get("dev_branch", ""),
                "prod_branch": publish_settings.get("prod_branch", ""),
                "prod_workflow": publish_settings.get("prod_workflow", ""),
                "token_set": bool(publish_settings.get("token")),
            },
        }


def image_entry(name: str, is_main: bool) -> Image:
    """Hilfsfunktion für Tests und Aufrufer."""

    return Image(name=name, is_main=is_main)
