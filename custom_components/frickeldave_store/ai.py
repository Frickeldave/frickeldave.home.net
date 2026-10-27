"""KI-Dienst für Beschreibungsvorschläge (Anbieter-Abstraktion, DeepSeek).

Vorschläge werden nie automatisch gespeichert. Bilder werden vor dem Upload von
EXIF/GPS-Metadaten befreit; personenbezogene, Kunden- und Finanzdaten werden
nicht übertragen.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any, Protocol

import aiohttp

from .models import Product, StoreError
from .store import CatalogStore

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 60
MAX_IMAGES_PER_REQUEST = 2
MAX_TOKENS = 800

JPEG_APP_MARKERS = frozenset(range(0xE1, 0xF0)) | {0xFE}  # APP1-APP15 + COM
PNG_DROP_CHUNKS = frozenset({b"eXIf", b"iTXt", b"tEXt", b"zTXt", b"tIME"})


class AIError(StoreError):
    """Fehler des KI-Anbieters mit lesbarer Meldung."""

    code = "ai_error"


def strip_metadata(data: bytes, content_type: str = "image/jpeg") -> bytes:
    """EXIF/GPS und Textmetadaten entfernen (JPEG/PNG), sonst unverändert."""

    if content_type in {"image/jpeg", "image/jpg"} or data.startswith(b"\xff\xd8"):
        return _strip_jpeg(data)
    if content_type == "image/png" or data.startswith(b"\x89PNG\r\n\x1a\n"):
        return _strip_png(data)
    return data


def _strip_jpeg(data: bytes) -> bytes:
    if not data.startswith(b"\xff\xd8"):
        return data
    output = bytearray(b"\xff\xd8")
    index = 2
    length = len(data)
    while index + 1 < length:
        if data[index] != 0xFF:
            # Ab hier keine gültige Segmentstruktur: Rest unverändert übernehmen.
            output.extend(data[index:])
            return bytes(output)
        marker = data[index + 1]
        if marker == 0xDA:  # Start of Scan – Bilddaten unverändert anhängen
            output.extend(data[index:])
            return bytes(output)
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            output.extend(data[index : index + 2])
            index += 2
            continue
        if index + 3 >= length:
            output.extend(data[index:])
            return bytes(output)
        segment_length = int.from_bytes(data[index + 2 : index + 4], "big")
        segment_end = index + 2 + segment_length
        if segment_length < 2 or segment_end > length:
            output.extend(data[index:])
            return bytes(output)
        if marker not in JPEG_APP_MARKERS:
            output.extend(data[index:segment_end])
        index = segment_end
    return bytes(output)


def _strip_png(data: bytes) -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"
    if not data.startswith(signature):
        return data
    output = bytearray(signature)
    index = len(signature)
    length = len(data)
    while index + 8 <= length:
        chunk_length = int.from_bytes(data[index : index + 4], "big")
        chunk_type = data[index + 4 : index + 8]
        chunk_end = index + 12 + chunk_length
        if chunk_end > length:
            output.extend(data[index:])
            return bytes(output)
        if chunk_type not in PNG_DROP_CHUNKS:
            output.extend(data[index:chunk_end])
        index = chunk_end
        if chunk_type == b"IEND":
            break
    if index < length:
        output.extend(data[index:])
    return bytes(output)


class AIProvider(Protocol):
    """Anbieter-Abstraktion für Beschreibungsvorschläge."""

    async def async_suggest(self, product: Product, images: list[tuple[bytes, str]]) -> str: ...

    async def async_close(self) -> None: ...


@dataclass
class DeepSeekProvider:
    """DeepSeek-Chat-API mit Bildern als Base64-Data-URLs."""

    endpoint: str
    model: str
    api_key: str
    prompt: str
    timeout: int = REQUEST_TIMEOUT
    session: aiohttp.ClientSession | None = None
    _owns_session: bool = True

    async def async_suggest(self, product: Product, images: list[tuple[bytes, str]]) -> str:
        if not self.api_key:
            raise AIError(
                "Es ist kein API-Key hinterlegt. Bitte in den Einstellungen ergänzen.",
                code="ai_not_configured",
            )
        content: list[dict[str, Any]] = [{"type": "text", "text": self._product_prompt(product)}]
        for data, content_type in images[:MAX_IMAGES_PER_REQUEST]:
            cleaned = strip_metadata(data, content_type)
            encoded = base64.b64encode(cleaned).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{content_type};base64,{encoded}"},
                }
            )
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Du schreibst deutschsprachige Produktbeschreibungen für einen "
                        "Handmade-Shop. Antworte nur mit dem Beschreibungstext, ohne "
                        "Vorrede und ohne Aufzählung von Preisen oder Beständen."
                    ),
                },
                {"role": "user", "content": content},
            ],
            "max_tokens": MAX_TOKENS,
            "temperature": 0.7,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            session = self.session or aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            )
        except Exception as err:  # pragma: no cover - defensive
            raise AIError(f"KI-Anfrage konnte nicht gestartet werden: {err}") from err
        try:
            async with session.post(self.endpoint, json=payload, headers=headers) as response:
                body = await response.json(content_type=None)
                if response.status == 429:
                    raise AIError(
                        "Das Nutzungslimit des KI-Anbieters ist erreicht. "
                        "Bitte später erneut versuchen.",
                        code="ai_rate_limited",
                    )
                if response.status in (401, 403):
                    raise AIError(
                        "Der KI-Anbieter hat die Zugangsdaten abgelehnt.",
                        code="ai_unauthorized",
                    )
                if response.status >= 500:
                    raise AIError(
                        "Der KI-Anbieter ist momentan nicht erreichbar. "
                        "Bitte später erneut versuchen.",
                        code="ai_unavailable",
                    )
                if response.status >= 400:
                    raise AIError(
                        f"Der KI-Anbieter hat die Anfrage abgelehnt (HTTP {response.status}).",
                        code="ai_request_failed",
                    )
                return _extract_suggestion(body)
        except (TimeoutError, aiohttp.ClientError) as err:
            raise AIError(
                "Zeitüberschreitung oder Netzwerkfehler beim KI-Anbieter.",
                code="ai_unavailable",
            ) from err
        finally:
            if self.session is None:
                await session.close()

    async def async_close(self) -> None:
        if self.session is not None:
            await self.session.close()

    def _product_prompt(self, product: Product) -> str:
        lines = [
            self.prompt,
            "",
            f"Name: {product.name}",
            f"Kategorie: {product.category}",
        ]
        if product.size:
            lines.append(f"Größe: {product.size}")
        if product.tags:
            lines.append(f"Tags: {', '.join(product.tags)}")
        if product.customizable:
            lines.append("Personalisierbar: ja")
        return "\n".join(lines)


def _extract_suggestion(body: Any) -> str:
    try:
        choices = body["choices"]
        message = choices[0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as err:
        raise AIError(
            "Der KI-Anbieter hat keine verwertbare Antwort geliefert.",
            code="ai_invalid_response",
        ) from err
    if isinstance(message, list):
        parts = [
            str(part.get("text", ""))
            for part in message
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        message = "\n".join(parts)
    suggestion = str(message or "").strip()
    if not suggestion:
        raise AIError(
            "Der KI-Anbieter hat eine leere Antwort geliefert.",
            code="ai_invalid_response",
        )
    return suggestion


class AIService:
    """Fassade: liest Anbieter-Einstellungen und liefert nur Vorschläge."""

    def __init__(
        self,
        store: CatalogStore,
        *,
        provider_factory: Any | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._store = store
        self._provider_factory = provider_factory
        self._session = session

    def _provider(self) -> AIProvider:
        if self._provider_factory is not None:
            return self._provider_factory()
        settings = self._store.settings["ai"]
        return DeepSeekProvider(
            endpoint=settings.get("endpoint", ""),
            model=settings.get("model", ""),
            api_key=settings.get("api_key", ""),
            prompt=settings.get("prompt", ""),
            session=self._session,
        )

    async def async_suggest_description(self, article_number: str) -> str:
        """Vorschlag erzeugen; der gespeicherte Datenstand bleibt unverändert."""

        product = self._store.get_product(article_number)
        images: list[tuple[bytes, str]] = []
        for image in product.images[:MAX_IMAGES_PER_REQUEST]:
            try:
                data = await self._store.images.async_read(product.article_number, image.name)
            except OSError:
                continue
            images.append((data, _content_type_for(image.name)))
        provider = self._provider()
        return await provider.async_suggest(product, images)


def _content_type_for(name: str) -> str:
    lowered = name.casefold()
    if lowered.endswith(".png"):
        return "image/png"
    if lowered.endswith(".webp"):
        return "image/webp"
    if lowered.endswith(".gif"):
        return "image/gif"
    return "image/jpeg"
