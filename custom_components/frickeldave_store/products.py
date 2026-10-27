"""Produkt-Domain: CRUD, Artikelnummern, Filter, Bilder, Tags und Bestände."""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import (
    ARTICLE_NUMBER_LENGTH,
    BOOKING_TYPES,
    CATEGORIES,
    MAX_IMAGES,
    VISIBILITY_VALUES,
    ConflictError,
    Image,
    ImageLimitError,
    NotFoundError,
    Product,
    StockBooking,
    StoreError,
    ValidationError,
    next_image_name,
    normalise_bool,
    normalise_tags,
    to_iso,
    validate_create_payload,
    validate_update_payload,
)
from .store import ALLOWED_CONTENT_TYPES, MAX_IMAGE_BYTES, CatalogStore

#: Zeichen ohne Verwechslungsgefahr (kein I, O, 0, 1).
_ARTICLE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_article_number(category: str) -> str:
    """Artikelnummer: Kategoriepräfix plus fünf Zufallszeichen."""

    category = category.upper()
    if category not in CATEGORIES:
        raise ValidationError(f"Unbekannte Kategorie: {category!r}")
    suffix_length = ARTICLE_NUMBER_LENGTH - len(category)
    suffix = "".join(secrets.choice(_ARTICLE_ALPHABET) for _ in range(suffix_length))
    return f"{category}{suffix}"


@dataclass(slots=True)
class ProductFilter:
    """Filter- und Sortierregeln (Kategorie ODER, Tags UND, Rest UND)."""

    search: str = ""
    categories: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    customizable: bool | None = None
    in_stock: bool | None = None
    in_cabinet: bool | None = None
    visibility: str | None = None
    archived: bool = False
    sort: str = "name"
    order: str = "asc"
    limit: int | None = None
    offset: int = 0

    @classmethod
    def from_query(cls, params: Mapping[str, str]) -> ProductFilter:
        categories = [part.strip().upper() for part in params.get("category", "").split(",")]
        categories = [part for part in categories if part]
        unknown = [part for part in categories if part not in CATEGORIES]
        if unknown:
            raise ValidationError(f"Unbekannte Kategorie: {', '.join(unknown)}")

        visibility = (params.get("visibility") or "").strip().lower() or None
        if visibility is not None and visibility not in VISIBILITY_VALUES:
            raise ValidationError("visibility muss 'visible' oder 'invisible' sein.")

        sort = (params.get("sort") or "name").strip().lower()
        if sort not in {"name", "price"}:
            raise ValidationError("sort muss 'name' oder 'price' sein.")
        order = (params.get("order") or "asc").strip().lower()
        if order not in {"asc", "desc"}:
            raise ValidationError("order muss 'asc' oder 'desc' sein.")

        def _optional_bool(key: str) -> bool | None:
            raw = params.get(key)
            if raw is None or raw == "":
                return None
            return normalise_bool(raw)

        limit_raw = (params.get("limit") or "").strip()
        offset_raw = (params.get("offset") or "0").strip()
        try:
            limit = int(limit_raw) if limit_raw else None
            offset = int(offset_raw) if offset_raw else 0
        except ValueError as err:
            raise ValidationError("limit/offset müssen ganze Zahlen sein.") from err
        if limit is not None and limit < 0:
            raise ValidationError("limit darf nicht negativ sein.")
        if offset < 0:
            raise ValidationError("offset darf nicht negativ sein.")

        return cls(
            search=(params.get("search") or "").strip(),
            categories=categories,
            tags=normalise_tags(params.get("tags", "")),
            customizable=_optional_bool("customizable"),
            in_stock=_optional_bool("in_stock"),
            in_cabinet=_optional_bool("in_cabinet"),
            visibility=visibility,
            archived=_optional_bool("archived") or False,
            sort=sort,
            order=order,
            limit=limit,
            offset=offset,
        )


def product_matches(product: Product, filters: ProductFilter) -> bool:
    """Alle Filterregeln anwenden (Kategorie ODER, Tags UND, übrige UND)."""

    if product.archived != filters.archived:
        return False
    if filters.categories and product.category not in filters.categories:
        return False
    product_tags = {tag.casefold() for tag in product.tags}
    if filters.tags and not all(tag.casefold() in product_tags for tag in filters.tags):
        return False
    if filters.search:
        needle = filters.search.casefold()
        haystack = " ".join(
            [
                product.name,
                product.description,
                product.article_number,
                " ".join(product.tags),
            ]
        ).casefold()
        if needle not in haystack:
            return False
    if filters.customizable is not None and product.customizable != filters.customizable:
        return False
    if filters.in_stock is not None and (product.stock > 0) != filters.in_stock:
        return False
    if filters.in_cabinet is not None and (product.sales_cabinet > 0) != filters.in_cabinet:
        return False
    if filters.visibility is not None:
        is_visible = product.visible
        if filters.visibility == "visible" and not is_visible:
            return False
        if filters.visibility == "invisible" and is_visible:
            return False
    return True


def sort_products(products: list[Product], sort: str, order: str) -> list[Product]:
    """Stabile Sortierung nach Name oder Preis mit Artikelnummer als Tiebreaker."""

    if sort == "price":
        key = lambda item: (item.price, item.article_number)  # noqa: E731
    else:
        key = lambda item: (item.name.casefold(), item.article_number)  # noqa: E731
    return sorted(products, key=key, reverse=order == "desc")


class ProductService:
    """Service-Layer um :class:`CatalogStore`; alle Methoden sind asynchron."""

    def __init__(self, store: CatalogStore) -> None:
        self._store = store

    @property
    def store(self) -> CatalogStore:
        return self._store

    # ------------------------------------------------------------------ Produkte
    async def async_create(self, payload: dict[str, Any], *, actor: str = "") -> Product:
        values = validate_create_payload(payload)
        async with self._store.lock:
            existing = self._store.article_numbers()
            article_number = generate_article_number(values["category"])
            attempts = 0
            while article_number in existing:
                attempts += 1
                if attempts > 1000:  # pragma: no cover - praktisch unerreichbar
                    raise ConflictError("Keine freie Artikelnummer gefunden.")
                article_number = generate_article_number(values["category"])
            now = to_iso()
            product = Product(
                article_number=article_number,
                created_at=now,
                updated_at=now,
                name=values["name"],
                category=values["category"],
                description=values["description"],
                price=values["price"],
                size=values["size"],
                tags=values["tags"],
                customizable=values["customizable"],
                visible=values["visible"],
                stock=values["stock"],
                sales_cabinet=values["salesCabinet"],
            )
            self._store.put_product(product)
            for tag in product.tags:
                self._store.add_tag(tag)
            await self._store.async_save()
        return product

    async def async_update(self, article_number: str, payload: dict[str, Any]) -> Product:
        async with self._store.lock:
            product = self._store.get_product(article_number)
            changes = validate_update_payload(payload, immutable_category=product.category)
            for key, value in changes.items():
                if key == "salesCabinet":
                    product.sales_cabinet = value
                else:
                    setattr(product, key, value)
            for tag in product.tags:
                self._store.add_tag(tag)
            self._store.put_product(product)
            await self._store.async_save()
        return product

    async def async_archive(self, article_number: str) -> Product:
        """Soft-Delete: setzt ``archived``; es gibt keinen Hard-Delete-Pfad."""

        async with self._store.lock:
            product = self._store.get_product(article_number, include_archived=True)
            product.archived = True
            self._store.put_product(product)
            await self._store.async_save()
        return product

    async def async_restore(self, article_number: str) -> Product:
        async with self._store.lock:
            product = self._store.get_product(article_number, include_archived=True)
            product.archived = False
            self._store.put_product(product)
            await self._store.async_save()
        return product

    async def async_get(self, article_number: str, *, include_archived: bool = False) -> Product:
        product = self._store.get_product(article_number, include_archived=include_archived)
        self._sync_images(product)
        return product

    async def async_list(self, filters: ProductFilter | None = None) -> tuple[list[Product], int]:
        rules = filters or ProductFilter()
        products = [item for item in self._store.products() if product_matches(item, rules)]
        products = sort_products(products, rules.sort, rules.order)
        total = len(products)
        if rules.offset:
            products = products[rules.offset :]
        if rules.limit is not None:
            products = products[: rules.limit]
        return products, total

    # ------------------------------------------------------------------ Tags
    async def async_list_tags(self) -> list[str]:
        return self._store.tag_names()

    async def async_create_tag(self, payload: dict[str, Any]) -> str:
        name = str(payload.get("name", "")).strip() if isinstance(payload, dict) else ""
        if not name:
            raise ValidationError("name ist ein Pflichtfeld.")
        async with self._store.lock:
            tag = self._store.add_tag(name)
            await self._store.async_save()
        return tag

    async def async_delete_tag(self, name: str) -> None:
        async with self._store.lock:
            self._store.remove_tag(name)
            await self._store.async_save()

    # ------------------------------------------------------------------ Bestand
    async def async_book_stock(
        self,
        article_number: str,
        payload: dict[str, Any],
        *,
        ha_user_id: str = "",
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValidationError("Anfragekörper muss ein JSON-Objekt sein.")
        booking_type = str(payload.get("type", "")).strip().lower()
        if booking_type not in BOOKING_TYPES:
            raise ValidationError(f"type muss eine von {', '.join(BOOKING_TYPES)} sein.")
        try:
            quantity = int(payload.get("quantity", 0))
        except (TypeError, ValueError) as err:
            raise ValidationError("quantity muss eine ganze Zahl sein.") from err
        if quantity == 0:
            raise ValidationError("quantity darf nicht 0 sein.")
        if booking_type in {"in", "out", "transfer"} and quantity < 0:
            raise ValidationError("quantity muss bei Zugang/Abgang positiv sein.")
        if booking_type == "correction" and quantity < 0:
            raise ValidationError("quantity muss bei der Korrektur positiv sein.")
        note = str(payload.get("note", "") or "").strip()

        async with self._store.lock:
            product = self._store.get_product(article_number)
            if booking_type == "in":
                product.stock += quantity
            elif booking_type == "out":
                product.stock = max(0, product.stock - quantity)
            elif booking_type == "transfer":
                moved = min(quantity, product.stock)
                product.stock -= moved
                product.sales_cabinet += moved
            elif booking_type == "correction":
                product.stock = quantity
            booking = StockBooking(
                id=secrets.token_hex(8),
                article_number=product.article_number,
                type=booking_type,
                quantity=quantity,
                note=note,
                at=to_iso(),
                ha_user_id=ha_user_id,
            )
            self._store.put_product(product)
            self._store.add_booking(booking)
            await self._store.async_save()
        return {"booking": booking.to_dict(), "product": product.to_dict()}

    async def async_list_bookings(self, article_number: str) -> list[dict[str, Any]]:
        self._store.get_product(article_number, include_archived=True)
        return sorted(
            self._store.bookings(article_number),
            key=lambda item: str(item.get("at", "")),
            reverse=True,
        )

    # ------------------------------------------------------------------ Bilder
    def _sync_images(self, product: Product) -> None:
        """Genau ein Hauptbild sicherstellen (erste Datei, falls keines gesetzt ist)."""

        if not product.images:
            return
        mains = [image for image in product.images if image.is_main]
        if not mains:
            product.images[0].is_main = True
        elif len(mains) > 1:
            first = mains[0]
            for image in product.images:
                image.is_main = image is first

    async def async_list_images(self, article_number: str) -> list[dict[str, Any]]:
        product = await self.async_get(article_number, include_archived=True)
        self._sync_images(product)
        if not product.images:
            return []
        return [
            {
                "name": image.name,
                "url": (
                    f"/api/frickeldave_store/products/{product.article_number}/images/{image.name}"
                ),
                "is_main": image.is_main,
            }
            for image in product.images
        ]

    async def async_add_image(
        self,
        article_number: str,
        *,
        filename: str,
        content_type: str,
        data: bytes,
    ) -> dict[str, Any]:
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise ValidationError(
                "Nur JPEG, PNG, WebP und GIF werden unterstützt.", code="unsupported_media_type"
            )
        if not data:
            raise ValidationError("Die hochgeladene Datei ist leer.")
        if len(data) > MAX_IMAGE_BYTES:
            raise ValidationError("Das Bild ist größer als 10 MB.", code="file_too_large")

        async with self._store.lock:
            product = self._store.get_product(article_number, include_archived=True)
            if len(product.images) >= MAX_IMAGES:
                raise ImageLimitError(
                    f"Maximal {MAX_IMAGES} Bilder pro Produkt. Bitte zuerst ein Bild löschen.",
                    code="image_limit",
                )
            suffix = ALLOWED_CONTENT_TYPES[content_type]
            existing = [image.name for image in product.images]
            base_name = next_image_name(product.article_number, existing)
            name = f"{Path(base_name).stem}{suffix}"
            written = await self._store.images.async_write(product.article_number, name, data)
            product.images.append(Image(name=name, is_main=not product.images))
            try:
                self._store.put_product(product)
                await self._store.async_save()
            except BaseException:
                await self._store.images.async_delete(product.article_number, name)
                raise
        _ = written, filename
        return {"images": await self.async_list_images(article_number)}

    async def async_delete_image(self, article_number: str, name: str) -> dict[str, Any]:
        async with self._store.lock:
            product = self._store.get_product(article_number, include_archived=True)
            _validate_image_name(product, name)
            was_main = any(image.is_main and image.name == name for image in product.images)
            product.images = [image for image in product.images if image.name != name]
            if was_main and product.images:
                product.images[0].is_main = True
            self._sync_images(product)
            self._store.put_product(product)
            await self._store.async_save()
            await self._store.images.async_delete(product.article_number, name)
        return {"images": await self.async_list_images(article_number)}

    async def async_set_image_order(self, article_number: str, order: list[str]) -> dict[str, Any]:
        async with self._store.lock:
            product = self._store.get_product(article_number, include_archived=True)
            existing = [image.name for image in product.images]
            if sorted(order) != sorted(existing) or len(set(order)) != len(order):
                raise ValidationError("order muss exakt alle vorhandenen Bildnamen enthalten.")
            by_name = {image.name: image for image in product.images}
            product.images = [by_name[name] for name in order]
            self._sync_images(product)
            self._store.put_product(product)
            await self._store.async_save()
        return {"images": await self.async_list_images(article_number)}

    async def async_set_main_image(self, article_number: str, name: str) -> dict[str, Any]:
        async with self._store.lock:
            product = self._store.get_product(article_number, include_archived=True)
            _validate_image_name(product, name)
            for image in product.images:
                image.is_main = image.name == name
            self._store.put_product(product)
            await self._store.async_save()
        return {"images": await self.async_list_images(article_number)}

    async def async_read_image(self, article_number: str, name: str) -> tuple[Path, str]:
        product = await self.async_get(article_number, include_archived=True)
        _validate_image_name(product, name)
        suffix = Path(name).suffix.casefold()
        content_type = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }.get(suffix, "application/octet-stream")
        return self._store.images.path_for(product.article_number, name), content_type


def _validate_image_name(product: Product, name: str) -> None:
    if Path(name).name != name:
        raise ValidationError("Ungültiger Bildname.")
    if name not in {image.name for image in product.images}:
        raise NotFoundError(f"Bild {name} nicht gefunden.")


__all__ = [
    "ProductFilter",
    "ProductService",
    "generate_article_number",
    "product_matches",
    "sort_products",
    "StoreError",
]
