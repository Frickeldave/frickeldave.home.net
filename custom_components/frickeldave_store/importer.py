"""Import des bestehenden Website-Katalogs inklusive Bilder.

Der Import ist idempotent: Bestehende Artikelnummern werden nie überschrieben,
sondern als ``skipped`` gemeldet. Es gibt keinen Hard-Delete-Pfad – ein Import
kann ausschließlich neue Produkte anlegen. Fachliche Fehler einzelner Produkte
oder Bilder landen in ``errors`` und brechen den Lauf nicht ab; nur ein nicht
lesbares oder strukturell ungültiges Katalogdokument lehnt die Anfrage
vollständig ab, bevor der Store verändert wird.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Final
from urllib.parse import urlparse

import aiohttp

from .const import (
    DEFAULT_CATALOG_URL,
    DEFAULT_IMAGE_BASE_URL,
    IMPORT_ALLOWED_HOSTS,
)
from .models import (
    CATEGORIES,
    MAX_IMAGES,
    SCHEMA_VERSION,
    Image,
    Product,
    StoreError,
    ValidationError,
    normalise_bool,
    normalise_price,
    normalise_stock,
    normalise_tags,
    normalise_text,
    to_iso,
)
from .store import CatalogStore

_LOGGER = logging.getLogger(__name__)

#: Zeitlimit je HTTP-Anfrage (Katalog und Bilder).
IMPORT_TIMEOUT = 30.0
#: Obergrenze gleichzeitiger Bildabrufe.
IMPORT_MAX_CONCURRENCY = 5
#: Bildendungen in der Reihenfolge, in der sie probiert werden.
IMAGE_SUFFIXES: tuple[str, ...] = (".jpg", ".jpeg")
#: Der Altbestand enthält einzelne größere Dateien (bis ~12 MB); Panel-Uploads
#: bleiben bei 10 MB. Der Import erlaubt daher bewusst etwas mehr.
MAX_IMPORT_IMAGE_BYTES: Final = 25 * 1024 * 1024
#: Signaturprüfung statt Content-Type: GitHub liefert große Binärdateien als
#: ``application/octet-stream`` aus.
IMAGE_SIGNATURES: Final[tuple[bytes, ...]] = (
    b"\xff\xd8\xff",
    b"\x89PNG\r\n\x1a\n",
    b"GIF87a",
    b"GIF89a",
)
#: Panel-generierte Artikelnummern sind sechsstellig; der Import akzeptiert
#: zusätzlich kurze Altbestandsnummern (z. B. ``LAX``), damit der bestehende
#: Katalog vollständig übernommen wird.
ARTICLE_NUMBER_RE = re.compile(r"^[A-Z0-9]{1,12}$")


class CatalogUnavailable(StoreError):
    """Katalog oder Bildbasis nicht erreichbar."""

    code = "catalog_unavailable"


class CatalogInvalid(StoreError):
    """Katalogdokument ist kein gültiges JSON oder strukturell falsch."""

    code = "catalog_invalid"


@dataclass(slots=True)
class ImportResult:
    """Ergebnis eines Imports in der Form der API-Antwort."""

    created: int = 0
    skipped: int = 0
    failed: int = 0
    images_imported: int = 0
    products_total: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)

    def add_error(self, article_number: str, message: str) -> None:
        self.errors.append({"articleNumber": article_number, "message": message})

    def to_dict(self) -> dict[str, Any]:
        return {
            "created": self.created,
            "skipped": self.skipped,
            "failed": self.failed,
            "images_imported": self.images_imported,
            "products_total": self.products_total,
            "errors": list(self.errors),
        }


def validate_import_url(url: Any, *, label: str) -> str:
    """Feste https-Allowlist gegen SSRF; liefert die bereinigte URL."""

    if not isinstance(url, str) or not url.strip():
        raise ValidationError(f"{label} muss eine absolute https-URL sein.")
    value = url.strip()
    parsed = urlparse(value)
    if parsed.scheme != "https":
        raise ValidationError(f"{label} muss https verwenden.")
    if parsed.username or parsed.password:
        raise ValidationError(f"{label} darf keine Zugangsdaten enthalten.")
    host = (parsed.hostname or "").casefold()
    if host not in IMPORT_ALLOWED_HOSTS:
        allowed = ", ".join(sorted(IMPORT_ALLOWED_HOSTS))
        raise ValidationError(f"{label} muss auf eine dieser Hosts zeigen: {allowed}.")
    if parsed.port not in (None, 443):
        raise ValidationError(f"{label} muss Port 443 verwenden.")
    return value


def map_catalog_product(raw: Any, *, stamp: str) -> Product:
    """Ein Katalogprodukt in das Store-Modell überführen (Schema-Version 1)."""

    if not isinstance(raw, dict):
        raise ValidationError("Produkt muss ein JSON-Objekt sein.")

    article_number = str(raw.get("articleNumber", "")).strip().upper()
    if not ARTICLE_NUMBER_RE.match(article_number):
        raise ValidationError("Artikelnummer muss aus 1–12 Großbuchstaben oder Ziffern bestehen.")

    category = str(raw.get("category", "")).strip().upper()
    if category not in CATEGORIES:
        allowed = ", ".join(CATEGORIES)
        raise ValidationError(f"Kategorie muss eine von {allowed} sein.")

    name = normalise_text(raw.get("name"), "name", max_length=200)
    if not name:
        raise ValidationError("Name ist ein Pflichtfeld.")

    return Product(
        article_number=article_number,
        name=name,
        category=category,
        price=normalise_price(raw.get("price", 0)),
        description=normalise_text(raw.get("description"), "description"),
        size=normalise_text(raw.get("size"), "size", max_length=200),
        tags=normalise_tags(raw.get("tags", [])),
        customizable=normalise_bool(raw.get("customizable", True)),
        visible=normalise_bool(raw.get("visible", True)),
        stock=normalise_stock(raw.get("stock", 0), "stock"),
        sales_cabinet=normalise_stock(raw.get("salesCabinet", 0), "salesCabinet"),
        images=[],
        archived=False,
        created_at=stamp,
        updated_at=stamp,
        schema_version=SCHEMA_VERSION,
    )


async def async_import_catalog(
    store: CatalogStore,
    *,
    source_url: str = DEFAULT_CATALOG_URL,
    image_base_url: str = DEFAULT_IMAGE_BASE_URL,
    import_images: bool = True,
    session: aiohttp.ClientSession | None = None,
    request_timeout: float = IMPORT_TIMEOUT,
    max_concurrency: int = IMPORT_MAX_CONCURRENCY,
    stamp: str | None = None,
) -> ImportResult:
    """Katalog idempotent importieren; bestehende Produkte bleiben unverändert."""

    source_url = validate_import_url(source_url, label="source_url")
    image_base = validate_import_url(image_base_url, label="image_base_url").rstrip("/")

    own_session = session is None
    client: aiohttp.ClientSession = session or aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=request_timeout)
    )
    try:
        document = await _async_fetch_document(client, source_url, request_timeout=request_timeout)
        result = ImportResult(products_total=len(document["products"]))
        candidates = _collect_candidates(document["products"], result, store, stamp)
        if import_images and candidates:
            await _async_collect_images(
                store,
                candidates,
                image_base,
                client=client,
                request_timeout=request_timeout,
                max_concurrency=max_concurrency,
                result=result,
            )
        await _async_store_candidates(store, candidates, result)
    finally:
        if own_session:
            await client.close()
    return result


def _collect_candidates(
    raw_products: list[Any],
    result: ImportResult,
    store: CatalogStore,
    stamp: str | None,
) -> list[Product]:
    """Payload prüfen, Duplikate und Bestand erkennen – ohne den Store zu verändern."""

    existing = store.article_numbers()
    seen: set[str] = set()
    candidates: list[Product] = []
    moment = stamp or to_iso()
    for raw in raw_products:
        article_number = ""
        if isinstance(raw, dict):
            article_number = str(raw.get("articleNumber", "")).strip().upper()
        try:
            product = map_catalog_product(raw, stamp=moment)
        except StoreError as err:
            result.failed += 1
            result.add_error(article_number, err.message)
            continue
        if product.article_number in seen:
            result.failed += 1
            result.add_error(product.article_number, "Doppelte Artikelnummer im Katalog.")
            continue
        seen.add(product.article_number)
        if product.article_number in existing:
            result.skipped += 1
            continue
        candidates.append(product)
    return candidates


async def _async_store_candidates(
    store: CatalogStore, candidates: list[Product], result: ImportResult
) -> None:
    """Kandidaten unter dem Store-Lock anlegen; nichts Bestehendes überschreiben."""

    async with store.lock:
        for product in candidates:
            if store.get_raw_product(product.article_number) is not None:
                await _discard_images(store, product)
                result.skipped += 1
                continue
            store.put_product(product)
            for tag in product.tags:
                store.add_tag(tag)
            result.created += 1
            result.images_imported += len(product.images)
        if result.created:
            await store.async_save()


async def _discard_images(store: CatalogStore, product: Product) -> None:
    """Bilder eines inzwischen vorhandenen Produkts entfernen (nur eigene Dateien)."""

    winner = store.get_raw_product(product.article_number) or {}
    keep = {str(item.get("name")) for item in winner.get("images", []) or []}
    for image in product.images:
        if image.name in keep:
            continue
        await store.images.async_delete(product.article_number, image.name)


async def _async_fetch_document(
    session: aiohttp.ClientSession, url: str, *, request_timeout: float
) -> dict[str, Any]:
    """Katalogdokument laden und strukturell prüfen (vor jeder Store-Änderung)."""

    try:
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=request_timeout)
        ) as response:
            if response.status != 200:
                raise CatalogUnavailable(
                    f"Der Katalog ist nicht erreichbar (HTTP {response.status})."
                )
            body = await response.read()
    except (TimeoutError, aiohttp.ClientError) as err:
        raise CatalogUnavailable(
            "Zeitüberschreitung oder Netzwerkfehler beim Katalogabruf."
        ) from err

    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise CatalogInvalid("Der Katalog ist kein gültiges JSON-Dokument.") from err
    if not isinstance(document, dict) or not isinstance(document.get("products"), list):
        raise CatalogInvalid("Der Katalog enthält keine Liste 'products'.")
    return document


async def _async_collect_images(
    store: CatalogStore,
    products: list[Product],
    image_base: str,
    *,
    client: aiohttp.ClientSession,
    request_timeout: float,
    max_concurrency: int,
    result: ImportResult,
) -> None:
    """Bilder mit begrenzter Parallelität laden; Fehler je Produkt sammeln."""

    limit = max(1, max_concurrency)
    semaphore = asyncio.Semaphore(limit)

    async def _load(product: Product) -> None:
        product.images = await _async_product_images(
            store,
            product,
            image_base,
            client=client,
            request_timeout=request_timeout,
            semaphore=semaphore,
            result=result,
        )

    await asyncio.gather(*(_load(product) for product in products))


async def _async_product_images(
    store: CatalogStore,
    product: Product,
    image_base: str,
    *,
    client: aiohttp.ClientSession,
    request_timeout: float,
    semaphore: asyncio.Semaphore,
    result: ImportResult,
) -> list[Image]:
    """``<artikel>-01`` bis ``<artikel>-10`` prüfen; fehlende Indizes werden übersprungen."""

    images: list[Image] = []
    for index in range(1, MAX_IMAGES + 1):
        found: tuple[str, bytes] | None = None
        for suffix in IMAGE_SUFFIXES:
            name = f"{product.article_number}-{index:02d}{suffix}"
            url = f"{image_base}/{product.category}/{product.article_number}/{name}"
            outcome = await _async_fetch_image(
                client, url, request_timeout=request_timeout, semaphore=semaphore
            )
            if outcome.error:
                result.add_error(product.article_number, f"Bild {name}: {outcome.error}")
                return images
            if outcome.status == 404:
                continue
            if outcome.status != 200 or outcome.data is None:
                result.add_error(
                    product.article_number,
                    f"Bild {name} konnte nicht geladen werden (HTTP {outcome.status}).",
                )
                return images
            found = (name, outcome.data)
            break
        if found is None:
            continue
        name, data = found
        await store.images.async_write(product.article_number, name, data)
        images.append(Image(name=name, is_main=not images))
    return images


@dataclass(slots=True)
class _ImageOutcome:
    """Ergebnis eines einzelnen Bildabrufs."""

    status: int | None
    data: bytes | None = None
    error: str = ""


async def _async_fetch_image(
    client: aiohttp.ClientSession,
    url: str,
    *,
    request_timeout: float,
    semaphore: asyncio.Semaphore,
) -> _ImageOutcome:
    async with semaphore:
        try:
            async with client.get(
                url, timeout=aiohttp.ClientTimeout(total=request_timeout)
            ) as response:
                if response.status != 200:
                    return _ImageOutcome(status=response.status)
                data = await response.read()
        except (TimeoutError, aiohttp.ClientError):
            return _ImageOutcome(
                status=None,
                error="Zeitüberschreitung oder Netzwerkfehler beim Bildabruf.",
            )
    if not data:
        return _ImageOutcome(status=None, error="leere Antwort.")
    if len(data) > MAX_IMPORT_IMAGE_BYTES:
        return _ImageOutcome(status=None, error="Datei ist größer als 25 MB.")
    if not _looks_like_image(data):
        return _ImageOutcome(status=None, error="kein gültiges Bild (JPEG, PNG, WebP oder GIF).")
    return _ImageOutcome(status=200, data=data)


def _looks_like_image(data: bytes) -> bool:
    """Signatur prüfen; der Content-Type ist bei GitHub nicht verlässlich."""

    if data.startswith(IMAGE_SIGNATURES):
        return True
    return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
