"""Speicherort der Katalogdaten: ``config``, ``share`` oder ``media``.

Der Speicherort ist eine Config-Entry-Option (``data_path``). ``config`` bleibt
der Standard und ist in Home-Assistant-Backups enthalten. ``share`` und ``media``
liegen außerhalb des Config-Ordners und sind damit nicht Teil eines Backups
(``exclude_from_backup``).

Es werden ausschließlich die drei bekannten Werte akzeptiert – niemals freie
Pfade. Dadurch sind Pfad-Traversal und versehentliche Fremdordner ausgeschlossen.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Final

from .const import BACKUP_DIR_NAME, DATA_DIR_NAME, IMAGES_DIR_NAME, STORAGE_KEY
from .models import ConflictError, ValidationError

_LOGGER = logging.getLogger(__name__)

DATA_PATH_CONFIG: Final = "config"
DATA_PATH_SHARE: Final = "share"
DATA_PATH_MEDIA: Final = "media"
DATA_PATH_VALUES: Final[tuple[str, ...]] = (
    DATA_PATH_CONFIG,
    DATA_PATH_SHARE,
    DATA_PATH_MEDIA,
)

#: Basisverzeichnisse der HA-Container-Mounts. In Tests ersetzbar (tmp_path),
#: damit niemals echtes ``/share`` oder ``/media`` berührt wird.
SHARE_ROOT: Final = Path("/share")
MEDIA_ROOT: Final = Path("/media")

DATA_PATH_LABELS: Final[dict[str, str]] = {
    DATA_PATH_CONFIG: "Konfigurationsordner (config)",
    DATA_PATH_SHARE: "/share",
    DATA_PATH_MEDIA: "/media",
}

DATA_PATH_DESCRIPTIONS: Final[dict[str, str]] = {
    DATA_PATH_CONFIG: (
        "Standard: Im Home-Assistant-Backup enthalten. Kein zusätzliches Volume nötig."
    ),
    DATA_PATH_SHARE: (
        "Nicht im Home-Assistant-Backup enthalten. Docker: Volume für /share einbinden "
        "und separat sichern."
    ),
    DATA_PATH_MEDIA: (
        "Nicht im Home-Assistant-Backup enthalten. Docker: Volume für /media einbinden "
        "und separat sichern."
    ),
}


def normalise_data_path(value: Any) -> str:
    """Option prüfen und als bekannten Wert zurückgeben (kein freier Pfad)."""

    if value is None or value == "":
        return DATA_PATH_CONFIG
    if not isinstance(value, str) or value not in DATA_PATH_VALUES:
        raise ValidationError(
            "Unbekannter Speicherort. Erlaubt sind: config, share, media.",
        )
    return value


def data_path_from_entry(entry: Any) -> str:
    """``data_path`` aus Config-Entry-Data/-Options lesen (Default ``config``)."""

    if entry is None:
        return DATA_PATH_CONFIG
    data = getattr(entry, "data", None) or {}
    options = getattr(entry, "options", None) or {}
    return normalise_data_path({**data, **options}.get("data_path", DATA_PATH_CONFIG))


def _root_for(data_path: str, share_root: Path | None, media_root: Path | None) -> Path | None:
    if data_path == DATA_PATH_CONFIG:
        return None
    if data_path == DATA_PATH_SHARE:
        return Path(share_root) if share_root is not None else SHARE_ROOT
    return Path(media_root) if media_root is not None else MEDIA_ROOT


def resolve_data_dir(
    hass: Any,
    data_path: str,
    *,
    share_root: Path | None = None,
    media_root: Path | None = None,
) -> Path:
    """Absoluten Datenordner für eine Option bestimmen (ohne ihn anzulegen)."""

    value = normalise_data_path(data_path)
    if value == DATA_PATH_CONFIG:
        return Path(hass.config.path(DATA_DIR_NAME))
    root = _root_for(value, share_root, media_root)
    assert root is not None  # nur config hat keine Basis
    return root / DATA_DIR_NAME


def ensure_data_dir_sync(path: Path, base: Path | None = None) -> None:
    """Ordner anlegen und Schreibbarkeit prüfen; klare Fehler statt stiller Fallback."""

    if base is not None and not base.is_dir():
        raise ValidationError(
            f"'{base}' ist nicht eingebunden. Der Speicherort ist nur verfügbar, wenn das "
            "Verzeichnis als Volume bereitgestellt wird.",
        )
    try:
        path.mkdir(parents=True, exist_ok=True)
        descriptor, probe = tempfile.mkstemp(dir=str(path), prefix=".write-test-")
        try:
            os.close(descriptor)
        finally:
            Path(probe).unlink(missing_ok=True)
    except OSError as err:
        raise ValidationError(
            f"'{path}' ist nicht beschreibbar: {err.strerror or err}",
        ) from err


async def async_prepare_data_dir(hass: Any, path: Path, base: Path | None = None) -> None:
    """``ensure_data_dir_sync`` im Executor ausführen."""

    await hass.async_add_executor_job(ensure_data_dir_sync, path, base)


def has_existing_data(path: Path) -> bool:
    """Ziel enthält bereits Katalogdaten (Store-Datei oder Bilder)."""

    if (path / STORAGE_KEY).is_file():
        return True
    images = path / IMAGES_DIR_NAME
    return images.is_dir() and any(images.iterdir())


def migrate_data_sync(source: Path, target: Path) -> None:
    """Store-Datei, Bilder und Snapshots kopieren; Quelle bleibt erhalten."""

    if source == target:
        return
    if has_existing_data(target):
        raise ConflictError(
            f"Im Zielordner '{target}' liegen bereits Katalogdaten. "
            "Bitte den Ordner leeren oder einen anderen Speicherort wählen.",
        )
    target.mkdir(parents=True, exist_ok=True)
    store_file = source / STORAGE_KEY
    if store_file.is_file():
        shutil.copy2(store_file, target / STORAGE_KEY)
    for name in (IMAGES_DIR_NAME, BACKUP_DIR_NAME):
        source_dir = source / name
        if source_dir.is_dir():
            shutil.copytree(source_dir, target / name, dirs_exist_ok=True)
    _LOGGER.info("Katalogdaten von %s nach %s kopiert (Quelle bleibt erhalten)", source, target)


async def async_migrate_data_dir(hass: Any, source: Path, target: Path) -> None:
    """Migration im Executor ausführen."""

    await hass.async_add_executor_job(migrate_data_sync, source, target)


def storage_payload(
    hass: Any,
    data_path: str,
    *,
    share_root: Path | None = None,
    media_root: Path | None = None,
) -> dict[str, Any]:
    """Speicherort-Abschnitt für ``GET/PATCH /settings`` (und das Config-Panel)."""

    value = normalise_data_path(data_path)
    effective = resolve_data_dir(hass, value, share_root=share_root, media_root=media_root)
    backup_included = value == DATA_PATH_CONFIG
    return {
        "data_path": value,
        "effective_path": str(effective),
        "backup_included": backup_included,
        "exclude_from_backup": not backup_included,
        "paths": [
            {"label": "Metadaten", "path": str(effective / STORAGE_KEY)},
            {"label": "Bilder", "path": str(effective / IMAGES_DIR_NAME)},
            {"label": "Snapshots", "path": str(effective / BACKUP_DIR_NAME)},
        ],
        "choices": [
            {
                "id": choice,
                "label": DATA_PATH_LABELS[choice],
                "description": DATA_PATH_DESCRIPTIONS[choice],
                "backup_included": choice == DATA_PATH_CONFIG,
            }
            for choice in DATA_PATH_VALUES
        ],
    }


def data_path_for_dir(
    hass: Any,
    directory: Path,
    *,
    share_root: Path | None = None,
    media_root: Path | None = None,
) -> str:
    """Option ermitteln, die zum aktuell geöffneten Datenordner gehört."""

    for choice in DATA_PATH_VALUES:
        if (
            resolve_data_dir(hass, choice, share_root=share_root, media_root=media_root)
            == directory
        ):
            return choice
    return DATA_PATH_CONFIG


def base_dir_for(
    data_path: str,
    *,
    share_root: Path | None = None,
    media_root: Path | None = None,
) -> Path | None:
    """Basis-Mount eines Speicherorts (``None`` für ``config``)."""

    return _root_for(normalise_data_path(data_path), share_root, media_root)


__all__ = [
    "DATA_PATH_CONFIG",
    "DATA_PATH_LABELS",
    "DATA_PATH_MEDIA",
    "DATA_PATH_SHARE",
    "DATA_PATH_VALUES",
    "MEDIA_ROOT",
    "SHARE_ROOT",
    "async_migrate_data_dir",
    "async_prepare_data_dir",
    "base_dir_for",
    "data_path_for_dir",
    "data_path_from_entry",
    "ensure_data_dir_sync",
    "has_existing_data",
    "migrate_data_sync",
    "normalise_data_path",
    "resolve_data_dir",
    "storage_payload",
]
