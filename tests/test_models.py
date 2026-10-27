"""Tests für Domainmodelle und Validierung."""

from __future__ import annotations

import pytest

from custom_components.frickeldave_store.models import (
    ARTICLE_NUMBER_RE,
    Product,
    StoreError,
    ValidationError,
    next_image_name,
    normalise_tags,
    validate_create_payload,
    validate_update_payload,
)
from custom_components.frickeldave_store.products import generate_article_number


def test_article_number_format_and_prefix() -> None:
    for _ in range(50):
        number = generate_article_number("LA")
        assert len(number) == 6
        assert number.startswith("LA")
        assert ARTICLE_NUMBER_RE.match(number)


def test_article_number_rejects_unknown_category() -> None:
    with pytest.raises(ValidationError):
        generate_article_number("XX")


def test_normalise_tags_deduplicates_case_insensitively() -> None:
    assert normalise_tags(["Hochzeit", " hochzeit ", "Deko", ""]) == ["Hochzeit", "Deko"]


def test_create_payload_requires_name_and_category() -> None:
    with pytest.raises(ValidationError):
        validate_create_payload({"category": "LA"})
    with pytest.raises(ValidationError):
        validate_create_payload({"name": "Test", "category": "XX"})
    values = validate_create_payload(
        {"name": " Test ", "category": "la", "price": "12.5", "tags": "Deko, Holz"}
    )
    assert values["category"] == "LA"
    assert values["price"] == 12.5
    assert values["tags"] == ["Deko", "Holz"]


def test_update_payload_rejects_category_change() -> None:
    with pytest.raises(ValidationError) as err:
        validate_update_payload({"category": "WO"}, immutable_category="LA")
    assert err.value.code == "category_immutable"


def test_update_payload_same_category_is_allowed() -> None:
    assert validate_update_payload({"category": "LA"}, immutable_category="LA") == {}


def test_update_payload_rejects_direct_archive_and_article_number() -> None:
    with pytest.raises(ValidationError) as err:
        validate_update_payload({"archived": True}, immutable_category="LA")
    assert err.value.code == "archive_endpoint_required"
    with pytest.raises(ValidationError):
        validate_update_payload({"articleNumber": "LA1234"}, immutable_category="LA")


def test_product_roundtrip_uses_camel_case() -> None:
    product = Product(
        article_number="LAH3K3",
        name="Schild",
        category="LA",
        price=35.0,
        stock=2,
        sales_cabinet=1,
    )
    data = product.to_dict()
    assert data["articleNumber"] == "LAH3K3"
    assert data["salesCabinet"] == 1
    assert data["schemaVersion"] == 1
    assert Product.from_dict(data).to_dict() == data


def test_next_image_name_skips_used_numbers() -> None:
    assert next_image_name("LAH3K3", ["LAH3K3-01.jpg", "LAH3K3-03.jpg"]) == "LAH3K3-02.jpg"


def test_store_error_payload_shape() -> None:
    err = StoreError("kaputt", code="custom")
    assert err.as_dict == {"code": "custom", "message": "kaputt"}
