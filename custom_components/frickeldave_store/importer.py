"""Import des bestehenden Website-Katalogs inklusive Bilder.

Standardmäßig ist der Import rein additiv und idempotent: Bestehende
Artikelnummern werden nie überschrieben, sondern als ``skipped`` gemeldet. Es
gibt keinen Hard-Delete-Pfad. Mit ``overwrite=True`` werden vorhandene Produkte
zusätzlich mit dem Katalogstand überschrieben (Artikelnummer, ``createdAt``,
Bilder, Buchungen und Archiv-Status bleiben lokal). Mit ``dry_run=True`` wird
der Katalog zwar geladen und geprüft, aber nichts geschrieben: kein
Store-Schreibzugriff, keine Bilddatei, kein Snapshot und kein ``updatedAt``.

Fachliche Fehler einzelner Produkte oder Bilder landen in ``errors`` und brechen
den Lauf nicht ab; nur ein nicht lesbares oder strukturell ungültiges
Katalogdokument lehnt die Anfrage vollständig ab, bevor der Store verändert
wird.
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
    default_location_ids,
    normalise_bool,
    normalise_price,
    normalise_stock,
    normalise_stock_map,
    normalise_tags,
    normalise_text,
    to_iso,
)
from .store import CatalogStore, ImageStorage

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
#: Dateinamensschema der Bildablage (``<artikel>-NN.jpg``).
_IMAGE_INDEX_RE = re.compile(r"-(\d{2})\.[A-Za-z]+$")


class CatalogUnavailable(StoreError):
    """Katalog oder Bildbasis nicht erreichbar."""

    code = "catalog_unavailable"


class CatalogInvalid(StoreError):
    """Katalogdokument ist kein gültiges JSON oder strukturell falsch."""

    code = "catalog_invalid"


@dataclass(slots=True)
class ImportResult:
    """Ergebnis eines Imports in der Form der API-Antwort.

    ``would_create``/``would_overwrite``/``unchanged`` sind reine
    Vorschau-Zähler und nur bei ``dry_run=True`` gefüllt. ``overwritten`` zählt
    ausschließlich tatsächlich überschriebene Produkte.
    """

    created: int = 0
    skipped: int = 0
    failed: int = 0
    images_imported: int = 0
    products_total: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)
    dry_run: bool = False
    would_create: int = 0
    would_overwrite: int = 0
    overwritten: int = 0
    unchanged: int = 0
    summary: str = ""
    #: Nur für die Zusammenfassung: archivierte Produkte, die bewusst
    #: unangetastet bleiben (in ``skipped``/``unchanged`` enthalten).
    archived_skipped: int = 0

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
            "dry_run": self.dry_run,
            "would_create": self.would_create,
            "would_overwrite": self.would_overwrite,
            "overwritten": self.overwritten,
            "unchanged": self.unchanged,
            "summary": self.summary,
        }


@dataclass(slots=True)
class ImportPlan:
    """Vorberechneter, noch nicht geschriebener Importplan."""

    create: list[Product] = field(default_factory=list)
    update: list[Product] = field(default_factory=list)
    unchanged: int = 0
    archived_skipped: int = 0
    #: Je Artikelnummer die in diesem Lauf geladenen (neuen) Bildnamen.
    fetched_images: dict[str, list[str]] = field(default_factory=dict)


def _count(value: int, singular: str, plural: str) -> str:
    return f"{value} {singular if value == 1 else plural}"


def build_summary(result: ImportResult) -> str:
    """Deutsche Zusammenfassung des Laufs für die API-Antwort."""

    if result.images_imported == 1:
        planned, imported = "1 Bild würde importiert", "1 Bild importiert"
    else:
        images = _count(result.images_imported, "Bild", "Bilder")
        planned, imported = f"{images} würden importiert", f"{images} importiert"
    if result.dry_run:
        text = (
            f"Vorschau: {_count(result.products_total, 'Produkt', 'Produkte')} im Katalog, "
            f"{result.would_create} neu, {result.would_overwrite} zu überschreiben, "
            f"{result.unchanged} unverändert, {result.failed} fehlerhaft, "
            f"{planned}. Es wurde nichts geschrieben."
        )
    else:
        text = (
            f"Import abgeschlossen: {result.created} neu angelegt, "
            f"{result.overwritten} überschrieben, {result.skipped} unverändert, "
            f"{result.failed} fehlerhaft, {imported}."
        )
    if result.archived_skipped:
        text += f" Davon archiviert und übersprungen: {result.archived_skipped}."
    return text


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


def map_catalog_product(raw: Any, *, stamp: str, location_ids: list[str] | None = None) -> Product:
    """Ein Katalogprodukt in das Store-Modell überführen (Schema-Version 2)."""

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

    ids = list(location_ids or default_location_ids())
    stock = normalise_stock_map(raw.get("stock", 0), ids, field_name="stock")
    if raw.get("salesCabinet") is not None and len(ids) > 1:
        stock[ids[1]] = normalise_stock(raw.get("salesCabinet"), "salesCabinet")

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
        stock=stock,
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
    dry_run: bool = False,
    overwrite: bool = False,
    session: aiohttp.ClientSession | None = None,
    request_timeout: float = IMPORT_TIMEOUT,
    max_concurrency: int = IMPORT_MAX_CONCURRENCY,
    stamp: str | None = None,
) -> ImportResult:
    """Katalog importieren.

    Ohne ``overwrite`` bleiben bestehende Produkte unverändert. ``dry_run``
    erzeugt ausschließlich eine Vorschau: Der Plan wird berechnet, aber weder
    Store noch Bildablage werden geschrieben (kein ``async_save``, damit auch
    kein Snapshot und kein ``updatedAt``).
    """

    source_url = validate_import_url(source_url, label="source_url")
    image_base = validate_import_url(image_base_url, label="image_base_url").rstrip("/")

    own_session = session is None
    client: aiohttp.ClientSession = session or aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=request_timeout)
    )
    try:
        document = await _async_fetch_document(client, source_url, request_timeout=request_timeout)
        result = ImportResult(products_total=len(document["products"]), dry_run=dry_run)
        plan = _plan_import(document["products"], result, store, stamp, overwrite=overwrite)
        if import_images:
            # Im Dry-Run existiert keine Bildablage als Schreibziel.
            sink = None if dry_run else store.images
            await _async_collect_images(
                plan,
                image_base,
                client=client,
                request_timeout=request_timeout,
                max_concurrency=max_concurrency,
                result=result,
                sink=sink,
            )
        if dry_run:
            result.would_create = len(plan.create)
            result.would_overwrite = len(plan.update)
            result.unchanged = plan.unchanged + plan.archived_skipped
            result.skipped = result.unchanged
            result.images_imported = sum(len(names) for names in plan.fetched_images.values())
        else:
            await _async_apply_plan(store, plan, result)
        result.archived_skipped = plan.archived_skipped
        result.summary = build_summary(result)
    finally:
        if own_session:
            await client.close()
    return result


def _plan_import(
    raw_products: list[Any],
    result: ImportResult,
    store: CatalogStore,
    stamp: str | None,
    *,
    overwrite: bool,
) -> ImportPlan:
    """Payload prüfen und den Schreibplan berechnen – ohne den Store zu verändern."""

    existing = store.article_numbers()
    location_ids = store.location_ids()
    seen: set[str] = set()
    plan = ImportPlan()
    moment = stamp or to_iso()
    for raw in raw_products:
        article_number = ""
        if isinstance(raw, dict):
            article_number = str(raw.get("articleNumber", "")).strip().upper()
        try:
            product = map_catalog_product(raw, stamp=moment, location_ids=location_ids)
        except StoreError as err:
            result.failed += 1
            result.add_error(article_number, err.message)
            continue
        if product.article_number in seen:
            result.failed += 1
            result.add_error(product.article_number, "Doppelte Artikelnummer im Katalog.")
            continue
        seen.add(product.article_number)
        if product.article_number not in existing:
            plan.create.append(product)
            continue
        local_raw = store.get_raw_product(product.article_number)
        if local_raw is None:  # pragma: no cover - existing und get_raw sind synchron
            plan.create.append(product)
            continue
        if local_raw.get("archived"):
            # Archivierte Produkte werden nie reaktiviert.
            plan.archived_skipped += 1
            continue
        if not overwrite:
            plan.unchanged += 1
            continue
        local = Product.from_dict(local_raw, location_ids=location_ids)
        if _catalog_signature(local) == _catalog_signature(product):
            plan.unchanged += 1
            continue
        plan.update.append(_merge_catalog_product(local, product, stamp=moment))
    return plan


#: Felder, die beim Überschreiben aus dem Katalog übernommen werden.
OVERWRITE_FIELDS: Final[tuple[str, ...]] = (
    "name",
    "description",
    "price",
    "size",
    "tags",
    "visible",
    "customizable",
    "stock",
)


def _catalog_signature(product: Product) -> tuple[Any, ...]:
    """Vergleichbare Katalogfelder (ohne lokale Felder wie Bilder/Archiv)."""

    values: list[Any] = []
    for field_name in OVERWRITE_FIELDS:
        value = getattr(product, field_name)
        if field_name == "tags":
            value = tuple(sorted(tag.casefold() for tag in value))
        elif field_name == "stock":
            value = tuple(sorted((key, int(amount)) for key, amount in value.items()))
        values.append(value)
    return tuple(values)


def _merge_catalog_product(local: Product, catalog: Product, *, stamp: str) -> Product:
    """Katalogstand über den lokalen Bestand legen.

    Übernommen werden :data:`OVERWRITE_FIELDS`. Lokal bleiben Artikelnummer,
    ``createdAt``, Bilder, Archiv-Status, Buchungen (separat gespeichert) und
    die Kategorie – letztere ist unveränderlich, weil die Artikelnummer sie
    kodiert.
    """

    return Product(
        article_number=local.article_number,
        name=catalog.name,
        category=local.category,
        price=catalog.price,
        description=catalog.description,
        size=catalog.size,
        tags=list(catalog.tags),
        customizable=catalog.customizable,
        visible=catalog.visible,
        stock=dict(catalog.stock),
        images=list(local.images),
        archived=local.archived,
        created_at=local.created_at or stamp,
        updated_at=stamp,
        schema_version=SCHEMA_VERSION,
    )


async def _async_apply_plan(store: CatalogStore, plan: ImportPlan, result: ImportResult) -> None:
    """Plan unter dem Store-Lock schreiben; nur hier entstehen Store-Änderungen."""

    async with store.lock:
        for product in plan.create:
            winner = store.get_raw_product(product.article_number)
            if winner is not None:
                await _discard_images(store, product, winner)
                result.skipped += 1
                continue
            store.put_product(product)
            for tag in product.tags:
                store.add_tag(tag)
            result.created += 1
            result.images_imported += len(product.images)

        for product in plan.update:
            winner = store.get_raw_product(product.article_number)
            fetched = plan.fetched_images.get(product.article_number, [])
            if winner is None:
                # Zwischen Planung und Anwendung verschwunden: wie neu anlegen.
                store.put_product(product)
                for tag in product.tags:
                    store.add_tag(tag)
                result.created += 1
                result.images_imported += len(fetched)
                continue
            if winner.get("archived"):
                # Archiv bleibt archiviert – auch bei overwrite.
                await _discard_images(store, product, winner)
                result.skipped += 1
                continue
            _merge_winner_images(product, winner)
            store.put_product(product)
            for tag in product.tags:
                store.add_tag(tag)
            result.overwritten += 1
            result.images_imported += len(fetched)

        result.skipped += plan.unchanged + plan.archived_skipped
        if result.created or result.overwritten:
            # Erst hier entstehen Store-Datei, Snapshot und ``updatedAt``.
            await store.async_save()


def _merge_winner_images(product: Product, winner: dict[str, Any]) -> None:
    """Zwischenzeitlich ergänzte lokale Bilder nicht verlieren."""

    known = {image.name for image in product.images}
    for item in winner.get("images", []) or []:
        name = str(item.get("name", ""))
        if name and name not in known:
            product.images.append(Image.from_dict(item))
            known.add(name)


async def _discard_images(store: CatalogStore, product: Product, winner: dict[str, Any]) -> None:
    """Nur die in diesem Lauf geladenen Bilder entfernen, die der Bestand nicht nutzt."""

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
    plan: ImportPlan,
    image_base: str,
    *,
    client: aiohttp.ClientSession,
    request_timeout: float,
    max_concurrency: int,
    result: ImportResult,
    sink: ImageStorage | None,
) -> None:
    """Bilder mit begrenzter Parallelität laden; Fehler je Produkt sammeln.

    ``sink`` ist im Trockenlauf ``None``; dann werden Bilddaten nur geprüft,
    aber nirgends geschrieben.
    """

    limit = max(1, max_concurrency)
    semaphore = asyncio.Semaphore(limit)

    async def _load(product: Product) -> None:
        product.images, fetched = await _async_product_images(
            product,
            image_base,
            client=client,
            request_timeout=request_timeout,
            semaphore=semaphore,
            result=result,
            sink=sink,
        )
        plan.fetched_images[product.article_number] = fetched

    await asyncio.gather(*(_load(product) for product in (*plan.create, *plan.update)))


async def _async_product_images(
    product: Product,
    image_base: str,
    *,
    client: aiohttp.ClientSession,
    request_timeout: float,
    semaphore: asyncio.Semaphore,
    result: ImportResult,
    sink: ImageStorage | None,
) -> tuple[list[Image], list[str]]:
    """``<artikel>-01`` bis ``<artikel>-10`` prüfen; fehlende Indizes werden übersprungen.

    Bereits lokal vorhandene Bilder bleiben unangetastet und werden nicht erneut
    geladen. Zurückgegeben werden das vollständige Bildverzeichnis und die in
    diesem Lauf neu geladenen Namen.
    """

    images: list[Image] = list(product.images)
    existing = {image.name for image in images}
    used_indices = {
        int(match.group(1))
        for match in (_IMAGE_INDEX_RE.search(name) for name in existing)
        if match is not None
    }
    fetched: list[str] = []
    for index in range(1, MAX_IMAGES + 1):
        if len(images) >= MAX_IMAGES:
            break
        if index in used_indices:
            # Lokales Bild bleibt erhalten; nichts nachladen oder schreiben.
            continue
        found: tuple[str, bytes] | None = None
        for suffix in IMAGE_SUFFIXES:
            name = f"{product.article_number}-{index:02d}{suffix}"
            url = f"{image_base}/{product.category}/{product.article_number}/{name}"
            outcome = await _async_fetch_image(
                client, url, request_timeout=request_timeout, semaphore=semaphore
            )
            if outcome.error:
                result.add_error(product.article_number, f"Bild {name}: {outcome.error}")
                return images, fetched
            if outcome.status == 404:
                continue
            if outcome.status != 200 or outcome.data is None:
                result.add_error(
                    product.article_number,
                    f"Bild {name} konnte nicht geladen werden (HTTP {outcome.status}).",
                )
                return images, fetched
            found = (name, outcome.data)
            break
        if found is None:
            continue
        name, data = found
        if sink is not None:
            await sink.async_write(product.article_number, name, data)
        images.append(Image(name=name, is_main=not images))
        fetched.append(name)
    return images, fetched


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
