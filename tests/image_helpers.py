"""Hilfsfunktionen für Bildtests: echte, dekodierbare Bilddaten erzeugen."""

from __future__ import annotations

import io
from typing import Any

from PIL import Image


def image_bytes(
    fmt: str = "JPEG",
    size: tuple[int, int] = (800, 600),
    mode: str = "RGB",
    *,
    color: Any = None,
    quality: int = 90,
    exif: bytes | None = None,
    frames: int = 1,
) -> bytes:
    """Bilddaten im gewünschten Format erzeugen (Standard: dekodierbares JPEG)."""

    fill = color
    if fill is None:
        fill = (200, 40, 40) if mode == "RGB" else 0
    buffer = io.BytesIO()
    if frames > 1:
        images = [
            Image.new(mode, size, fill if index % 2 == 0 else (30, 60, 200))
            for index in range(frames)
        ]
        images[0].save(
            buffer,
            format=fmt,
            save_all=True,
            append_images=images[1:],
            duration=100,
            loop=0,
        )
        return buffer.getvalue()
    image = Image.new(mode, size, fill)
    options: dict[str, Any] = {}
    if exif is not None and fmt.upper() in {"JPEG", "WEBP", "PNG"}:
        options["exif"] = exif
    if fmt.upper() in {"JPEG", "WEBP"}:
        options["quality"] = quality
    image.save(buffer, format=fmt, **options)
    return buffer.getvalue()


def jpeg_bytes(size: tuple[int, int] = (800, 600), *, quality: int = 90) -> bytes:
    return image_bytes("JPEG", size, "RGB", quality=quality)


def png_bytes(size: tuple[int, int] = (800, 600), *, alpha: bool = False) -> bytes:
    return image_bytes("PNG", size, "RGBA" if alpha else "RGB")


def webp_bytes(size: tuple[int, int] = (800, 600), *, alpha: bool = False) -> bytes:
    return image_bytes("WEBP", size, "RGBA" if alpha else "RGB")


def gif_bytes(size: tuple[int, int] = (200, 150), *, frames: int = 1) -> bytes:
    return image_bytes("GIF", size, "RGB", frames=frames)


def exif_bytes(**tags: Any) -> bytes:
    """EXIF-Block mit den angegebenen Tags (Standard: Modell, Künstler, Datum)."""

    exif = Image.Exif()
    defaults: dict[int, Any] = {
        0x0110: "Testkamera",  # Model
        0x013B: "David Koenig",  # Artist
        0x0132: "2026:11:02 15:40:00",  # DateTime
    }
    for key, value in {**defaults, **tags}.items():
        exif[int(key)] = value
    return exif.tobytes()
