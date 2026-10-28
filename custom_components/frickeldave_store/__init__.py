"""Setup der Integration ``frickeldave_store``."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import config_validation as cv

from .ai import AIService
from .const import (
    CONFIG_ENTRY_TITLE,
    DATA_DIR_NAME,
    DEFAULT_AI_ENDPOINT,
    DEFAULT_AI_MODEL,
    DEFAULT_AI_PROMPT,
    DEFAULT_EXPORT_PATH,
    DOMAIN,
)
from .models import StoreError
from .permissions import PermissionManager, parse_user_grants
from .products import ProductService
from .storage import (
    async_migrate_data_dir,
    async_prepare_data_dir,
    base_dir_for,
    data_path_for_dir,
    data_path_from_entry,
    normalise_data_path,
    resolve_data_dir,
)
from .store import CatalogStore, copy_snapshot

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS: list[str] = []


@dataclass
class FrickeldaveStoreRuntime:
    """Laufzeitobjekte einer eingerichteten Instanz."""

    entry: ConfigEntry
    store: CatalogStore
    products: ProductService
    permissions: PermissionManager
    ai: AIService
    export_path: Path
    #: Ziel einer bereits durchgeführten Datenmigration (Reload steht noch aus).
    migrated_to: Path | None = None


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """HTTP-Views und WebSocket-API einmalig registrieren."""

    from . import views, websocket_api

    await views.async_register_views(hass)
    websocket_api.async_register(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Config Entry laden: Store öffnen, Rechte anwenden, Panel registrieren."""

    data_path = data_path_from_entry(entry)
    try:
        data_dir = resolve_data_dir(hass, data_path)
        await async_prepare_data_dir(hass, data_dir, base_dir_for(data_path))
    except StoreError as err:
        raise ConfigEntryError(
            f"Frickeldave Store: Speicherort '{data_path}' ist nicht verfügbar: {err.message}"
        ) from err

    store = CatalogStore(hass, data_dir=data_dir)
    try:
        await store.async_load()
    except StoreError as err:
        raise ConfigEntryError(f"Frickeldave Store konnte nicht geladen werden: {err}") from err

    permissions = PermissionManager()
    runtime = FrickeldaveStoreRuntime(
        entry=entry,
        store=store,
        products=ProductService(store),
        permissions=permissions,
        ai=AIService(store),
        export_path=Path(DEFAULT_EXPORT_PATH),
    )
    hass.data[DOMAIN] = runtime
    await _async_apply_entry_config(hass, entry, runtime)

    from .panel import async_register_panel

    await async_register_panel(hass)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    _LOGGER.debug("Frickeldave Store eingerichtet (%s Produkte)", len(store.products()))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from .panel import async_unregister_panel

    await async_unregister_panel(hass)
    hass.data.pop(DOMAIN, None)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Config-Entry-Version migrieren; vorher einen Snapshot der Store-Datei ablegen."""

    from .config_flow import MINOR_VERSION, VERSION

    if entry.version > VERSION:
        _LOGGER.error(
            "Config-Entry-Version %s ist neuer als die Integration (%s)", entry.version, VERSION
        )
        return False

    try:
        data_dir = resolve_data_dir(hass, data_path_from_entry(entry))
    except StoreError:  # pragma: no cover - defekte Option, Setup scheitert ohnehin
        data_dir = Path(hass.config.path(DATA_DIR_NAME))
    snapshot_dir = data_dir / "backups"
    source = data_dir / "frickeldave_store.data"
    await hass.async_add_executor_job(
        copy_snapshot, source, snapshot_dir, f"pre-entry-migration-v{entry.version}"
    )

    if entry.version < VERSION or entry.minor_version < MINOR_VERSION:
        hass.config_entries.async_update_entry(
            entry,
            version=max(entry.version, VERSION),
            minor_version=max(entry.minor_version, MINOR_VERSION),
        )
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Options-Änderungen ohne Neustart anwenden (Rechteentzug greift sofort)."""

    runtime = hass.data.get(DOMAIN)
    if not isinstance(runtime, FrickeldaveStoreRuntime):
        return
    if await _async_handle_storage_change(hass, entry, runtime):
        return
    await _async_apply_entry_config(hass, entry, runtime)
    _LOGGER.debug("Frickeldave Store: Optionen neu angewendet")


async def _async_handle_storage_change(
    hass: HomeAssistant, entry: ConfigEntry, runtime: FrickeldaveStoreRuntime
) -> bool:
    """Speicherort-Option übernehmen: kopieren, Options schreiben, Reload planen.

    Gibt ``True`` zurück, wenn ein Reload aussteht (Aufrufer darf keine weiteren
    Einstellungen auf den alten Store anwenden).
    """

    try:
        value = data_path_from_entry(entry)
        target = resolve_data_dir(hass, value)
    except StoreError as err:
        _LOGGER.error("Ungültiger Speicherort in den Optionen: %s", err.message)
        await _async_revert_storage_option(hass, entry, runtime)
        return False

    if target == runtime.store.data_dir:
        return False

    if runtime.migrated_to != target:
        try:
            await runtime.store.async_save()
            await async_prepare_data_dir(hass, target, base_dir_for(value))
            await async_migrate_data_dir(hass, runtime.store.data_dir, target)
        except StoreError as err:
            _LOGGER.error("Speicherort '%s' nicht übernommen: %s", target, err.message)
            await _async_revert_storage_option(hass, entry, runtime)
            return False
        runtime.migrated_to = target

    hass.config_entries.async_schedule_reload(entry.entry_id)
    _LOGGER.info("Frickeldave Store: Speicherort wechselt zu %s (Reload)", target)
    return True


async def _async_revert_storage_option(
    hass: HomeAssistant, entry: ConfigEntry, runtime: FrickeldaveStoreRuntime
) -> None:
    """Option auf den tatsächlich geöffneten Datenordner zurücksetzen."""

    current = data_path_for_dir(hass, runtime.store.data_dir)
    options = dict(getattr(entry, "options", None) or {})
    if options.get("data_path") == current:
        return
    options["data_path"] = current
    hass.config_entries.async_update_entry(entry, options=options)


async def async_set_data_path(
    hass: HomeAssistant, entry: ConfigEntry, value: str, *, persist: bool = True
) -> bool:
    """Speicherort validieren, Daten kopieren und optional Option schreiben.

    Gibt ``True`` zurück, wenn ein Reload erforderlich ist. Der alte Ordner bleibt
    unverändert (manuelle Bereinigung).
    """

    normalised = normalise_data_path(value)
    target = resolve_data_dir(hass, normalised)
    await async_prepare_data_dir(hass, target, base_dir_for(normalised))

    runtime = runtime_for(hass)
    reload_required = False
    if runtime is not None and target != runtime.store.data_dir:
        if runtime.migrated_to != target:
            await runtime.store.async_save()
            await async_migrate_data_dir(hass, runtime.store.data_dir, target)
            runtime.migrated_to = target
        reload_required = True

    updated = False
    if persist:
        options = dict(getattr(entry, "options", None) or {})
        if options.get("data_path") != normalised:
            options["data_path"] = normalised
            hass.config_entries.async_update_entry(entry, options=options)
            updated = True
    if reload_required and persist and not updated:
        hass.config_entries.async_schedule_reload(entry.entry_id)
    return reload_required


async def _async_apply_entry_config(
    hass: HomeAssistant, entry: ConfigEntry, runtime: FrickeldaveStoreRuntime
) -> None:
    config = {**entry.data, **entry.options}

    grants = parse_user_grants(str(config.get("users", "") or ""))
    names: dict[str, str] = {}
    auth = getattr(hass, "auth", None)
    if auth is not None:
        for user_id in grants:
            try:
                user = await auth.async_get_user(user_id)
            except Exception:  # pragma: no cover - Auth-Backend nicht verfügbar
                user = None
            if user is not None and getattr(user, "name", None):
                names[user_id] = str(user.name)
    runtime.permissions.load(grants, names)

    ai_settings = runtime.store.settings["ai"]
    endpoint = str(config.get("ai_endpoint") or DEFAULT_AI_ENDPOINT)
    model = str(config.get("ai_model") or DEFAULT_AI_MODEL)
    prompt = str(config.get("ai_prompt") or DEFAULT_AI_PROMPT)
    api_key = str(config.get("ai_api_key") or "")
    changes: dict[str, Any] = {}
    if ai_settings.get("endpoint") != endpoint:
        changes["endpoint"] = endpoint
    if ai_settings.get("model") != model:
        changes["model"] = model
    if ai_settings.get("prompt") != prompt:
        changes["prompt"] = prompt
    if api_key and ai_settings.get("api_key") != api_key:
        changes["api_key"] = api_key
    if changes:
        runtime.store.update_settings({"ai": changes})
        await runtime.store.async_save()

    export_path = str(config.get("export_path") or DEFAULT_EXPORT_PATH)
    runtime.export_path = Path(export_path)


def runtime_for(hass: HomeAssistant) -> FrickeldaveStoreRuntime | None:
    """Laufzeit für Views/WebSocket; ``None`` wenn nicht eingerichtet."""

    runtime = hass.data.get(DOMAIN)
    return runtime if isinstance(runtime, FrickeldaveStoreRuntime) else None


__all__ = [
    "CONFIG_ENTRY_TITLE",
    "CONFIG_SCHEMA",
    "DOMAIN",
    "FrickeldaveStoreRuntime",
    "async_migrate_entry",
    "async_set_data_path",
    "async_setup",
    "async_setup_entry",
    "async_unload_entry",
    "runtime_for",
]
