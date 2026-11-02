"""Tests für die Bildverkleinerung (:mod:`frickeldave_store.imaging`)."""

from __future__ import annotations

import builtins
import io

import pytest
from image_helpers import exif_bytes, gif_bytes, image_bytes, jpeg_bytes, png_bytes
from PIL import Image

from custom_components.frickeldave_store import imaging
from custom_components.frickeldave_store.imaging import (
    MAX_LONG_EDGE,
    format_bytes,
    optimize_image_sync,
)
from custom_components.frickeldave_store.models import ValidationError


def _open(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def test_large_jpeg_is_scaled_to_max_long_edge() -> None:
    result = optimize_image_sync(jpeg_bytes((4032, 3024), quality=95), "image/jpeg")

    assert result.resized is True
    assert result.content_type == "image/jpeg"
    assert result.suffix == ".jpg"
    assert max(result.width, result.height) == MAX_LONG_EDGE
    assert (result.width, result.height) == (1600, 1200)
    assert (result.original_width, result.original_height) == (4032, 3024)
    assert len(result.data) < result.original_bytes
    assert "verkleinert" in result.message and "1600×1200" in result.message


def test_jpeg_is_progressive_and_metadata_is_removed() -> None:
    source = image_bytes("JPEG", (2000, 1500), "RGB", quality=95, exif=exif_bytes())

    result = optimize_image_sync(source, "image/jpeg")
    stored = _open(result.data)

    assert stored.format == "JPEG"
    assert stored.info.get("progressive") in {1}
    assert not stored.getexif()
    assert "icc_profile" not in stored.info


def test_png_with_alpha_becomes_webp_and_keeps_transparency() -> None:
    source = png_bytes((2400, 1200), alpha=True)

    result = optimize_image_sync(source, "image/png")
    stored = _open(result.data)

    assert result.resized is True
    assert result.content_type == "image/webp"
    assert result.suffix == ".webp"
    assert stored.format == "WEBP"
    assert stored.convert("RGBA").getpixel((10, 10))[3] == 0


def test_png_without_alpha_becomes_jpeg() -> None:
    result = optimize_image_sync(png_bytes((2000, 1000), alpha=False), "image/png")

    assert result.content_type == "image/jpeg"
    assert result.suffix == ".jpg"
    assert _open(result.data).format == "JPEG"


def test_gif_is_passed_through_byte_identical() -> None:
    source = gif_bytes((120, 90), frames=3)

    result = optimize_image_sync(source, "image/gif")

    assert result.resized is False
    assert result.data == source
    assert result.content_type == "image/gif"
    assert result.width == 120 and result.height == 90
    assert "unverändert" in result.message


def test_animated_webp_is_passed_through() -> None:
    source = image_bytes("WEBP", (100, 100), "RGB", frames=3)

    result = optimize_image_sync(source, "image/webp")

    assert result.resized is False
    assert result.data == source


def test_small_image_is_not_reencoded() -> None:
    source = jpeg_bytes((640, 480), quality=60)

    result = optimize_image_sync(source, "image/jpeg")

    assert result.resized is False
    assert result.data == source
    assert result.message.startswith("Bild unverändert übernommen")


def test_no_upscaling_for_small_images() -> None:
    source = jpeg_bytes((320, 200), quality=60)

    result = optimize_image_sync(source, "image/jpeg")

    assert (result.width, result.height) == (320, 200)


def test_tall_image_is_limited_on_the_long_edge() -> None:
    result = optimize_image_sync(jpeg_bytes((900, 3200)), "image/jpeg")

    assert (result.width, result.height) == (450, 1600)


def test_broken_image_raises_german_validation_error() -> None:
    with pytest.raises(ValidationError) as err:
        optimize_image_sync(b"kein bild", "image/jpeg")

    assert err.value.code == "image_invalid"
    assert "Bild" in err.value.message
    assert "Traceback" not in err.value.message


def test_empty_upload_raises_validation_error() -> None:
    with pytest.raises(ValidationError) as err:
        optimize_image_sync(b"", "image/jpeg")

    assert err.value.code == "image_invalid"


def test_pixel_limit_blocks_decompression_bombs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(imaging, "MAX_IMAGE_PIXELS", 100)

    with pytest.raises(ValidationError) as err:
        optimize_image_sync(png_bytes((200, 200)), "image/png")

    assert err.value.code == "image_invalid"
    assert "Megapixel" in err.value.message


def test_missing_pillow_fails_with_german_message(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError("No module named 'PIL'")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ValidationError) as err:
        optimize_image_sync(b"egal", "image/jpeg")

    assert err.value.code == "pillow_missing"
    assert "Pillow" in err.value.message


def test_optimizer_is_idempotent_on_its_own_output() -> None:
    first = optimize_image_sync(jpeg_bytes((2400, 1800), quality=95), "image/jpeg")
    second = optimize_image_sync(first.data, first.content_type)

    assert first.resized is True
    assert second.resized is False
    assert second.data == first.data


def test_format_bytes_uses_german_notation() -> None:
    assert format_bytes(12 * 1024 * 1024) == "12,0 MB"
    assert format_bytes(285_000) == "278 KB"
    assert format_bytes(1) == "1 Byte"
