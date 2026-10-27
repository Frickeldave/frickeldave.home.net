"""Tests für Default Deny, Rollenkombinationen und sofortigen Rechteentzug."""

from __future__ import annotations

import pytest

from custom_components.frickeldave_store.models import PermissionDenied, ValidationError
from custom_components.frickeldave_store.permissions import (
    ROLE_APP_ADMIN,
    ROLE_FINANCE,
    ROLE_PRODUCT,
    PermissionManager,
    format_user_grants,
    parse_user_grants,
)


def test_default_deny_for_unknown_user() -> None:
    manager = PermissionManager()
    manager.load({"user-1": ["product"]})
    assert manager.roles("unknown") == []
    assert manager.can("unknown", ROLE_PRODUCT) is False
    assert manager.snapshot("unknown") == {
        "user_id": "unknown",
        "name": "",
        "roles": [],
        "can": {"product": False, "finance": False, "app_admin": False},
    }
    with pytest.raises(PermissionDenied):
        manager.require("unknown", ROLE_PRODUCT)


def test_roles_are_combinable_but_finance_is_never_implied() -> None:
    manager = PermissionManager()
    manager.load({"david": ["product", "finance", "app_admin"], "kind": ["product"]})
    assert manager.can("david", ROLE_FINANCE)
    assert manager.can("kind", ROLE_PRODUCT)
    assert not manager.can("kind", ROLE_FINANCE)
    assert not manager.can("kind", ROLE_APP_ADMIN)


def test_app_admin_does_not_imply_finance() -> None:
    manager = PermissionManager()
    manager.load({"admin": ["app_admin"]})
    assert manager.can("admin", ROLE_APP_ADMIN)
    assert not manager.can("admin", ROLE_FINANCE)
    assert not manager.can("admin", ROLE_PRODUCT)


def test_revocation_takes_effect_on_next_call() -> None:
    manager = PermissionManager()
    manager.load({"user-1": ["product"]})
    assert manager.can("user-1", ROLE_PRODUCT)
    manager.load({"user-1": []})
    assert not manager.can("user-1", ROLE_PRODUCT)
    with pytest.raises(PermissionDenied):
        manager.require("user-1", ROLE_PRODUCT)


def test_unknown_role_rejected() -> None:
    manager = PermissionManager()
    with pytest.raises(ValidationError):
        manager.load({"user-1": ["root"]})


def test_parse_and_format_grants() -> None:
    text = "# Kopfzeile\nuser-1: product, app_admin\nuser-2:product\n\nuser-3: finance"
    grants = parse_user_grants(text)
    assert grants == {
        "user-1": ["product", "app_admin"],
        "user-2": ["product"],
        "user-3": ["finance"],
    }
    assert format_user_grants(grants).splitlines() == [
        "user-1: product,app_admin",
        "user-2: product",
        "user-3: finance",
    ]
