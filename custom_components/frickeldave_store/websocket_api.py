"""WebSocket-API der Integration mit denselben Rechteprüfungen wie HTTP."""

from __future__ import annotations

import logging
from typing import Any

try:  # HA 2026.10+ ships probatio as the schema library
    import probatio as schema_lib
except ImportError:  # pragma: no cover - HA <= 2026.9
    import voluptuous as schema_lib

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from . import FrickeldaveStoreRuntime, runtime_for
from .const import DOMAIN
from .models import StoreError
from .permissions import ROLE_PRODUCT
from .products import ProductFilter

_LOGGER = logging.getLogger(__name__)

_FILTER_KEYS: dict[str, Any] = {
    "search": schema_lib.Optional("search", default=""),
    "category": schema_lib.Optional("category", default=""),
    "tags": schema_lib.Optional("tags", default=""),
    "customizable": schema_lib.Optional("customizable"),
    "in_stock": schema_lib.Optional("in_stock"),
    "in_cabinet": schema_lib.Optional("in_cabinet"),
    "visibility": schema_lib.Optional("visibility"),
    "archived": schema_lib.Optional("archived"),
    "sort": schema_lib.Optional("sort", default="name"),
    "order": schema_lib.Optional("order", default="asc"),
    "limit": schema_lib.Optional("limit"),
    "offset": schema_lib.Optional("offset", default=0),
}


def _runtime_or_error(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> Any | None:
    runtime = runtime_for(hass)
    if runtime is None:
        connection.send_error(msg["id"], "not_loaded", "Integration ist nicht eingerichtet.")
    return runtime


def _require(
    runtime: FrickeldaveStoreRuntime, connection: Any, msg: dict[str, Any], role: str
) -> bool:
    user_id = str(getattr(connection.user, "id", ""))
    if not runtime.permissions.can(user_id, role):
        connection.send_error(msg["id"], "forbidden", "Keine Berechtigung für diesen Bereich.")
        return False
    return True


def _query_from_msg(msg: dict[str, Any]) -> dict[str, str]:
    query: dict[str, str] = {}
    for key in _FILTER_KEYS:
        value = msg.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            query[key] = "true" if value else "false"
        else:
            query[key] = str(value)
    return query


@websocket_api.websocket_command({schema_lib.Required("type"): "frickeldave_store/me"})
@websocket_api.async_response
async def websocket_me(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    runtime = _runtime_or_error(hass, connection, msg)
    if runtime is None:
        return
    user_id = str(getattr(connection.user, "id", ""))
    connection.send_result(msg["id"], runtime.permissions.snapshot(user_id))


def _command_schema(command: str) -> dict[Any, Any]:
    schema: dict[Any, Any] = {schema_lib.Required("type"): command}
    schema.update(_FILTER_KEYS)
    return schema


@websocket_api.websocket_command(_command_schema("frickeldave_store/products"))
@websocket_api.async_response
async def websocket_products(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    runtime = _runtime_or_error(hass, connection, msg)
    if runtime is None or not _require(runtime, connection, msg, ROLE_PRODUCT):
        return
    try:
        filters = ProductFilter.from_query(_query_from_msg(msg))
        products, total = await runtime.products.async_list(filters)
    except StoreError as err:
        connection.send_error(msg["id"], err.code, err.message)
        return
    connection.send_result(
        msg["id"],
        {"products": [item.to_dict() for item in products], "total": total},
    )


@websocket_api.websocket_command(_command_schema("frickeldave_store/subscribe"))
@websocket_api.async_response
async def websocket_subscribe(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    """Panel-Abo: pusht die Produktliste bei jeder Änderung, Rechte je Push geprüft."""

    runtime = _runtime_or_error(hass, connection, msg)
    if runtime is None or not _require(runtime, connection, msg, ROLE_PRODUCT):
        return
    user_id = str(getattr(connection.user, "id", ""))
    try:
        filters = ProductFilter.from_query(_query_from_msg(msg))
    except StoreError as err:
        connection.send_error(msg["id"], err.code, err.message)
        return

    def _payload() -> dict[str, Any]:
        products, total = _list_sync(runtime, filters)
        return {"products": [item.to_dict() for item in products], "total": total}

    @callback
    def _on_change() -> None:
        if not runtime.permissions.can(user_id, ROLE_PRODUCT):
            connection.send_error(msg["id"], "forbidden", "Berechtigung entzogen.")
            unsubscribe()
            return
        connection.send_message(websocket_api.event_message(msg["id"], _payload()))

    def unsubscribe() -> None:
        remove = connection.subscriptions.pop(msg["id"], None)
        if remove is not None:
            remove()

    connection.send_result(msg["id"], _payload())
    connection.subscriptions[msg["id"]] = runtime.store.add_listener(_on_change)
    _ = user_id


def _list_sync(runtime: FrickeldaveStoreRuntime, filters: ProductFilter) -> tuple[list[Any], int]:
    """Synchroner Pfad für Listener (reine In-Memory-Filterung, kein I/O)."""

    from .products import product_matches, sort_products

    products = [item for item in runtime.store.products() if product_matches(item, filters)]
    products = sort_products(products, filters.sort, filters.order)
    total = len(products)
    if filters.offset:
        products = products[filters.offset :]
    if filters.limit is not None:
        products = products[: filters.limit]
    return products, total


COMMANDS = (websocket_me, websocket_products, websocket_subscribe)


def async_register(hass: HomeAssistant) -> None:
    key = f"{DOMAIN}_websocket_registered"
    if hass.data.get(key):
        return
    for command in COMMANDS:
        websocket_api.async_register_command(hass, command)
    hass.data[key] = True
