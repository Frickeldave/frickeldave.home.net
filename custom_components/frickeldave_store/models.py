"""Domänenmodelle und Validierung für ``frickeldave_store``.

Die Modelle sind bewusst frei von Home-Assistant-Abhängigkeiten, damit sie in
reinen Domain-Tests und im Export-CLI ohne HA-Laufzeit verwendet werden können.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

SCHEMA_VERSION: Final = 1
CATEGORIES: Final[tuple[str, ...]] = ("3D", "EP", "LA", "SB", "WO")
CATEGORY_LABELS: Final[dict[str, str]] = {
    "3D": "3D-Druck",
    "EP": "Epoxidharz",
    "LA": "Laser",
    "SB": "Steinmasse",
    "WO": "Holz",
}
MAX_IMAGES: Final = 10
ARTICLE_NUMBER_LENGTH: Final = 6
ARTICLE_NUMBER_RE: Final = re.compile(r"^[A-Z0-9]{6}$")
BOOKING_TYPES: Final[tuple[str, ...]] = ("in", "out", "transfer", "correction")
VISIBILITY_VALUES: Final[tuple[str, ...]] = ("visible", "invisible")
IMAGE_SUFFIXES: Final[tuple[str, ...]] = (".jpg", ".jpeg", ".png", ".webp", ".gif")

PRODUCT_FIELDS: Final[tuple[str, ...]] = (
    "articleNumber",
    "name",
    "description",
    "category",
    "price",
    "size",
    "tags",
    "customizable",
    "visible",
    "stock",
    "salesCabinet",
    "images",
    "archived",
    "createdAt",
    "updatedAt",
    "schemaVersion",
)

#: Felder, die vom Client geschrieben werden dürfen.
WRITABLE_FIELDS: Final[tuple[str, ...]] = (
    "name",
    "description",
    "price",
    "size",
    "tags",
    "customizable",
    "visible",
    "stock",
    "salesCabinet",
)


class StoreError(Exception):
    """Basisklasse aller fachlichen Fehler mit stabilem Fehlercode."""

    code: str = "error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code

    @property
    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class ValidationError(StoreError):
    """Ungültige Eingabedaten."""

    code = "validation_error"


class NotFoundError(StoreError):
    """Angefragtes Objekt existiert nicht."""

    code = "not_found"


class ConflictError(StoreError):
    """Konflikt mit bestehenden Daten (z. B. doppelte Artikelnummer)."""

    code = "conflict"


class ImageLimitError(StoreError):
    """Mehr als zehn Bilder pro Produkt."""

    code = "image_limit"


class PermissionDenied(StoreError):
    """Default Deny: keine ausreichende Rolle."""

    code = "forbidden"


def utc_now() -> datetime:
    """Aktuelle UTC-Zeit (sekundengenau)."""

    return datetime.now(UTC).replace(microsecond=0)


def to_iso(moment: datetime | None = None) -> str:
    """Zeitstempel im ISO-8601-Format mit ``Z``-Suffix."""

    value = (moment or utc_now()).astimezone(UTC)
    return value.isoformat().replace("+00:00", "Z")


def normalise_tags(values: Any) -> list[str]:
    """Tags trimmen, leere entfernen und case-insensitiv deduplizieren."""

    if values is None:
        return []
    if isinstance(values, str):
        values = values.split(",")
    if not isinstance(values, (list, tuple, set)):
        raise ValidationError("Tags müssen eine Liste von Zeichenketten sein.")
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        if not isinstance(raw, str):
            raise ValidationError("Tags müssen Zeichenketten sein.")
        tag = " ".join(raw.split())
        if not tag:
            continue
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(tag)
    return result


def normalise_price(value: Any) -> float:
    """Preis als Dezimalzahl (EUR), nie negativ."""

    if isinstance(value, bool):
        raise ValidationError("Preis muss eine Zahl sein.")
    try:
        price = round(float(value), 2)
    except (TypeError, ValueError) as err:
        raise ValidationError("Preis muss eine Zahl sein.") from err
    if price < 0:
        raise ValidationError("Preis darf nicht negativ sein.")
    if price > 1_000_000:
        raise ValidationError("Preis ist unrealistisch hoch.")
    return price


def normalise_stock(value: Any, field_name: str) -> int:
    """Bestand als ganze Zahl ≥ 0."""

    if isinstance(value, bool):
        raise ValidationError(f"{field_name} muss eine ganze Zahl sein.")
    try:
        stock = int(value)
    except (TypeError, ValueError) as err:
        raise ValidationError(f"{field_name} muss eine ganze Zahl sein.") from err
    if stock < 0:
        raise ValidationError(f"{field_name} darf nicht negativ sein.")
    return stock


def normalise_bool(value: Any) -> bool:
    """Boolesche Werte tolerant aus JSON/Query-Parametern lesen."""

    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().casefold()
        if lowered in {"1", "true", "yes", "ja", "on"}:
            return True
        if lowered in {"0", "false", "no", "nein", "off"}:
            return False
    raise ValidationError(f"Ungültiger Wahrheitswert: {value!r}")


def normalise_text(value: Any, field_name: str, *, max_length: int = 20_000) -> str:
    """Textfeld trimmen und Länge begrenzen."""

    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} muss eine Zeichenkette sein.")
    text = value.strip()
    if len(text) > max_length:
        raise ValidationError(f"{field_name} ist zu lang (max. {max_length} Zeichen).")
    return text


@dataclass(slots=True)
class Image:
    """Bilddatei eines Produkts."""

    name: str
    is_main: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "is_main": self.is_main}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Image:
        return cls(name=str(data.get("name", "")), is_main=bool(data.get("is_main", False)))


@dataclass(slots=True)
class Product:
    """Produkt der Lagerverwaltung (Schema-Version 1)."""

    article_number: str
    name: str
    category: str
    price: float = 0.0
    description: str = ""
    size: str = ""
    tags: list[str] = field(default_factory=list)
    customizable: bool = True
    visible: bool = True
    stock: int = 0
    sales_cabinet: int = 0
    images: list[Image] = field(default_factory=list)
    archived: bool = False
    created_at: str = ""
    updated_at: str = ""
    schema_version: int = SCHEMA_VERSION

    @property
    def main_image(self) -> Image | None:
        for image in self.images:
            if image.is_main:
                return image
        return self.images[0] if self.images else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "articleNumber": self.article_number,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "price": self.price,
            "size": self.size,
            "tags": list(self.tags),
            "customizable": self.customizable,
            "visible": self.visible,
            "stock": self.stock,
            "salesCabinet": self.sales_cabinet,
            "images": [image.to_dict() for image in self.images],
            "archived": self.archived,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "schemaVersion": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Product:
        """Tolerant lesen – ältere Katalogdaten haben weder Bilder noch Zeitstempel."""

        article_number = str(data.get("articleNumber", "")).upper()
        category = str(data.get("category", "")).upper()
        if category not in CATEGORIES:
            raise ValidationError(f"Unbekannte Kategorie: {category!r}")
        images = [Image.from_dict(item) for item in data.get("images", []) or []]
        if len(images) > MAX_IMAGES:
            images = images[:MAX_IMAGES]
        return cls(
            article_number=article_number,
            name=str(data.get("name", "")),
            category=category,
            price=normalise_price(data.get("price", 0)),
            description=str(data.get("description", "") or ""),
            size=str(data.get("size", "") or ""),
            tags=normalise_tags(data.get("tags", [])),
            customizable=bool(data.get("customizable", True)),
            visible=bool(data.get("visible", True)),
            stock=normalise_stock(data.get("stock", 0), "stock"),
            sales_cabinet=normalise_stock(data.get("salesCabinet", 0), "salesCabinet"),
            images=images,
            archived=bool(data.get("archived", False)),
            created_at=str(data.get("createdAt", "") or ""),
            updated_at=str(data.get("updatedAt", "") or ""),
            schema_version=int(data.get("schemaVersion", SCHEMA_VERSION)),
        )


@dataclass(slots=True)
class StockBooking:
    """Bestandsbuchung (Zugang, Abgang, Umlagerung, Korrektur)."""

    id: str
    article_number: str
    type: str
    quantity: int
    note: str = ""
    at: str = ""
    ha_user_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "articleNumber": self.article_number,
            "type": self.type,
            "quantity": self.quantity,
            "note": self.note,
            "at": self.at,
            "ha_user_id": self.ha_user_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StockBooking:
        return cls(
            id=str(data.get("id", "")),
            article_number=str(data.get("articleNumber", "")),
            type=str(data.get("type", "correction")),
            quantity=int(data.get("quantity", 0)),
            note=str(data.get("note", "") or ""),
            at=str(data.get("at", "") or ""),
            ha_user_id=str(data.get("ha_user_id", "") or ""),
        )


def validate_create_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Eingaben für ein neues Produkt prüfen und normalisieren."""

    if not isinstance(payload, dict):
        raise ValidationError("Anfragekörper muss ein JSON-Objekt sein.")

    name = normalise_text(payload.get("name"), "name", max_length=200)
    if not name:
        raise ValidationError("Name ist ein Pflichtfeld.")

    category = str(payload.get("category", "")).upper().strip()
    if category not in CATEGORIES:
        allowed = ", ".join(CATEGORIES)
        raise ValidationError(f"Kategorie muss eine von {allowed} sein.")

    return {
        "name": name,
        "category": category,
        "description": normalise_text(payload.get("description"), "description"),
        "price": normalise_price(payload.get("price", 0)),
        "size": normalise_text(payload.get("size"), "size", max_length=200),
        "tags": normalise_tags(payload.get("tags", [])),
        "customizable": normalise_bool(payload.get("customizable", True)),
        "visible": normalise_bool(payload.get("visible", True)),
        "stock": normalise_stock(payload.get("stock", 0), "stock"),
        "salesCabinet": normalise_stock(payload.get("salesCabinet", 0), "salesCabinet"),
    }


def validate_update_payload(payload: dict[str, Any], *, immutable_category: str) -> dict[str, Any]:
    """Eingaben für ein Produktupdate prüfen; Kategorie und Artikelnummer sind unveränderlich."""

    if not isinstance(payload, dict):
        raise ValidationError("Anfragekörper muss ein JSON-Objekt sein.")

    if "articleNumber" in payload:
        raise ValidationError("Die Artikelnummer ist unveränderlich.")
    if "category" in payload:
        requested = str(payload.get("category", "")).upper().strip()
        if requested != immutable_category:
            raise ValidationError(
                "Die Kategorie ist nach dem Anlegen unveränderlich. "
                "Bitte ein neues Produkt anlegen.",
                code="category_immutable",
            )

    changes: dict[str, Any] = {}
    if "name" in payload:
        name = normalise_text(payload.get("name"), "name", max_length=200)
        if not name:
            raise ValidationError("Name darf nicht leer sein.")
        changes["name"] = name
    if "description" in payload:
        changes["description"] = normalise_text(payload.get("description"), "description")
    if "price" in payload:
        changes["price"] = normalise_price(payload.get("price"))
    if "size" in payload:
        changes["size"] = normalise_text(payload.get("size"), "size", max_length=200)
    if "tags" in payload:
        changes["tags"] = normalise_tags(payload.get("tags"))
    if "customizable" in payload:
        changes["customizable"] = normalise_bool(payload.get("customizable"))
    if "visible" in payload:
        changes["visible"] = normalise_bool(payload.get("visible"))
    if "stock" in payload:
        changes["stock"] = normalise_stock(payload.get("stock"), "stock")
    if "salesCabinet" in payload:
        changes["salesCabinet"] = normalise_stock(payload.get("salesCabinet"), "salesCabinet")

    unknown = {
        key
        for key in payload
        if key
        not in {
            *WRITABLE_FIELDS,
            "articleNumber",
            "category",
            "archived",
            "images",
            "createdAt",
            "updatedAt",
            "schemaVersion",
        }
    }
    if unknown:
        raise ValidationError(f"Unbekannte Felder: {', '.join(sorted(unknown))}")
    if "archived" in payload:
        raise ValidationError(
            "Archivierung erfolgt ausschließlich über den Archiv-Endpunkt.",
            code="archive_endpoint_required",
        )
    return changes


def next_image_name(article_number: str, existing: list[str]) -> str:
    """Nächste freie Bilddatei ``<artikelnummer>-NN.jpg``."""

    used: set[int] = set()
    for name in existing:
        match = re.search(r"-(\d{2})\.[A-Za-z]+$", name)
        if match:
            used.add(int(match.group(1)))
    for number in range(1, 100):
        if number not in used:
            return f"{article_number}-{number:02d}.jpg"
    raise ImageLimitError("Kein freier Bildname mehr verfügbar.", code="image_limit")
