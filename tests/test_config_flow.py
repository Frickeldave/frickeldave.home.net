"""Regressionstests: Formular-Schemas müssen für die HTTP-API serialisierbar sein.

HA serialisiert ``result["data_schema"]`` im HTTP-Layer mit ``probatio.to_field_list``
(HA 2026.10+) bzw. ``voluptuous_serialize.convert`` (ältere Versionen). Ein
Validator als Schema-Wert führt dort zu ``unable to serialize schema`` und damit
zu "Config flow could not be loaded: 500 Internal Server Error".
"""

from __future__ import annotations

from typing import Any

from homeassistant.helpers import config_validation as cv
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.frickeldave_store.const import DOMAIN

OPTIONS: dict[str, Any] = {
    "users": "user-1: product",
    "ai_endpoint": "https://api.deepseek.com/chat/completions",
    "ai_model": "deepseek-flash",
    "ai_prompt": "Generiere mir eine schöne Beschreibung des Artikels für meinen Handmade-Shop.",
    "export_path": "frickeldave_store/export",
}


def _serialise_schema(schema: Any) -> list[dict[str, Any]]:
    """Serialisieren wie der HTTP-Layer von Home Assistant."""
    try:
        import probatio
    except ImportError:
        import voluptuous_serialize

        return voluptuous_serialize.convert(schema, custom_serializer=cv.custom_serializer)
    return probatio.to_field_list(schema, custom_serializer=cv.custom_serializer)


def _users_field(fields: list[dict[str, Any]]) -> dict[str, Any]:
    matches = [field for field in fields if field.get("name") == "users"]
    assert matches, f"users field missing in serialised schema: {fields}"
    return matches[0]


async def test_config_flow_schema_is_serialisable(
    hass: Any, enable_custom_integrations: Any
) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    assert result["type"] == "form"
    fields = _serialise_schema(result["data_schema"])
    users = _users_field(fields)
    assert users["type"] == "string"
    assert users["default"] == ""


async def test_options_flow_schema_is_serialisable(
    hass: Any, enable_custom_integrations: Any
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data={"users": "user-1: product"}, options=OPTIONS, unique_id=DOMAIN
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] == "form"
    assert result["step_id"] == "init"
    fields = _serialise_schema(result["data_schema"])
    users = _users_field(fields)
    assert users["type"] == "string"
    assert users["default"] == "user-1: product"
    data_path = [field for field in fields if field.get("name") == "data_path"]
    assert data_path, f"data_path field missing in serialised schema: {fields}"
    assert data_path[0]["default"] == "config"


async def test_config_flow_invalid_users_shows_error(
    hass: Any, enable_custom_integrations: Any
) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"users": "user-1: unbekannte_rolle"}
    )

    assert result["type"] == "form"
    assert result["errors"] == {"users": "invalid_users"}


async def test_options_flow_invalid_users_shows_error(
    hass: Any, enable_custom_integrations: Any
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data={"users": "user-1: product"}, options=OPTIONS, unique_id=DOMAIN
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"users": "user-1: unbekannte_rolle"}
    )

    assert result["type"] == "form"
    assert result["errors"] == {"users": "invalid_users"}
