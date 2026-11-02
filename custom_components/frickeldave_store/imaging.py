"""Bildverarbeitung für Uploads und Bestandsbilder (Issue #289).

Regeln (siehe ``.squad/decisions/inbox/bruce-289-bildoptimierung.md``):

* Lange Kante höchstens :data:`MAX_LONG_EDGE` Pixel – niemals hochskalieren.
* JPEG wird mit Qualität :data:`JPEG_QUALITY` progressiv neu geschrieben,
  EXIF und weitere Metadaten werden entfernt.
* PNG/WebP mit Transparenz werden zu WebP (:data:`WEBP_QUALITY`), ohne
  Transparenz zu JPEG.
* GIF und animierte WebP bleiben unverändert.
* Bilder, die bereits passen (lange Kante ≤ 1600 px **und** ≤ 400 KiB),
  werden byteweise übernommen – kein Re-Encoding, kein Qualitätsverlust.

Pillow ist Core-Abhängigkeit von Home Assistant und wird deshalb **nicht** in
``manifest.json`` eingetragen. Der Import erfolgt bewusst erst bei Bedarf:
fehlt Pillow, scheitert der Upload mit einer deutschen Meldung statt mit einem
``ImportError`` beim Setup der Integration.

Die Metadaten der vorhandenen Bilder (Bytes, Maße, Inhaltstyp) werden beim
Lesen aus den Dateien ermittelt und prozesslokal gecacht. Der Cache ist nach
Pfad, ``mtime`` und Größe geschlüsselt, damit geänderte Dateien automatisch neu
gelesen werden, und durch :data:`METADATA_CACHE_SIZE` begrenzt, damit er bei
großen Katalogen nicht unbegrenzt wächst.
"""

from __future__ import annotations

import asyncio
import io
import threading
import warnings
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from .models import ValidationError

#: Lange Kante des Ergebnisses in Pixeln.
MAX_LONG_EDGE: Final = 1600
#: Grenze, ab der ein Bild als "zu groß" gilt (400 KiB).
MAX_IMAGE_BYTES: Final = 400 * 1024
JPEG_QUALITY: Final = 82
WEBP_QUALITY: Final = 85
#: Schutz vor Dekomprimierungsbomben: 40 Megapixel reichen für jede Handykamera,
#: ein 10-MB-PNG könnte sonst mehrere Gigabyte Arbeitsspeicher belegen.
MAX_IMAGE_PIXELS: Final = 40_000_000
#: Obergrenze des Metadaten-Caches (Anzahl Dateien).
METADATA_CACHE_SIZE: Final = 4096

CONTENT_TYPE_BY_SUFFIX: Final[dict[str, str]] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

SUFFIX_BY_CONTENT_TYPE: Final[dict[str, str]] = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

_MISSING_PILLOW: Final = (
    "Bildverarbeitung nicht verfügbar: Die Python-Bibliothek Pillow fehlt. "
    "Pillow gehört zu den Home-Assistant-Core-Abhängigkeiten – bitte die "
    "Installation prüfen und Home Assistant neu starten."
)


def pillow() -> Any:
    """Pillow bei Bedarf importieren (kein Import auf Modulebene)."""

    try:
        from PIL import Image  # noqa: PLC0415 - absichtlich lazy
    except ImportError as err:  # pragma: no cover - Pillow ist Core-Abhängigkeit
        raise ValidationError(_MISSING_PILLOW, code="pillow_missing") from err
    return Image


def content_type_for_name(name: str) -> str:
    """Inhaltstyp aus der Dateiendung (Fallback ``application/octet-stream``)."""

    return CONTENT_TYPE_BY_SUFFIX.get(Path(name).suffix.casefold(), "application/octet-stream")


def suffix_for_content_type(content_type: str) -> str:
    """Dateiendung zu einem Inhaltstyp (Fallback ``.jpg``)."""

    return SUFFIX_BY_CONTENT_TYPE.get(content_type.casefold(), ".jpg")


def is_oversized(width: int | None, height: int | None, size: int) -> bool:
    """Zu groß = lange Kante über 1600 px **oder** mehr als 400 KiB."""

    if size > MAX_IMAGE_BYTES:
        return True
    longest = max(width or 0, height or 0)
    return longest > MAX_LONG_EDGE


def format_bytes(count: int) -> str:
    """Deutsche Kurzform: ``12,0 MB``, ``278 KB``, ``900 Bytes``."""

    if count >= 1024 * 1024:
        return f"{count / (1024 * 1024):.1f} MB".replace(".", ",")
    if count >= 1024:
        return f"{round(count / 1024)} KB"
    return f"{count} Byte" if count == 1 else f"{count} Bytes"


@dataclass(frozen=True, slots=True)
class ImageInfo:
    """Größenmetadaten einer Bilddatei (nicht im Store persistiert)."""

    bytes: int
    width: int | None
    height: int | None
    content_type: str
    oversized: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "bytes": self.bytes,
            "width": self.width,
            "height": self.height,
            "content_type": self.content_type,
            "oversized": self.oversized,
        }


@dataclass(frozen=True, slots=True)
class OptimizeResult:
    """Ergebnis der Verkleinerung inklusive Metadaten vorher/nachher."""

    data: bytes
    content_type: str
    suffix: str
    width: int
    height: int
    original_bytes: int
    original_width: int
    original_height: int
    original_content_type: str
    resized: bool
    message: str

    def original_payload(self) -> dict[str, Any]:
        return {
            "bytes": self.original_bytes,
            "width": self.original_width,
            "height": self.original_height,
            "content_type": self.original_content_type,
        }

    def stored_payload(self) -> dict[str, Any]:
        return {
            "bytes": len(self.data),
            "width": self.width,
            "height": self.height,
            "content_type": self.content_type,
        }


def _invalid(reason: str) -> ValidationError:
    return ValidationError(
        f"Das Bild konnte nicht verarbeitet werden: {reason}",
        code="image_invalid",
    )


def _has_alpha(image: Any) -> bool:
    if image.mode in {"RGBA", "LA", "PA"}:
        return True
    if image.mode == "P":
        return "transparency" in image.info
    return bool(image.info.get("transparency"))


def _save(image: Any, target_format: str) -> bytes:
    """Neu kodieren, ohne EXIF/ICC/XMP zu übernehmen."""

    image.info.clear()
    buffer = io.BytesIO()
    if target_format == "WEBP":
        image.save(buffer, format="WEBP", quality=WEBP_QUALITY, method=4)
    else:
        image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    return buffer.getvalue()


def optimize_image_sync(data: bytes, content_type: str) -> OptimizeResult:
    """Rohbytes synchron verkleinern; Aufruf gehört in einen Executor."""

    image_mod = pillow()
    original_content_type = (content_type or "").casefold()
    if original_content_type == "image/jpg":
        original_content_type = "image/jpeg"
    original_bytes = len(data)
    if not data:
        raise _invalid("Die Datei ist leer.")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", image_mod.DecompressionBombWarning)
            with image_mod.open(io.BytesIO(data)) as source:
                width, height = source.size
                if width * height > MAX_IMAGE_PIXELS:
                    raise _invalid(
                        f"Das Bild ist mit {width}×{height} Pixeln zu groß "
                        f"(Grenze: {MAX_IMAGE_PIXELS // 1_000_000} Megapixel)."
                    )
                image_format = (source.format or "").upper()
                animated = bool(getattr(source, "is_animated", False)) or (
                    int(getattr(source, "n_frames", 1) or 1) > 1
                )
                has_alpha = _has_alpha(source)
                source.load()
                unchanged = _unchanged_result(
                    data=data,
                    content_type=original_content_type,
                    image_format=image_format,
                    animated=animated,
                    width=width,
                    height=height,
                    original_bytes=original_bytes,
                )
                if unchanged is not None:
                    return unchanged

                target_format, target_type = _target_format(image_format, has_alpha)
                image = source.copy()
                image.thumbnail((MAX_LONG_EDGE, MAX_LONG_EDGE), image_mod.Resampling.LANCZOS)
                if target_format == "JPEG" and image.mode != "RGB":
                    image = image.convert("RGB")
                elif target_format == "WEBP" and image.mode not in {"RGB", "RGBA"}:
                    image = image.convert("RGBA" if has_alpha else "RGB")
                result = _save(image, target_format)
                new_width, new_height = image.size
    except ValidationError:
        raise
    except (image_mod.DecompressionBombError, image_mod.DecompressionBombWarning) as err:
        raise _invalid(f"Das Bild überschreitet die zulässige Pixelmenge ({err}).") from err
    except (
        image_mod.UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        KeyError,
        EOFError,
    ) as err:
        raise _invalid("Die Datei ist kein lesbares Bild oder beschädigt.") from err

    if result == data:
        # Re-Encoding ohne Byte-Änderung (deterministisch, bereits optimal):
        # als unverändert melden, damit wiederholte Läufe idempotent bleiben.
        return _passthrough(
            data,
            content_type=original_content_type or target_type,
            suffix=suffix_for_content_type(original_content_type or target_type),
            width=width,
            height=height,
            original_bytes=original_bytes,
            message=(
                f"Bild unverändert übernommen ({width}×{height}, {format_bytes(original_bytes)})."
            ),
        )
    message = (
        f"Bild von {format_bytes(original_bytes)} auf {format_bytes(len(result))} "
        f"verkleinert ({width}×{height} → {new_width}×{new_height})."
    )
    return OptimizeResult(
        data=result,
        content_type=target_type,
        suffix=suffix_for_content_type(target_type),
        width=new_width,
        height=new_height,
        original_bytes=original_bytes,
        original_width=width,
        original_height=height,
        original_content_type=original_content_type or target_type,
        resized=True,
        message=message,
    )


def _unchanged_result(
    *,
    data: bytes,
    content_type: str,
    image_format: str,
    animated: bool,
    width: int,
    height: int,
    original_bytes: int,
) -> OptimizeResult | None:
    """Durchreichen, wenn das Format unangetastet bleiben muss oder bereits passt."""

    reason: str | None = None
    if image_format == "GIF":
        reason = "GIF"
    elif animated and image_format == "WEBP":
        reason = "Animiertes WebP"
    if reason is not None:
        return _passthrough(
            data,
            content_type=content_type,
            suffix=f".{image_format.lower()}",
            width=width,
            height=height,
            original_bytes=original_bytes,
            message=(
                f"{reason} unverändert übernommen "
                f"({width}×{height}, {format_bytes(original_bytes)})."
            ),
        )
    if is_oversized(width, height, original_bytes):
        return None
    return _passthrough(
        data,
        content_type=content_type,
        suffix=SUFFIX_BY_CONTENT_TYPE.get(content_type, ".jpg"),
        width=width,
        height=height,
        original_bytes=original_bytes,
        message=(
            f"Bild unverändert übernommen ({width}×{height}, {format_bytes(original_bytes)})."
        ),
    )


def _passthrough(
    data: bytes,
    *,
    content_type: str,
    suffix: str,
    width: int,
    height: int,
    original_bytes: int,
    message: str,
) -> OptimizeResult:
    return OptimizeResult(
        data=data,
        content_type=content_type,
        suffix=suffix,
        width=width,
        height=height,
        original_bytes=original_bytes,
        original_width=width,
        original_height=height,
        original_content_type=content_type,
        resized=False,
        message=message,
    )


def _target_format(image_format: str, has_alpha: bool) -> tuple[str, str]:
    """Zielformat: Transparenz → WebP, sonst JPEG."""

    if has_alpha:
        return "WEBP", "image/webp"
    return "JPEG", "image/jpeg"


async def async_optimize_image(data: bytes, content_type: str) -> OptimizeResult:
    """Verkleinerung im Executor, damit der Event-Loop frei bleibt."""

    return await asyncio.to_thread(optimize_image_sync, data, content_type)


def read_info_sync(path: Path) -> ImageInfo:
    """Metadaten einer Datei lesen (nur Header, kein vollständiges Dekodieren)."""

    content_type = CONTENT_TYPE_BY_SUFFIX.get(path.suffix.casefold(), "application/octet-stream")
    try:
        size = path.stat().st_size
    except OSError:
        return ImageInfo(
            bytes=0, width=None, height=None, content_type=content_type, oversized=False
        )
    width: int | None = None
    height: int | None = None
    try:
        image_mod = pillow()
        with image_mod.open(path) as source:
            width, height = source.size
    except Exception:  # noqa: BLE001 - defekte Dateien (oder fehlendes Pillow) brechen die Liste nicht
        width = None
        height = None
    return ImageInfo(
        bytes=size,
        width=width,
        height=height,
        content_type=content_type,
        oversized=is_oversized(width, height, size),
    )


class MetadataCache:
    """Kleiner LRU-Cache für Bildmetadaten (prozesslokal, threadsicher)."""

    def __init__(self, max_entries: int = METADATA_CACHE_SIZE) -> None:
        self._max_entries = max_entries
        self._entries: OrderedDict[tuple[str, int, int], ImageInfo] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: tuple[str, int, int]) -> ImageInfo | None:
        with self._lock:
            info = self._entries.get(key)
            if info is not None:
                self._entries.move_to_end(key)
            return info

    def put(self, key: tuple[str, int, int], info: ImageInfo) -> None:
        with self._lock:
            self._entries[key] = info
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


_CACHE: Final = MetadataCache()


def metadata_cache() -> MetadataCache:
    """Aktiver Cache (Tests können ihn leeren)."""

    return _CACHE


def inspect_sync(path: Path) -> ImageInfo:
    """Metadaten mit Cache lesen; Schlüssel ist Pfad + mtime + Größe."""

    try:
        stat = path.stat()
    except OSError:
        return read_info_sync(path)
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    info = read_info_sync(path)
    _CACHE.put(key, info)
    return info


def inspect_paths(paths: list[Path]) -> list[ImageInfo]:
    """Mehrere Dateien in einem Durchgang prüfen (Executor-Aufruf)."""

    return [inspect_sync(path) for path in paths]


async def async_inspect_paths(paths: list[Path]) -> list[ImageInfo]:
    """``inspect_paths`` im Executor ausführen."""

    if not paths:
        return []
    return await asyncio.to_thread(inspect_paths, paths)


__all__ = [
    "CONTENT_TYPE_BY_SUFFIX",
    "JPEG_QUALITY",
    "MAX_IMAGE_BYTES",
    "MAX_IMAGE_PIXELS",
    "MAX_LONG_EDGE",
    "METADATA_CACHE_SIZE",
    "WEBP_QUALITY",
    "ImageInfo",
    "MetadataCache",
    "OptimizeResult",
    "async_inspect_paths",
    "async_optimize_image",
    "content_type_for_name",
    "format_bytes",
    "inspect_paths",
    "inspect_sync",
    "is_oversized",
    "metadata_cache",
    "optimize_image_sync",
    "pillow",
    "read_info_sync",
    "suffix_for_content_type",
]
