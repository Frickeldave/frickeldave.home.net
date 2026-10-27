"""Registrierung des HA-Panels und der statischen Frontend-Dateien.

Die Panel-JS-Datei gehört dem Frontend-Team (``frontend/``). Diese Datei legt
dort bewusst nichts an; fehlt das Modul, bleibt die Integration funktionsfähig
und die API nutzbar.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from homeassistant.components import frontend
from homeassistant.components.panel_custom import async_register_panel as _async_register_panel
from homeassistant.core import HomeAssistant

from .const import (
    DOMAIN,
    FRONTEND_URL_PATH,
    PANEL_ICON,
    PANEL_MODULE_URL,
    PANEL_TITLE,
    PANEL_URL_PATH,
)

_LOGGER = logging.getLogger(__name__)

STATIC_URL = f"/{FRONTEND_URL_PATH}"
_ATTR_REGISTERED = f"{DOMAIN}_static_registered"


def frontend_dir() -> Path:
    return Path(__file__).parent / "frontend"


async def async_register_static(hass: HomeAssistant) -> None:
    directory = frontend_dir()
    if not directory.is_dir():
        _LOGGER.warning(
            "Frontend-Verzeichnis %s fehlt; das Panel bleibt bis zum Deployment "
            "der Panel-JS-Datei ohne Inhalt.",
            directory,
        )
        return
    if hass.data.get(_ATTR_REGISTERED):
        return
    try:
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(directory), cache_headers=False)]
        )
    except ImportError:  # pragma: no cover - ältere HA-Versionen
        hass.http.register_static_path(STATIC_URL, str(directory), cache_headers=False)
    hass.data[_ATTR_REGISTERED] = True


async def async_register_panel(hass: HomeAssistant) -> None:
    await async_register_static(hass)
    if hass.data.get(f"{DOMAIN}_panel_registered"):
        return
    if not (frontend_dir() / "frickeldave-store-panel.js").is_file():
        _LOGGER.warning(
            "Panel-Modul %s noch nicht vorhanden; Panel wird trotzdem registriert.",
            PANEL_MODULE_URL,
        )
    config: dict[str, Any] = {
        "api_prefix": "/api/frickeldave_store",
    }
    await _async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name="frickeldave-store-panel",
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        module_url=PANEL_MODULE_URL,
        embed_iframe=False,
        require_admin=False,
        config=config,
    )
    hass.data[f"{DOMAIN}_panel_registered"] = True


async def async_unregister_panel(hass: HomeAssistant) -> None:
    if not hass.data.pop(f"{DOMAIN}_panel_registered", False):
        return
    try:
        frontend.async_remove_panel(hass, PANEL_URL_PATH)
    except Exception:  # pragma: no cover - Panel war nie im Frontend
        _LOGGER.debug("Panel konnte nicht entfernt werden", exc_info=True)
