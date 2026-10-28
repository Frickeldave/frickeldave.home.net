"""Tests der Benutzerverwaltungs-API (Freigaben lesen, ändern, entziehen)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from custom_components.frickeldave_store import FrickeldaveStoreRuntime
from custom_components.frickeldave_store.ai import AIService
from custom_components.frickeldave_store.const import DOMAIN
from custom_components.frickeldave_store.permissions import PermissionManager, format_user_grants
from custom_components.frickeldave_store.products import ProductService
from custom_components.frickeldave_store.store import CatalogStore
from custom_components.frickeldave_store.views import UsersView, UserView


class FakeConfig:
    def __init__(self, root: Path) -> None:
        self._root = root

    def path(self, *parts: str) -> str:
        return str(self._root.joinpath(*parts))

    def config_dir(self) -> str:
        return str(self._root)


class FakeEntry:
    def __init__(self, options: dict[str, Any]) -> None:
        self.entry_id = "entry-1"
        self.data: dict[str, Any] = {}
        self.options = options


class FakeConfigEntries:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []

    def async_update_entry(self, entry: FakeEntry, **kwargs: Any) -> None:
        self.updates.append(kwargs)
        for key, value in kwargs.items():
            setattr(entry, key, value)


class FakeAuth:
    """Minimales Abbild von ``hass.auth`` (nur Lesen)."""

    def __init__(self, users: list[Any]) -> None:
        self._users = list(users)

    async def async_get_users(self) -> list[Any]:
        return list(self._users)

    async def async_get_user(self, user_id: str) -> Any | None:
        return next((user for user in self._users if user.id == user_id), None)


class FakeHass:
    def __init__(self, root: Path, users: list[Any]) -> None:
        self.data: dict[str, Any] = {}
        self.config = FakeConfig(root)
        self.config_entries = FakeConfigEntries()
        self.auth = FakeAuth(users)

    async def async_add_executor_job(self, func: Any, *args: Any) -> Any:
        return func(*args)


class FakeRequest:
    def __init__(
        self,
        hass: FakeHass,
        user_id: str | None,
        *,
        body: dict[str, Any] | None = None,
    ) -> None:
        self.app = {"hass": hass}
        self._user = SimpleNamespace(id=user_id, name="Test") if user_id else None
        self.query: dict[str, str] = {}
        self._body = body

    def get(self, key: str, default: Any = None) -> Any:
        if key == "hass_user":
            return self._user
        return default

    async def json(self) -> dict[str, Any]:
        if self._body is None:
            raise ValueError("kein JSON")
        return self._body


def _user(user_id: str, name: str, *, is_active: bool = True) -> Any:
    return SimpleNamespace(
        id=user_id,
        name=name,
        is_active=is_active,
        is_admin=True,
        email=f"{user_id}@example.com",
    )


AUTH_USERS = [
    _user("admin", "David Koenig"),
    _user("user-1", "Erika Muster"),
    _user("neu", "Neue Kollegin"),
    _user("user-2", "Lena Frei"),
    _user("user-3", "Inaktiv Person", is_active=False),
]


def _payload(response: Any) -> Any:
    return json.loads(response.body.decode("utf-8"))


async def _setup(
    tmp_path: Path, grants: dict[str, list[str]]
) -> tuple[FakeHass, FrickeldaveStoreRuntime, FakeEntry]:
    hass = FakeHass(tmp_path, AUTH_USERS)
    store = CatalogStore(data_dir=tmp_path)
    await store.async_load()
    permissions = PermissionManager()
    permissions.load(grants)
    entry = FakeEntry(options={"users": format_user_grants(grants)})
    runtime = FrickeldaveStoreRuntime(
        entry=entry,  # type: ignore[arg-type]
        store=store,
        products=ProductService(store),
        permissions=permissions,
        ai=AIService(store),
        export_path=Path("export"),
    )
    hass.data[DOMAIN] = runtime
    return hass, runtime, entry


async def test_users_list_resolves_names_and_available_users(tmp_path: Path) -> None:
    hass, _, _ = await _setup(
        tmp_path,
        {"admin": ["app_admin"], "user-1": ["product"], "ghost": ["finance"]},
    )

    response = await UsersView().get(FakeRequest(hass, "admin"))
    assert response.status == 200
    body = _payload(response)

    users = {item["user_id"]: item for item in body["users"]}
    assert set(users) == {"admin", "user-1", "ghost"}
    assert users["admin"] == {
        "user_id": "admin",
        "name": "David Koenig",
        "roles": ["app_admin"],
        "role_labels": ["Anwendungsverwaltung"],
    }
    assert users["user-1"]["name"] == "Erika Muster"
    # Unbekannte Freigabe: kein Name, bleibt aber in der Liste (editier-/entfernbar).
    assert users["ghost"] == {
        "user_id": "ghost",
        "name": "",
        "roles": ["finance"],
        "role_labels": ["Finanzen"],
    }
    assert body["roles"] == [
        {"id": "product", "label": "Produktpflege"},
        {"id": "finance", "label": "Finanzen"},
        {"id": "app_admin", "label": "Anwendungsverwaltung"},
    ]
    # Nur aktive, noch nicht freigegebene Benutzer, ohne E-Mail/Admin-Flags.
    assert body["available_users"] == [
        {"user_id": "user-2", "name": "Lena Frei"},
        {"user_id": "neu", "name": "Neue Kollegin"},
    ]
    assert all(set(item) == {"user_id", "name"} for item in body["available_users"])


async def test_put_adds_grant_persists_and_applies_immediately(tmp_path: Path) -> None:
    hass, runtime, entry = await _setup(tmp_path, {"admin": ["app_admin"]})

    response = await UserView().put(
        FakeRequest(hass, "admin", body={"roles": ["product", "finance"]}), "neu"
    )
    assert response.status == 200
    body = _payload(response)

    assert runtime.permissions.can("neu", "product") is True
    assert runtime.permissions.can("neu", "finance") is True
    assert "neu: product,finance" in entry.options["users"]
    assert hass.config_entries.updates[-1]["options"]["users"] == entry.options["users"]

    added = next(item for item in body["users"] if item["user_id"] == "neu")
    assert added["name"] == "Neue Kollegin"
    assert added["roles"] == ["product", "finance"]
    assert added["role_labels"] == ["Produktpflege", "Finanzen"]
    assert [item["user_id"] for item in body["available_users"]] == ["user-1", "user-2"]


async def test_put_updates_existing_grant_with_unknown_ha_user_id(tmp_path: Path) -> None:
    hass, runtime, entry = await _setup(tmp_path, {"admin": ["app_admin"], "ghost": ["product"]})

    response = await UserView().put(
        FakeRequest(hass, "admin", body={"roles": ["finance"]}), "ghost"
    )
    assert response.status == 200

    assert runtime.permissions.roles("ghost") == ["finance"]
    assert runtime.permissions.can("ghost", "product") is False
    assert "ghost: finance" in entry.options["users"]
    ghost = next(item for item in _payload(response)["users"] if item["user_id"] == "ghost")
    assert ghost["name"] == ""
    assert ghost["role_labels"] == ["Finanzen"]


async def test_put_rejects_unknown_ha_user_when_adding(tmp_path: Path) -> None:
    hass, runtime, entry = await _setup(tmp_path, {"admin": ["app_admin"]})
    before = entry.options["users"]

    response = await UserView().put(
        FakeRequest(hass, "admin", body={"roles": ["product"]}), "does-not-exist"
    )
    assert response.status == 404
    assert _payload(response)["error"]["code"] == "not_found"
    assert runtime.permissions.roles("does-not-exist") == []
    assert entry.options["users"] == before


async def test_put_rejects_unknown_and_empty_roles(tmp_path: Path) -> None:
    hass, runtime, entry = await _setup(tmp_path, {"admin": ["app_admin"]})
    before = entry.options["users"]

    unknown = await UserView().put(
        FakeRequest(hass, "admin", body={"roles": ["product", "chef"]}), "neu"
    )
    assert unknown.status == 400
    assert _payload(unknown)["error"]["code"] == "validation_error"

    empty = await UserView().put(FakeRequest(hass, "admin", body={"roles": []}), "neu")
    assert empty.status == 400
    assert _payload(empty)["error"]["code"] == "validation_error"

    wrong_type = await UserView().put(FakeRequest(hass, "admin", body={"roles": "product"}), "neu")
    assert wrong_type.status == 400

    assert runtime.permissions.roles("neu") == []
    assert entry.options["users"] == before


async def test_last_admin_cannot_be_downgraded_or_removed(tmp_path: Path) -> None:
    hass, runtime, entry = await _setup(tmp_path, {"admin": ["app_admin"], "user-1": ["product"]})
    before = entry.options["users"]

    downgrade = await UserView().put(
        FakeRequest(hass, "admin", body={"roles": ["product"]}), "admin"
    )
    assert downgrade.status == 409
    assert _payload(downgrade)["error"]["code"] == "conflict"
    assert "letzte" in _payload(downgrade)["error"]["message"]

    removed = await UserView().delete(FakeRequest(hass, "admin"), "admin")
    assert removed.status == 409
    assert _payload(removed)["error"]["code"] == "conflict"

    assert runtime.permissions.can("admin", "app_admin") is True
    assert entry.options["users"] == before


async def test_admin_can_be_downgraded_when_another_admin_exists(tmp_path: Path) -> None:
    hass, runtime, _ = await _setup(
        tmp_path, {"admin": ["app_admin"], "second": ["app_admin"], "user-1": ["product"]}
    )

    response = await UserView().put(
        FakeRequest(hass, "admin", body={"roles": ["product"]}), "second"
    )
    assert response.status == 200
    assert runtime.permissions.can("second", "app_admin") is False
    assert runtime.permissions.can("admin", "app_admin") is True


async def test_delete_removes_grant_and_revokes_immediately(tmp_path: Path) -> None:
    hass, runtime, entry = await _setup(tmp_path, {"admin": ["app_admin"], "user-1": ["product"]})
    assert runtime.permissions.can("user-1", "product") is True

    response = await UserView().delete(FakeRequest(hass, "admin"), "user-1")
    assert response.status == 200
    body = _payload(response)

    assert runtime.permissions.can("user-1", "product") is False
    assert "user-1" not in entry.options["users"]
    assert {item["user_id"] for item in body["users"]} == {"admin"}
    assert {"user_id": "user-1", "name": "Erika Muster"} in body["available_users"]


async def test_delete_unknown_grant_returns_404(tmp_path: Path) -> None:
    hass, _, entry = await _setup(tmp_path, {"admin": ["app_admin"]})
    before = entry.options["users"]

    response = await UserView().delete(FakeRequest(hass, "admin"), "user-1")
    assert response.status == 404
    assert _payload(response)["error"]["code"] == "not_found"
    assert entry.options["users"] == before


async def test_users_endpoints_require_app_admin(tmp_path: Path) -> None:
    hass, runtime, entry = await _setup(
        tmp_path, {"admin": ["app_admin"], "user-1": ["product", "finance"]}
    )
    before = entry.options["users"]

    requests = [
        UsersView().get(FakeRequest(hass, "user-1")),
        UserView().put(FakeRequest(hass, "user-1", body={"roles": ["app_admin"]}), "user-1"),
        UserView().delete(FakeRequest(hass, "user-1"), "admin"),
        UsersView().get(FakeRequest(hass, None)),
    ]
    for request in requests:
        response = await request
        assert response.status == 403
        assert _payload(response)["error"]["code"] == "forbidden"

    assert runtime.permissions.can("user-1", "app_admin") is False
    assert entry.options["users"] == before
