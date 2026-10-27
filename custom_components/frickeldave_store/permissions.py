"""Berechtigungsschicht: Rollen, Default Deny, Prüfung je Anfrage.

``require_admin`` ist bewusst keine Zugangskontrolle: jede geschützte HTTP- und
WebSocket-Route prüft hier serverseitig pro Anfrage. Ein Rechteentzug greift
dadurch sofort, auch bei bereits geöffnetem Panel.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .models import PermissionDenied, ValidationError

ROLE_PRODUCT = "product"
ROLE_FINANCE = "finance"
ROLE_APP_ADMIN = "app_admin"
ALL_ROLES: tuple[str, ...] = (ROLE_PRODUCT, ROLE_FINANCE, ROLE_APP_ADMIN)
ROLE_LABELS: dict[str, str] = {
    ROLE_PRODUCT: "Produktpflege",
    ROLE_FINANCE: "Finanzen",
    ROLE_APP_ADMIN: "Anwendungsverwaltung",
}


def normalise_roles(values: Iterable[str] | str) -> list[str]:
    """Rollen normalisieren, prüfen und deduplizieren."""

    if isinstance(values, str):
        parts: Iterable[str] = values.split(",")
    else:
        parts = values
    roles: list[str] = []
    for raw in parts:
        role = str(raw).strip().lower()
        if not role:
            continue
        if role not in ALL_ROLES:
            raise ValidationError(f"Unbekannte Rolle: {role!r}")
        if role not in roles:
            roles.append(role)
    return roles


def parse_user_grants(text: str) -> dict[str, list[str]]:
    """Freigaben aus der Config-Entry-Option lesen.

    Format: eine Zeile pro Benutzer, ``<ha_user_id>: <rolle>,<rolle>``.
    Kommentare (``#``) und Leerzeilen werden ignoriert.
    """

    grants: dict[str, list[str]] = {}
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" in stripped:
            user_id, _, roles_raw = stripped.partition(":")
        else:
            user_id, roles_raw = stripped, ""
        user_id = user_id.strip()
        if not user_id:
            continue
        grants[user_id] = normalise_roles(roles_raw)
    return grants


def format_user_grants(grants: Mapping[str, Iterable[str]]) -> str:
    """Umgekehrter Weg für Options-Flow und Dokumentation."""

    lines = [
        f"{user_id}: {','.join(normalise_roles(roles))}".strip()
        for user_id, roles in sorted(grants.items())
    ]
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class UserGrant:
    """Freigabe eines HA-Benutzers."""

    user_id: str
    name: str = ""
    roles: tuple[str, ...] = ()


@dataclass(slots=True)
class PermissionManager:
    """Verwaltet Benutzerfreigaben; unbekannte Benutzer haben keine Rechte."""

    _grants: dict[str, UserGrant] = field(default_factory=dict)

    def load(
        self, grants: Mapping[str, Iterable[str]], names: Mapping[str, str] | None = None
    ) -> None:
        """Freigaben ersetzen (z. B. nach Options-Änderung)."""

        known_names = names or {}
        self._grants = {
            user_id: UserGrant(
                user_id=user_id,
                name=known_names.get(user_id, ""),
                roles=tuple(normalise_roles(roles)),
            )
            for user_id, roles in grants.items()
        }

    def set_user(self, user_id: str, roles: Iterable[str], *, name: str = "") -> None:
        if not user_id:
            raise ValidationError("user_id darf nicht leer sein.")
        self._grants[user_id] = UserGrant(
            user_id=user_id, name=name, roles=tuple(normalise_roles(roles))
        )

    def remove_user(self, user_id: str) -> None:
        self._grants.pop(user_id, None)

    def grants(self) -> dict[str, UserGrant]:
        return dict(self._grants)

    def roles(self, user_id: str) -> list[str]:
        grant = self._grants.get(user_id)
        return list(grant.roles) if grant else []

    def name(self, user_id: str) -> str:
        grant = self._grants.get(user_id)
        return grant.name if grant else ""

    def can(self, user_id: str, role: str) -> bool:
        """Default Deny; Finanzrechte folgen nie implizit aus anderen Rollen."""

        if role not in ALL_ROLES:
            return False
        return role in self.roles(user_id)

    def require(self, user_id: str, role: str) -> None:
        if not self.can(user_id, role):
            raise PermissionDenied("Keine Berechtigung für diesen Bereich.")

    def snapshot(self, user_id: str) -> dict[str, Any]:
        """Antwort für ``GET /api/frickeldave_store/me``."""

        return {
            "user_id": user_id,
            "name": self.name(user_id),
            "roles": self.roles(user_id),
            "can": {role: self.can(user_id, role) for role in ALL_ROLES},
        }
