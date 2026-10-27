"""Persistenzschicht: HA-Store, Bildablage, Migrationen und Snapshots.

Metadaten liegen als JSON unter ``config/frickeldave_store/`` (HA-``Store``-Helper,
atomar, versioniert). Bilder liegen als Dateien unter
``config/frickeldave_store/images/<artikelnummer>/``. Beide Pfade sind Teil des
HA-Container-Backups.
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
    IMAGES_DIR_NAME,
    STORAGE_KEY,
    STORAGE_MINOR_VERSION,
    STORAGE_PATH,
    STORAGE_VERSION,
)
from .models import (
    IMAGE_SUFFIXES,
    SCHEMA_VERSION,
    Image,
    NotFoundError,
    Product,
    StockBooking,
    StoreError,
    ValidationError,
    normalise_tags,
    to_iso,
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
    """Standard-Einstellungen inklusive KI-Anbieter."""

    return {
        "ai": {
            "endpoint": DEFAULT_AI_ENDPOINT,
            "model": DEFAULT_AI_MODEL,
            "prompt": DEFAULT_AI_PROMPT,
            "api_key": "",
        }
    }


def new_payload() -> dict[str, Any]:
    """Leerer, gültiger Store-Inhalt."""

    return {
        "schemaVersion": SCHEMA_VERSION,
        "products": [],
        "tags": [],
        "bookings": [],
        "settings": default_settings(),
        "updatedAt": to_iso(),
    }


def _migrate_0_to_1(payload: dict[str, Any]) -> dict[str, Any]:
    """Schema 0 (unversioniert) auf Schema 1 heben."""

    products: list[dict[str, Any]] = []
    for raw in payload.get("products", []) or []:
        product = Product.from_dict(raw)
        products.append(product.to_dict())
    return {
        "schemaVersion": 1,
        "products": products,
        "tags": normalise_tags(payload.get("tags", [])),
        "bookings": [
            StockBooking.from_dict(item).to_dict() for item in payload.get("bookings", [])
        ],
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
        version += 1
        data["schemaVersion"] = version
    return coerce_payload(data)


def coerce_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Fehlende Schlüssel ergänzen und Grundtypen sicherstellen."""

    base = new_payload()
    if not payload:
        return base
    settings = payload.get("settings") or {}
    ai_settings = {**base["settings"]["ai"], **(settings.get("ai") or {})}
    products: list[dict[str, Any]] = []
    for raw in payload.get("products", []) or []:
        try:
            products.append(Product.from_dict(raw).to_dict())
        except (ValidationError, StoreError) as err:  # pragma: no cover - defensive
            _LOGGER.warning("Produkt übersprungen (%s): %s", raw.get("articleNumber"), err)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "products": products,
        "tags": normalise_tags(payload.get("tags", [])),
        "bookings": [
            StockBooking.from_dict(item).to_dict() for item in payload.get("bookings", []) or []
        ],
        "settings": {**settings, "ai": ai_settings},
        "updatedAt": payload.get("updatedAt") or to_iso(),
    }


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
    """HA-``Store``-Backend mit Migration und Snapshot vor der Migration."""

    def __init__(self, hass: Any, snapshot_dir: Path) -> None:
        self._hass = hass
        self._snapshot_dir = snapshot_dir
        self._store = self._build_store()

    @property
    def path(self) -> Path:
        return Path(self._hass.config.path(STORAGE_PATH, STORAGE_KEY))

    def _build_store(self) -> Any:
        # Import im Methodenkörper: Tests und das Export-CLI nutzen die reine Dateiablage.
        import inspect

        from homeassistant.helpers.storage import Store

        backend = self
        storage_path = Path(self._hass.config.path(STORAGE_PATH))
        storage_file = storage_path / STORAGE_KEY
        supports_path = "path" in inspect.signature(Store.__init__).parameters

        class _MigratingStore(Store):  # type: ignore[misc, valid-type]
            async def _async_migrate_func(
                self,
                old_major_version: int,
                old_minor_version: int,
                old_data: dict[str, Any],
            ) -> dict[str, Any]:
                return await backend._async_migrate(old_major_version, old_data)

        if supports_path:
            return _MigratingStore(
                self._hass,
                STORAGE_VERSION,
                STORAGE_KEY,
                minor_version=STORAGE_MINOR_VERSION,
                atomic_writes=True,
                path=str(storage_path),
            )

        class _PathStore(_MigratingStore):  # type: ignore[misc, valid-type]
            @property
            def path(self) -> str:
                return str(storage_file)

        return _PathStore(
            self._hass,
            STORAGE_VERSION,
            STORAGE_KEY,
            minor_version=STORAGE_MINOR_VERSION,
            atomic_writes=True,
        )

    async def _async_migrate(
        self, old_major_version: int, old_data: dict[str, Any]
    ) -> dict[str, Any]:
        await asyncio.to_thread(self._snapshot_sync, f"pre-migration-v{old_major_version}")
        return migrate_payload(old_data, old_major_version)

    async def async_load(self) -> dict[str, Any] | None:
        loaded = await self._store.async_load()
        return loaded if isinstance(loaded, dict) else None

    async def async_save(self, data: dict[str, Any]) -> None:
        await self._store.async_save(data)

    def _snapshot_sync(self, reason: str) -> Path | None:
        return copy_snapshot(self.path, self._snapshot_dir, reason)


def copy_snapshot(source: Path, target_dir: Path, reason: str) -> Path | None:
    """Aktuelle Store-Datei nach ``backups/`` kopieren (vor Migration/Restore)."""

    if not source.exists():
        return None
    target_dir.mkdir(parents=True, exist_ok=True)
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
                backend = HassStoreBackend(hass, self.backup_dir)
        self._backend = backend
        self._data: dict[str, Any] = new_payload()
        self._lock = asyncio.Lock()
        self._listeners: list[Callable[[], None]] = []

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
        if raw is None:
            self._data = new_payload()
            return
        version = int(raw.get("schemaVersion", 0))
        if version > SCHEMA_VERSION:
            raise StoreError(
                f"Gespeichertes Schema {version} ist neuer als {SCHEMA_VERSION}.",
                code="schema_too_new",
            )
        if version < SCHEMA_VERSION:
            await asyncio.to_thread(self.async_snapshot_sync, f"pre-load-v{version}")
            raw = migrate_payload(raw, version)
            self._data = raw
            await self.async_save()
        else:
            self._data = coerce_payload(raw)

    async def async_save(self) -> None:
        self._data["schemaVersion"] = SCHEMA_VERSION
        self._data["updatedAt"] = to_iso()
        await self._backend.async_save(self._data)
        self._notify()

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
        return self._data["settings"]

    def public_settings(self) -> dict[str, Any]:
        """Einstellungen ohne Geheimnisse (API-Key wird nur als Flag gemeldet)."""

        ai_settings = self._data["settings"]["ai"]
        return {
            "ai": {
                "endpoint": ai_settings.get("endpoint", ""),
                "model": ai_settings.get("model", ""),
                "prompt": ai_settings.get("prompt", ""),
                "api_key_set": bool(ai_settings.get("api_key")),
            }
        }


def image_entry(name: str, is_main: bool) -> Image:
    """Hilfsfunktion für Tests und Aufrufer."""

    return Image(name=name, is_main=is_main)
