"""Tests für den KI-Dienst: Metadaten, Fehler, keine Persistenz."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from custom_components.frickeldave_store.ai import (
    AIError,
    AIService,
    DeepSeekProvider,
    strip_metadata,
)
from custom_components.frickeldave_store.models import Product
from custom_components.frickeldave_store.products import ProductService
from custom_components.frickeldave_store.store import CatalogStore


def _jpeg_with_metadata() -> bytes:
    def segment(marker: int, payload: bytes) -> bytes:
        return bytes([0xFF, marker]) + (len(payload) + 2).to_bytes(2, "big") + payload

    return (
        b"\xff\xd8"
        + segment(0xE1, b"Exif\x00\x00GPSLatitude=52.5;GPSLongitude=13.4")
        + segment(0xE0, b"JFIF\x00\x01")
        + segment(0xFE, b"geheimer Kommentar")
        + b"\xff\xda\x00\x02"
        + b"BILDDATEN"
        + b"\xff\xd9"
    )


def _png_with_text() -> bytes:
    import struct
    import zlib

    def chunk(kind: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"tEXt", b"Author\x00David")
        + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
        + chunk(b"IEND", b"")
    )


def test_strip_metadata_removes_exif_gps_and_comments() -> None:
    cleaned = strip_metadata(_jpeg_with_metadata(), "image/jpeg")
    assert b"GPS" not in cleaned
    assert b"geheimer Kommentar" not in cleaned
    assert b"JFIF" in cleaned
    assert cleaned.endswith(b"BILDDATEN\xff\xd9")
    assert cleaned.startswith(b"\xff\xd8")


def test_strip_metadata_removes_png_text_chunks() -> None:
    cleaned = strip_metadata(_png_with_text(), "image/png")
    assert b"Author" not in cleaned
    assert cleaned.startswith(b"\x89PNG\r\n\x1a\n")
    assert b"IHDR" in cleaned and b"IDAT" in cleaned


class FakeResponse:
    def __init__(self, status: int, payload: dict[str, Any]) -> None:
        self.status = status
        self._payload = payload

    async def json(self, content_type: str | None = None) -> dict[str, Any]:
        return self._payload

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


class FakeSession:
    def __init__(self, response: FakeResponse | Exception) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def post(self, url: str, **kwargs: Any) -> Any:
        self.calls.append({"url": url, **kwargs})
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def product() -> Product:
    return Product(article_number="LAH3K3", name="Schild", category="LA", size="25cm")


async def test_provider_returns_suggestion_and_sends_cleaned_image(product: Product) -> None:
    session = FakeSession(FakeResponse(200, {"choices": [{"message": {"content": "Schön."}}]}))
    provider = DeepSeekProvider(
        endpoint="https://api.example.test/chat",
        model="deepseek-flash",
        api_key="secret-key",
        prompt="Beschreibe.",
        session=session,  # type: ignore[arg-type]
    )
    suggestion = await provider.async_suggest(product, [(_jpeg_with_metadata(), "image/jpeg")])
    assert suggestion == "Schön."
    call = session.calls[0]
    assert call["headers"]["Authorization"] == "Bearer secret-key"
    image_url = call["json"]["messages"][1]["content"][1]["image_url"]["url"]
    assert image_url.startswith("data:image/jpeg;base64,")
    import base64

    encoded = image_url.split(",", 1)[1]
    assert b"GPS" not in base64.b64decode(encoded)


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (429, "ai_rate_limited"),
        (401, "ai_unauthorized"),
        (500, "ai_unavailable"),
        (400, "ai_request_failed"),
    ],
)
async def test_provider_maps_http_errors(product: Product, status: int, code: str) -> None:
    session = FakeSession(FakeResponse(status, {}))
    provider = DeepSeekProvider(
        endpoint="https://api.example.test",
        model="deepseek-flash",
        api_key="key",
        prompt="x",
        session=session,  # type: ignore[arg-type]
    )
    with pytest.raises(AIError) as err:
        await provider.async_suggest(product, [])
    assert err.value.code == code


async def test_provider_maps_network_errors(product: Product) -> None:
    session = FakeSession(aiohttp.ClientConnectionError("offline"))
    provider = DeepSeekProvider(
        endpoint="https://api.example.test",
        model="deepseek-flash",
        api_key="key",
        prompt="x",
        session=session,  # type: ignore[arg-type]
    )
    with pytest.raises(AIError) as err:
        await provider.async_suggest(product, [])
    assert err.value.code == "ai_unavailable"


async def test_provider_requires_api_key(product: Product) -> None:
    provider = DeepSeekProvider(
        endpoint="https://api.example.test", model="m", api_key="", prompt="x"
    )
    with pytest.raises(AIError) as err:
        await provider.async_suggest(product, [])
    assert err.value.code == "ai_not_configured"


async def test_ai_error_does_not_mutate_store(tmp_path: Path) -> None:
    store = CatalogStore(data_dir=tmp_path)
    await store.async_load()
    service = ProductService(store)
    created = await service.async_create({"name": "Schild", "category": "LA"})
    await service.async_add_image(
        created.article_number,
        filename="bild.jpg",
        content_type="image/jpeg",
        data=_jpeg_with_metadata(),
    )
    before = json.dumps(store.data, sort_keys=True, ensure_ascii=False)

    failing_provider = DeepSeekProvider(
        endpoint="https://api.example.test",
        model="deepseek-flash",
        api_key="key",
        prompt="x",
        session=FakeSession(FakeResponse(429, {})),  # type: ignore[arg-type]
    )
    ai = AIService(store, provider_factory=lambda: failing_provider)
    with pytest.raises(AIError):
        await ai.async_suggest_description(created.article_number)

    after = json.dumps(store.data, sort_keys=True, ensure_ascii=False)
    assert before == after
    assert (await service.async_get(created.article_number)).description == ""


async def test_ai_service_never_persists_suggestion(tmp_path: Path) -> None:
    store = CatalogStore(data_dir=tmp_path)
    await store.async_load()
    service = ProductService(store)
    created = await service.async_create({"name": "Schild", "category": "LA", "description": "Alt"})
    before = json.dumps(store.data, sort_keys=True, ensure_ascii=False)

    provider = DeepSeekProvider(
        endpoint="https://api.example.test",
        model="deepseek-flash",
        api_key="key",
        prompt="x",
        session=FakeSession(  # type: ignore[arg-type]
            FakeResponse(200, {"choices": [{"message": {"content": "Vorschlag"}}]})
        ),
    )
    ai = AIService(store, provider_factory=lambda: provider)
    assert await ai.async_suggest_description(created.article_number) == "Vorschlag"
    assert json.dumps(store.data, sort_keys=True, ensure_ascii=False) == before
