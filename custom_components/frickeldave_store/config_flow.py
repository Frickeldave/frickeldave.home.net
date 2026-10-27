"""Config Flow und Options Flow (keine YAML-Konfiguration)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONFIG_ENTRY_TITLE,
    DEFAULT_AI_ENDPOINT,
    DEFAULT_AI_MODEL,
    DEFAULT_AI_PROMPT,
    DEFAULT_EXPORT_PATH,
    DOMAIN,
)
from .models import StoreError
from .permissions import format_user_grants, parse_user_grants

VERSION = 1
MINOR_VERSION = 1


def _validate_users(value: str) -> str:
    try:
        parse_user_grants(value)
    except StoreError as err:
        raise vol.Invalid(str(err)) from err
    return value


USER_SCHEMA = vol.Schema({vol.Optional("users", default=""): _validate_users})


class FrickeldaveStoreConfigFlow(ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg]
    """Einrichtung über die HA-Oberfläche."""

    VERSION = VERSION
    MINOR_VERSION = MINOR_VERSION

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                grants = parse_user_grants(str(user_input.get("users", "") or ""))
            except StoreError as err:
                errors["users"] = "invalid_users"
                _ = err
            else:
                users = format_user_grants(grants)
                return self.async_create_entry(
                    title=CONFIG_ENTRY_TITLE,
                    data={"users": users},
                    options={
                        "users": users,
                        "ai_endpoint": DEFAULT_AI_ENDPOINT,
                        "ai_model": DEFAULT_AI_MODEL,
                        "ai_prompt": DEFAULT_AI_PROMPT,
                        "export_path": DEFAULT_EXPORT_PATH,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                USER_SCHEMA, user_input or {"users": ""}
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return FrickeldaveStoreOptionsFlow(config_entry)


class FrickeldaveStoreOptionsFlow(OptionsFlow):
    """Optionen: Freigaben und KI-Anbieter."""

    def __init__(self, config_entry: ConfigEntry | None = None) -> None:
        super().__init__()
        self._entry = config_entry

    @property
    def entry(self) -> ConfigEntry:
        if self._entry is not None:
            return self._entry
        entry = getattr(self, "config_entry", None)
        if entry is None:  # pragma: no cover - von HA immer gesetzt
            raise RuntimeError("Config Entry fehlt")
        return entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        entry = self.entry
        current = {**entry.data, **entry.options}
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                grants = parse_user_grants(str(user_input.get("users", "") or ""))
            except StoreError:
                errors["users"] = "invalid_users"
            else:
                api_key = str(user_input.get("ai_api_key", "") or "")
                options: dict[str, Any] = {
                    "users": format_user_grants(grants),
                    "ai_endpoint": str(user_input.get("ai_endpoint") or DEFAULT_AI_ENDPOINT),
                    "ai_model": str(user_input.get("ai_model") or DEFAULT_AI_MODEL),
                    "ai_prompt": str(user_input.get("ai_prompt") or DEFAULT_AI_PROMPT),
                    "export_path": str(user_input.get("export_path") or DEFAULT_EXPORT_PATH),
                }
                if api_key:
                    options["ai_api_key"] = api_key
                else:
                    # Leeres Feld behält den gespeicherten Key; er wird nie angezeigt.
                    options["ai_api_key"] = str(current.get("ai_api_key", "") or "")
                return self.async_create_entry(title="", data=options)

        schema = vol.Schema(
            {
                vol.Optional("users", default=str(current.get("users", ""))): _validate_users,
                vol.Optional(
                    "ai_endpoint", default=str(current.get("ai_endpoint", DEFAULT_AI_ENDPOINT))
                ): str,
                vol.Optional(
                    "ai_model", default=str(current.get("ai_model", DEFAULT_AI_MODEL))
                ): str,
                vol.Optional("ai_api_key", default=""): str,
                vol.Optional(
                    "ai_prompt", default=str(current.get("ai_prompt", DEFAULT_AI_PROMPT))
                ): str,
                vol.Optional(
                    "export_path", default=str(current.get("export_path", DEFAULT_EXPORT_PATH))
                ): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
