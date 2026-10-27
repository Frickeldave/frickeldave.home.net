"""Regressionstests: WebSocket-Kommando-Schemas müssen echte Nachrichten akzeptieren.

Die Schemas werden über ``handler._ws_schema`` geprüft – genau das Objekt, das
``websocket_api.async_register_command`` beim Verbindungsaufbau verwendet.
Ein falsch aufgebautes Mapping (Marker als Werte statt als Schlüssel) führt hier
zu ``not a valid value at 'search'`` und damit zu abgelehnten Panel-Anfragen.
"""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.frickeldave_store import websocket_api

COMMANDS: dict[str, Any] = {
    "frickeldave_store/me": websocket_api.websocket_me,
    "frickeldave_store/products": websocket_api.websocket_products,
    "frickeldave_store/subscribe": websocket_api.websocket_subscribe,
}

FILTER_COMMANDS = ("frickeldave_store/products", "frickeldave_store/subscribe")


def _schema(command: str) -> Any:
    schema = getattr(COMMANDS[command], "_ws_schema", None)
    assert schema is not None, f"kein Schema an {command} registriert"
    return schema


def _validate(command: str, message: dict[str, Any]) -> Any:
    """Nachricht so prüfen, wie es der WebSocket-Layer tut."""

    schema = _schema(command)
    if schema is False:
        # HA akzeptiert bei schema=False ausschließlich id und type.
        assert len(message) == 2, f"{command} akzeptiert keine Filterfelder: {message}"
        return message
    return schema(message)


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_schema_accepts_minimal_message(command: str) -> None:
    result = _validate(command, {"id": 1, "type": command})
    assert result["type"] == command


@pytest.mark.parametrize("command", FILTER_COMMANDS)
def test_schema_accepts_realistic_filter_message(command: str) -> None:
    message = {
        "id": 2,
        "type": command,
        "search": "x",
        "category": "LA,WO",
        "tags": "a,b",
        "customizable": True,
        "in_stock": False,
        "in_cabinet": True,
        "visibility": "visible",
        "archived": False,
        "sort": "price",
        "order": "desc",
        "limit": 10,
        "offset": 0,
    }
    result = _validate(command, message)
    assert result["search"] == "x"
    assert result["category"] == "LA,WO"
    assert result["tags"] == "a,b"
    assert result["customizable"] is True
    assert result["in_stock"] is False
    assert result["in_cabinet"] is True
    assert result["visibility"] == "visible"
    assert result["archived"] is False
    assert result["sort"] == "price"
    assert result["order"] == "desc"
    assert result["limit"] == 10
    assert result["offset"] == 0


@pytest.mark.parametrize("command", FILTER_COMMANDS)
def test_schema_applies_filter_defaults(command: str) -> None:
    result = _validate(command, {"id": 3, "type": command})
    assert result["search"] == ""
    assert result["sort"] == "name"
    assert result["order"] == "asc"
    assert result["offset"] == 0


@pytest.mark.parametrize("command", FILTER_COMMANDS)
@pytest.mark.parametrize(
    "invalid",
    [
        {"limit": "abc"},
        {"offset": "viele"},
        {"customizable": "vielleicht"},
        {"search": ["kein", "string"]},
    ],
)
def test_schema_rejects_invalid_filter_values(command: str, invalid: dict[str, Any]) -> None:
    message = {"id": 4, "type": command, **invalid}
    with pytest.raises(websocket_api.schema_lib.MultipleInvalid):
        _validate(command, message)


def test_query_from_msg_uses_filter_names() -> None:
    query = websocket_api._query_from_msg(
        {
            "type": "frickeldave_store/products",
            "search": "x",
            "customizable": True,
            "in_stock": False,
            "limit": 10,
            "offset": 0,
            "sort": "name",
            "order": "asc",
        }
    )
    assert query["search"] == "x"
    assert query["customizable"] == "true"
    assert query["in_stock"] == "false"
    assert query["limit"] == "10"
    assert "type" not in query
