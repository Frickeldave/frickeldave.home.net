"""Tests für die Produktdomain: Artikelnummern, Soft-Delete, Filter, Bilder, Bestand."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from custom_components.frickeldave_store.models import (
    MAX_IMAGES,
    ImageLimitError,
    NotFoundError,
    Product,
    ValidationError,
)
from custom_components.frickeldave_store.products import (
    ProductFilter,
    ProductService,
    generate_article_number,
    product_matches,
    sort_products,
)
from custom_components.frickeldave_store.store import CatalogStore


async def _service(tmp_path: Path) -> tuple[CatalogStore, ProductService]:
    store = CatalogStore(data_dir=tmp_path)
    await store.async_load()
    return store, ProductService(store)


async def _create(service: ProductService, **overrides: object) -> Product:
    payload: dict[str, object] = {
        "name": "Testprodukt",
        "category": "LA",
        "price": 10.0,
        "tags": ["Deko"],
    }
    payload.update(overrides)
    return await service.async_create(payload)


async def test_create_generates_unique_formatted_article_numbers(tmp_path: Path) -> None:
    _, service = await _service(tmp_path)
    products = await asyncio.gather(
        *[_create(service, name=f"Produkt {index}") for index in range(20)]
    )
    numbers = [product.article_number for product in products]
    assert len(set(numbers)) == 20
    for number in numbers:
        assert len(number) == 6
        assert number.startswith("LA")


async def test_parallel_creation_never_duplicates(tmp_path: Path) -> None:
    store, service = await _service(tmp_path)

    async def _create_one(index: int) -> str:
        product = await service.async_create(
            {"name": f"Parallel {index}", "category": "WO", "price": 1}
        )
        return product.article_number

    numbers = await asyncio.gather(*[_create_one(index) for index in range(30)])
    assert len(set(numbers)) == 30
    assert len(store.article_numbers()) == 30


async def test_article_number_is_immutable(tmp_path: Path) -> None:
    _, service = await _service(tmp_path)
    product = await _create(service)
    with pytest.raises(ValidationError):
        await service.async_update(product.article_number, {"articleNumber": "LAXXXX"})


async def test_category_is_immutable(tmp_path: Path) -> None:
    _, service = await _service(tmp_path)
    product = await _create(service, category="EP")
    with pytest.raises(ValidationError) as err:
        await service.async_update(product.article_number, {"category": "LA"})
    assert err.value.code == "category_immutable"
    updated = await service.async_update(product.article_number, {"name": "Neu"})
    assert updated.category == "EP"
    assert updated.name == "Neu"


async def test_soft_delete_only(tmp_path: Path) -> None:
    store, service = await _service(tmp_path)
    product = await _create(service)
    archived = await service.async_archive(product.article_number)
    assert archived.archived is True
    assert not hasattr(service, "async_delete")
    assert not hasattr(store, "delete_product")
    assert await service.async_list() == ([], 0)
    archived_list, total = await service.async_list(ProductFilter(archived=True))
    assert total == 1
    assert archived_list[0].article_number == product.article_number
    with pytest.raises(NotFoundError):
        await service.async_get(product.article_number)


async def test_filters_category_or_tags_or_rest_and(tmp_path: Path) -> None:
    _, service = await _service(tmp_path)
    a = await _create(service, name="Lampe", category="LA", tags=["Deko", "Holz"], stock=1)
    b = await _create(service, name="Vase", category="EP", tags=["Deko"], stock=0)
    c = await _create(service, name="Box", category="LA", tags=["Holz"], stock=0, visible=False)
    d = await _create(service, name="Archiv", category="LA", tags=["Deko", "Holz"])
    await service.async_archive(d.article_number)

    category_or, total = await service.async_list(ProductFilter(categories=["LA", "EP"]))
    assert total == 3
    assert {item.article_number for item in category_or} == {
        a.article_number,
        b.article_number,
        c.article_number,
    }

    tags_or, total = await service.async_list(ProductFilter(tags=["Deko", "Holz"]))
    assert total == 3
    assert {item.article_number for item in tags_or} == {
        a.article_number,
        b.article_number,
        c.article_number,
    }

    combined, total = await service.async_list(
        ProductFilter(categories=["LA"], tags=["Holz"], in_stock=True)
    )
    assert total == 1
    assert combined[0].article_number == a.article_number

    invisible, total = await service.async_list(ProductFilter(visibility="invisible"))
    assert total == 1
    assert invisible[0].article_number == c.article_number

    search, total = await service.async_list(ProductFilter(search="vase"))
    assert total == 1
    assert search[0].article_number == b.article_number


async def test_tag_filter_matches_any_selected_tag(tmp_path: Path) -> None:
    _, service = await _service(tmp_path)
    both = await _create(service, name="Beides", tags=["a", "b"], stock=1)
    only_a = await _create(service, name="Nur A", tags=["a"], stock=1)
    only_b = await _create(service, name="Nur B", tags=["B"], stock=1)
    neither = await _create(service, name="Keins", tags=["c"], stock=1)

    any_tag, total = await service.async_list(ProductFilter(tags=["a", "b"]))
    assert total == 3
    assert {item.article_number for item in any_tag} == {
        both.article_number,
        only_a.article_number,
        only_b.article_number,
    }
    assert neither.article_number not in {item.article_number for item in any_tag}

    single, total = await service.async_list(ProductFilter(tags=["b"]))
    assert total == 2
    assert {item.article_number for item in single} == {
        both.article_number,
        only_b.article_number,
    }

    without_stock = await _create(service, name="Ohne Bestand", category="EP", tags=["a"])
    combined, total = await service.async_list(
        ProductFilter(categories=["LA"], tags=["a", "b"], in_stock=True)
    )
    assert total == 3
    assert {item.article_number for item in combined} == {
        both.article_number,
        only_a.article_number,
        only_b.article_number,
    }
    assert without_stock.article_number not in {item.article_number for item in combined}


async def test_sorting_is_stable_and_bidirectional(tmp_path: Path) -> None:
    _, service = await _service(tmp_path)
    first = await _create(service, name="Beta", price=20)
    second = await _create(service, name="alpha", price=5)
    third = await _create(service, name="Beta", price=10)

    ascending, _ = await service.async_list(ProductFilter(sort="name", order="asc"))
    assert [item.name for item in ascending] == ["alpha", "Beta", "Beta"]
    assert ascending[1].article_number < ascending[2].article_number

    descending_price, _ = await service.async_list(ProductFilter(sort="price", order="desc"))
    assert [item.price for item in descending_price] == [20.0, 10.0, 5.0]
    assert descending_price[0].article_number == first.article_number
    assert descending_price[-1].article_number == second.article_number
    assert descending_price[1].article_number == third.article_number

    filtered, _ = await service.async_list(
        ProductFilter(sort="price", order="asc", categories=["LA"], limit=2)
    )
    assert [item.price for item in filtered] == [5.0, 10.0]


def test_filter_combination_semantics_directly() -> None:
    product = Product(
        article_number="LA0001",
        name="Schild",
        category="LA",
        tags=["Hochzeit", "Holz"],
        stock=2,
        sales_cabinet=1,
    )
    assert product_matches(product, ProductFilter(categories=["LA"]))
    assert not product_matches(product, ProductFilter(categories=["EP"]))
    assert product_matches(product, ProductFilter(tags=["holz", "HOCHZEIT"]))
    assert product_matches(product, ProductFilter(tags=["holz"]))
    assert product_matches(product, ProductFilter(tags=["Deko", "Holz"]))
    assert not product_matches(product, ProductFilter(tags=["Deko"]))
    assert not product_matches(product, ProductFilter(tags=["Deko", "Metall"]))
    assert not product_matches(product, ProductFilter(tags=["Metall"]))
    rules = ProductFilter(sort="name", order="asc")
    assert sort_products([product], rules.sort, rules.order) == [product]


async def test_max_ten_images_and_eleventh_rejected_without_persistence(tmp_path: Path) -> None:
    store, service = await _service(tmp_path)
    product = await _create(service)

    for _ in range(MAX_IMAGES):
        await service.async_add_image(
            product.article_number,
            filename="bild.jpg",
            content_type="image/jpeg",
            data=b"\xff\xd8fake-jpeg",
        )
    product_after = await service.async_get(product.article_number)
    assert len(product_after.images) == MAX_IMAGES
    stored_files = await store.images.async_list(product.article_number)
    assert len(stored_files) == MAX_IMAGES

    with pytest.raises(ImageLimitError) as err:
        await service.async_add_image(
            product.article_number,
            filename="elf.jpg",
            content_type="image/jpeg",
            data=b"\xff\xd8elf",
        )
    assert err.value.code == "image_limit"
    assert len(await store.images.async_list(product.article_number)) == MAX_IMAGES
    assert len((await service.async_get(product.article_number)).images) == MAX_IMAGES


async def test_image_order_and_main_image(tmp_path: Path) -> None:
    _, service = await _service(tmp_path)
    product = await _create(service)
    for _ in range(3):
        await service.async_add_image(
            product.article_number,
            filename="bild.png",
            content_type="image/png",
            data=b"png",
        )
    images = await service.async_list_images(product.article_number)
    assert images[0]["is_main"] is True
    assert all(item["url"].startswith("/api/frickeldave_store/products/") for item in images)

    names = [item["name"] for item in images]
    await service.async_set_main_image(product.article_number, names[2])
    reordered = await service.async_list_images(product.article_number)
    assert [item["name"] for item in reordered if item["is_main"]] == [names[2]]

    await service.async_set_image_order(product.article_number, list(reversed(names)))
    after = await service.async_list_images(product.article_number)
    assert [item["name"] for item in after] == list(reversed(names))

    with pytest.raises(ValidationError):
        await service.async_set_image_order(product.article_number, names[:2])

    await service.async_delete_image(product.article_number, names[1])
    remaining = await service.async_list_images(product.article_number)
    assert names[1] not in [item["name"] for item in remaining]
    assert sum(1 for item in remaining if item["is_main"]) == 1


async def test_stock_bookings_update_both_counters(tmp_path: Path) -> None:
    _, service = await _service(tmp_path)
    product = await _create(service)

    result = await service.async_book_stock(
        product.article_number, {"type": "in", "quantity": 5}, ha_user_id="user-1"
    )
    assert result["product"]["stock"] == 5
    assert result["booking"]["ha_user_id"] == "user-1"

    result = await service.async_book_stock(
        product.article_number, {"type": "transfer", "quantity": 2}
    )
    assert result["product"]["stock"] == 3
    assert result["product"]["salesCabinet"] == 2

    result = await service.async_book_stock(product.article_number, {"type": "out", "quantity": 1})
    assert result["product"]["stock"] == 2

    result = await service.async_book_stock(
        product.article_number, {"type": "correction", "quantity": 7}
    )
    assert result["product"]["stock"] == 7

    bookings = await service.async_list_bookings(product.article_number)
    assert len(bookings) == 4
    assert {item["type"] for item in bookings} == {"in", "transfer", "out", "correction"}

    with pytest.raises(ValidationError):
        await service.async_book_stock(product.article_number, {"type": "out", "quantity": 0})


async def test_tags_management_removes_tag_from_products(tmp_path: Path) -> None:
    _, service = await _service(tmp_path)
    product = await _create(service, tags=["Deko"])
    await service.async_create_tag({"name": "Neu"})
    assert (await service.async_list_tags()) == ["Deko", "Neu"]

    await service.async_delete_tag("Deko")
    assert (await service.async_list_tags()) == ["Neu"]
    refreshed = await service.async_get(product.article_number)
    assert refreshed.tags == []


def test_generated_numbers_avoid_ambiguous_characters() -> None:
    for _ in range(100):
        assert not set(generate_article_number("SB")[2:]) & set("IO01")
