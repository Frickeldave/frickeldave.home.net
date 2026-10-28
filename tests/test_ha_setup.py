"""Home-Assistant-Integrationstests: Config Flow, Setup, Panel und Speicherort."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from homeassistant.components import frontend
from homeassistant.config_entries import ConfigEntryState
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.frickeldave_store import FrickeldaveStoreRuntime
from custom_components.frickeldave_store.const import (
    CONFIG_PANEL_MODULE_URL,
    DOMAIN,
    FRONTEND_URL_PATH,
    PANEL_MODULE_URL,
    STORAGE_KEY,
    STORAGE_MINOR_VERSION,
    STORAGE_PATH,
    STORAGE_VERSION,
    VERSION,
)
from custom_components.frickeldave_store.models import SCHEMA_VERSION

OPTIONS: dict[str, Any] = {
    "ai_endpoint": "https://api.deepseek.com/chat/completions",
    "ai_model": "deepseek-flash",
    "ai_prompt": "Generiere mir eine schöne Beschreibung des Artikels für meinen Handmade-Shop.",
    "export_path": "frickeldave_store/export",
}


async def _prepare_hass(hass: Any) -> None:
    assert await async_setup_component(hass, "http", {})
    assert await async_setup_component(hass, "frontend", {})


async def _setup_entry(hass: Any) -> MockConfigEntry:
    await _prepare_hass(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"users": "user-1: product"},
        options=OPTIONS,
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_config_flow_creates_single_entry(hass: Any, enable_custom_integrations: Any) -> None:
    await _prepare_hass(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "form"
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"users": "user-1: product,app_admin"}
    )
    await hass.async_block_till_done()
    assert result["type"] == "create_entry"

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    assert entries[0].state is ConfigEntryState.LOADED

    runtime = hass.data[DOMAIN]
    assert isinstance(runtime, FrickeldaveStoreRuntime)
    assert runtime.permissions.can("user-1", "product")
    assert runtime.permissions.can("user-1", "app_admin")
    assert not runtime.permissions.can("user-1", "finance")
    assert runtime.permissions.can("someone-else", "product") is False


async def test_setup_registers_panel_with_required_contract(
    hass: Any, enable_custom_integrations: Any
) -> None:
    await _setup_entry(hass)

    panels = hass.data[frontend.DATA_PANELS]
    assert "frickeldave-store" in panels
    panel = panels["frickeldave-store"]
    assert panel.sidebar_title == "Frickeldave Store"
    assert panel.sidebar_icon == "mdi:store-cog"
    assert panel.require_admin is False
    assert panel.component_name == "custom"
    custom = panel.config["_panel_custom"]
    assert custom["name"] == "frickeldave-store-panel"
    assert custom["module_url"] == PANEL_MODULE_URL
    assert custom["embed_iframe"] is False
    assert panel.config["api_prefix"] == "/api/frickeldave_store"


async def test_setup_registers_config_panel_for_entry_gear(
    hass: Any, enable_custom_integrations: Any
) -> None:
    await _setup_entry(hass)

    panels = hass.data[frontend.DATA_PANELS]
    assert "frickeldave-store-config" in panels
    panel = panels["frickeldave-store-config"]
    assert panel.require_admin is True
    assert panel.config_panel_domain == DOMAIN
    custom = panel.config["_panel_custom"]
    assert custom["name"] == "frickeldave-store-config-panel"
    assert custom["module_url"] == CONFIG_PANEL_MODULE_URL
    assert custom["embed_iframe"] is False
    assert panel.config["api_prefix"] == "/api/frickeldave_store"


async def test_panel_module_urls_are_cache_busted_by_version(
    hass: Any, enable_custom_integrations: Any
) -> None:
    """HACS-Updates muessen den Browser-Cache umgehen (Issue #289)."""

    await _setup_entry(hass)

    panels = hass.data[frontend.DATA_PANELS]
    expected = {
        "frickeldave-store": f"/{FRONTEND_URL_PATH}/frickeldave-store-panel.js?v={VERSION}",
        "frickeldave-store-config": (
            f"/{FRONTEND_URL_PATH}/frickeldave-store-config-panel.js?v={VERSION}"
        ),
    }
    for panel_key, expected_url in expected.items():
        module_url = panels[panel_key].config["_panel_custom"]["module_url"]
        assert module_url == expected_url
        assert f"?v={VERSION}" in module_url


async def test_panel_registration_is_idempotent(hass: Any, enable_custom_integrations: Any) -> None:
    await _setup_entry(hass)
    from custom_components.frickeldave_store.panel import async_register_panel

    # Ein zweiter Aufruf darf weder das Panel überschreiben noch einen Fehler werfen.
    await async_register_panel(hass)
    panels = hass.data[frontend.DATA_PANELS]
    assert "frickeldave-store" in panels
    assert "frickeldave-store-config" in panels


async def test_unload_removes_panels(hass: Any, enable_custom_integrations: Any) -> None:
    entry = await _setup_entry(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert "frickeldave-store" not in hass.data[frontend.DATA_PANELS]
    assert "frickeldave-store-config" not in hass.data[frontend.DATA_PANELS]


async def test_store_and_views_are_registered(
    hass: Any, enable_custom_integrations: Any, hass_storage: dict[str, Any]
) -> None:
    entry = await _setup_entry(hass)

    runtime: FrickeldaveStoreRuntime = hass.data[DOMAIN]
    product = await runtime.products.async_create({"name": "Schild", "category": "LA"})
    await hass.async_block_till_done()

    # Die `hass`-Fixture ersetzt HA-Storage durch `hass_storage`; Dateisystem-Assertions
    # wären hier von Artefakten früherer Läufe abhängig.
    assert Path(runtime.store._backend.path) == Path(hass.config.path(STORAGE_PATH, STORAGE_KEY))
    persisted = hass_storage[STORAGE_KEY]
    assert persisted["version"] == STORAGE_VERSION
    assert persisted["minor_version"] == STORAGE_MINOR_VERSION
    assert persisted["data"]["schemaVersion"] == SCHEMA_VERSION
    assert [item["articleNumber"] for item in persisted["data"]["products"]] == [
        product.article_number
    ]

    image_path = runtime.store.images.path_for("LAH3K3", "LAH3K3-01.jpg")
    assert image_path.parent.parent == Path(hass.config.path("frickeldave_store", "images"))
    assert hass.data[f"{DOMAIN}_views_registered"] is True
    assert hass.data[f"{DOMAIN}_websocket_registered"] is True
    assert entry.entry_id


async def test_options_update_revokes_permission_immediately(
    hass: Any, enable_custom_integrations: Any
) -> None:
    entry = await _setup_entry(hass)
    runtime: FrickeldaveStoreRuntime = hass.data[DOMAIN]
    assert runtime.permissions.can("user-1", "product")

    hass.config_entries.async_update_entry(entry, options={**OPTIONS, "users": "user-1: "})
    await hass.async_block_till_done()
    assert runtime.permissions.can("user-1", "product") is False


async def test_config_entry_migration_updates_version(
    hass: Any, enable_custom_integrations: Any
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"users": ""},
        options=OPTIONS,
        unique_id=DOMAIN,
        version=0,
        minor_version=1,
    )
    entry.add_to_hass(hass)
    from custom_components.frickeldave_store import async_migrate_entry

    assert await async_migrate_entry(hass, entry)
    assert entry.version == 1
    assert entry.minor_version == 1
