"""Konstanten der Integration ``frickeldave_store``."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "frickeldave_store"
NAME: Final = "Frickeldave Store"
MANUFACTURER: Final = "Frickeldave"
VERSION: Final = "0.1.2"
ISSUE_URL: Final = "https://github.com/Frickeldave/frickeldave.github.io/issues/289"
REPOSITORY_URL: Final = "https://github.com/Frickeldave/frickeldave.home.net"

CONFIG_ENTRY_TITLE: Final = "Frickeldave Store"

#: HA-Store-Schlüssel und -Pfad. HA legt die Datei unter ``config/<STORAGE_PATH>/<STORAGE_KEY>`` ab.
STORAGE_KEY: Final = "frickeldave_store.data"
STORAGE_PATH: Final = "frickeldave_store"
STORAGE_VERSION: Final = 1
STORAGE_MINOR_VERSION: Final = 1

#: Datenverzeichnis unterhalb von ``config/`` (Backups des HA-Containers enthalten es).
DATA_DIR_NAME: Final = "frickeldave_store"
IMAGES_DIR_NAME: Final = "images"
BACKUP_DIR_NAME: Final = "backups"
EXPORT_DIR_NAME: Final = "export"

FRONTEND_URL_PATH: Final = "frickeldave_store_frontend"
PANEL_URL_PATH: Final = "frickeldave-store"
PANEL_MODULE_URL: Final = f"/{FRONTEND_URL_PATH}/frickeldave-store-panel.js"
PANEL_TITLE: Final = "Frickeldave Store"
PANEL_ICON: Final = "mdi:store-cog"

API_PREFIX: Final = "/api/frickeldave_store"

DEFAULT_AI_ENDPOINT: Final = "https://api.deepseek.com/chat/completions"
DEFAULT_AI_MODEL: Final = "deepseek-flash"
DEFAULT_AI_PROMPT: Final = (
    "Generiere mir eine schöne Beschreibung des Artikels für meinen Handmade-Shop."
)

DEFAULT_EXPORT_PATH: Final = "frickeldave_store/export"
