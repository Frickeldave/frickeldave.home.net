"""Registrierung der HA-Panels und der statischen Frontend-Dateien.

Zwei Panels teilen sich das Frontend-Verzeichnis:

- ``frickeldave-store`` – Seitenleisten-Panel (unverändert, ``require_admin=False``).
- ``frickeldave-store-config`` – Konfigurationspanel am Zahnrad des
  Integrationseintrags (``config_panel_domain``), nur für Administratoren.

Die Panel-JS-Dateien gehören dem Frontend-Team (``frontend/``). Diese Datei legt
dort bewusst nichts an; fehlt ein Modul, bleibt die Integration funktionsfähig
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
    CONFIG_PANEL_MODULE_URL,
    CONFIG_PANEL_URL_PATH,
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
_ATTR_PANEL_REGISTERED = f"{DOMAIN}_panel_registered"
_ATTR_CONFIG_PANEL_REGISTERED = f"{DOMAIN}_config_panel_registered"

CONFIG_PANEL_WEBCOMPONENT = "frickeldave-store-config-panel"


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
    """Beide Panels idempotent registrieren (Seitenleiste und Konfiguration)."""

    await async_register_static(hass)
    config: dict[str, Any] = {
        "api_prefix": "/api/frickeldave_store",
    }

    if not hass.data.get(_ATTR_PANEL_REGISTERED):
        if not (frontend_dir() / "frickeldave-store-panel.js").is_file():
            _LOGGER.warning(
                "Panel-Modul %s noch nicht vorhanden; Panel wird trotzdem registriert.",
                PANEL_MODULE_URL,
            )
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
        hass.data[_ATTR_PANEL_REGISTERED] = True

    if not hass.data.get(_ATTR_CONFIG_PANEL_REGISTERED):
        if not (frontend_dir() / "frickeldave-store-config-panel.js").is_file():
            _LOGGER.warning(
                "Konfigurations-Panel-Modul %s noch nicht vorhanden; Panel wird "
                "trotzdem registriert.",
                CONFIG_PANEL_MODULE_URL,
            )
        # `config_panel_domain` verlinkt das Zahnrad am Integrationseintrag auf
        # `/{CONFIG_PANEL_URL_PATH}?config_entry=<id>` statt auf den Options-Flow.
        await _async_register_panel(
            hass,
            frontend_url_path=CONFIG_PANEL_URL_PATH,
            webcomponent_name=CONFIG_PANEL_WEBCOMPONENT,
            module_url=CONFIG_PANEL_MODULE_URL,
            embed_iframe=False,
            require_admin=True,
            config=config,
            config_panel_domain=DOMAIN,
        )
        hass.data[_ATTR_CONFIG_PANEL_REGISTERED] = True


async def async_unregister_panel(hass: HomeAssistant) -> None:
    """Beide Panels entfernen, unabhängig davon, welches registriert wurde."""

    for key, url_path in (
        (_ATTR_PANEL_REGISTERED, PANEL_URL_PATH),
        (_ATTR_CONFIG_PANEL_REGISTERED, CONFIG_PANEL_URL_PATH),
    ):
        if not hass.data.pop(key, False):
            continue
        try:
            frontend.async_remove_panel(hass, url_path)
        except Exception:  # pragma: no cover - Panel war nie im Frontend
            _LOGGER.debug("Panel %s konnte nicht entfernt werden", url_path, exc_info=True)
