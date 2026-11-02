"""Öffentlicher Erweiterungsvertrag der Integration ``frickeldave_store`` (Contract 1).

**Stabilitätszusage:** Alles, was in :data:`__all__` dieses Moduls steht, ist
öffentliche API und folgt der Versionierung von :data:`CONTRACT_VERSION`.
Alles andere unter ``custom_components.frickeldave_store`` – insbesondere
``store``, ``products``, ``views``, ``permissions``, ``models``, ``const``,
``export``, ``panel`` und ``frontend/`` – bleibt privat und darf von einer
Erweiterung nicht importiert werden.

Eine Erweiterung (eigene Custom Integration) registriert sich in ihrem
``async_setup_entry`` über :func:`async_register_extension` und meldet sich über
:func:`async_unregister_extension` bzw. ``entry.async_on_unload`` wieder ab. Sie
erhält je Anfrage eine schmale, benutzergebundene :class:`StoreFacade` und liefert
Feature-Handler, die die Basis unter ``POST /api/frickeldave_store/features/{feature}``
authentifiziert und routet. Die Basis importiert die Erweiterung nie und kennt ihren
Domainnamen nicht.

Vertrag 1 ist additiv: Die eingebauten Fähigkeiten ``publish`` und ``import``
bleiben in der Basis funktionsfähig, solange keine Erweiterung sie übernimmt.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, ClassVar, Final, Literal

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .export import build_catalog, catalog_bytes, ordered_images
from .importer import ImageUpload, ImportItem, async_import_products
from .models import Product, ValidationError, to_iso
from .permissions import ALL_ROLES

_LOGGER = logging.getLogger(__name__)

#: Version des öffentlichen Vertrags. Erhöht sich bei jeder inkompatiblen Änderung.
CONTRACT_VERSION: Final = 1

#: Domain der Basisintegration.
BASE_DOMAIN: Final = DOMAIN

#: HA-Event, das nach jeder Änderung der registrierten Erweiterungen feuert.
EVENT_FEATURES_CHANGED: Final = f"{DOMAIN}_features_changed"

#: Schlüssel in ``hass.data``; interne Ablage, nicht Teil des Vertrags.
_STATE_KEY: Final = f"{DOMAIN}_extensions"

ROLE_PRODUCT: Final = "product"
ROLE_FINANCE: Final = "finance"
ROLE_APP_ADMIN: Final = "app_admin"

#: Reservierte Feature-Namen. Eine Erweiterung darf sie übernehmen.
FEATURE_PUBLISH: Final = "publish"
FEATURE_IMPORT: Final = "import"

#: Fähigkeiten, die die Basis ohne Erweiterung selbst bedient (Fallback).
BUILTIN_FEATURES: Final[tuple[str, ...]] = (FEATURE_PUBLISH, FEATURE_IMPORT)

#: Zielpfade im Website-Repository (Vorgaben für :meth:`StoreFacade.async_build_export`).
CATALOG_PATH_DEFAULT: Final = "public/data/handmade.json"
IMAGES_PATH_DEFAULT: Final = "src/assets/handmade"

#: Ein Rückruf, der eine Feature-Anfrage beantwortet.
FeatureHandler = Callable[["FeatureRequest"], Awaitable["FeatureResponse"]]

_ERROR_STATUS: Final[dict[str, int]] = {
    "contract_mismatch": 400,
    "already_registered": 409,
    "not_registered": 404,
    "invalid_registration": 400,
    "feature_unknown": 404,
    "feature_unavailable": 409,
    "handler_failed": 502,
    "invalid_request": 400,
}


class ExtensionError(Exception):
    """Strukturierter Fehler des Erweiterungsvertrags.

    ``code`` landet unverändert in der API-Antwort (``{"error": {"code", "message"}}``);
    ``status`` ist der HTTP-Status. Handler von Erweiterungen dürfen die Klasse
    selbst werfen, um fachliche Fehler strukturiert an das Panel zu geben.
    """

    def __init__(self, code: str, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status if status is not None else _ERROR_STATUS.get(code, 400)

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class FeatureRequest:
    """Eine Anfrage an eine Fähigkeit oder Panel-Aktion."""

    feature: str
    payload: Mapping[str, Any]
    user_id: str
    user_name: str
    facade: StoreFacade


@dataclass(frozen=True, slots=True)
class FeatureResponse:
    """Antwort einer Fähigkeit; ``payload`` wird als JSON zurückgegeben."""

    payload: Mapping[str, Any]
    status: int = 200


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    """Beschreibung einer Fähigkeit, die eine Erweiterung beisteuert.

    ``ready=False`` heißt: installiert, aber nicht eingerichtet (z. B. fehlender
    Token). ``reason`` ist dann die deutsche Begründung für das Panel.
    ``provides_panel_routes`` markiert Fähigkeiten, die eigene Routen oder Dialoge
    im Panel mitbringen; das Panel blendet sie nur ein, wenn sie verfügbar sind.
    """

    name: str
    title: str
    required_role: str | None = None
    provides_panel_routes: bool = False
    ready: bool = True
    reason: str = ""
    handler: FeatureHandler | None = None


@dataclass(frozen=True, slots=True)
class ExtensionRegistration:
    """Was eine Erweiterung bei der Registrierung übergibt.

    ``panel_actions`` sind zusätzliche Rückrufe für Panel-Dialoge; sie erscheinen
    nicht im Feature-Status, werden aber über denselben Endpunkt aufgerufen wie
    Fähigkeiten (Features haben Vorrang).
    """

    name: str
    version: str
    contract_version: int = CONTRACT_VERSION
    features: Mapping[str, FeatureSpec] = field(default_factory=dict)
    panel_actions: Mapping[str, FeatureHandler] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FeatureStatus:
    """Zustand einer Fähigkeit für ``/me``, ``/settings`` und das Panel."""

    available: bool
    reason: str = ""
    provider: str = ""
    contract_version: int = CONTRACT_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "reason": self.reason,
            "provider": self.provider,
            "contract_version": self.contract_version,
        }


@dataclass(frozen=True, slots=True)
class ImageSnapshot:
    """Bildmetadaten eines Produkts (ohne Dateizugriff)."""

    name: str
    is_main: bool
    order: int


@dataclass(frozen=True, slots=True)
class ProductSnapshot:
    """Produktstand für eine Erweiterung – inklusive archivierter Produkte."""

    article_number: str
    name: str
    description: str
    price: float
    size: str
    category: str
    tags: tuple[str, ...]
    visible: bool
    customizable: bool
    archived: bool
    stock: Mapping[str, int]
    images: tuple[ImageSnapshot, ...]
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ExportArtifact:
    """Eine Datei des Website-Exports (Pfad relativ zum Ziel-Repository)."""

    path: str
    data: bytes
    kind: Literal["catalog", "image"]
    article_number: str = ""


@dataclass(frozen=True, slots=True)
class ExportBundle:
    """Vollständiger Website-Export, im Speicher gebaut (kein Dateisystemzugriff)."""

    catalog_path: str
    images_path: str
    catalog_bytes: bytes
    artifacts: tuple[ExportArtifact, ...]
    product_count: int
    image_count: int
    empty_catalog: bool
    generated_at: str


@dataclass(frozen=True, slots=True)
class ImportItemError:
    """Fehler eines einzelnen Produkts im Import-Batch."""

    article_number: str
    message: str


@dataclass(frozen=True, slots=True)
class ImportBatchResult:
    """Ergebnis eines über die Fassade angewendeten Import-Batches."""

    created: tuple[str, ...]
    updated: tuple[str, ...]
    unchanged: int
    skipped: int
    failed: int
    images_written: int
    errors: tuple[ImportItemError, ...]
    snapshot: str | None
    summary: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "created": list(self.created),
            "updated": list(self.updated),
            "unchanged": self.unchanged,
            "skipped": self.skipped,
            "failed": self.failed,
            "images_written": self.images_written,
            "errors": [
                {"articleNumber": item.article_number, "message": item.message}
                for item in self.errors
            ],
            "snapshot": self.snapshot,
            "summary": self.summary,
        }


def _freeze(value: Any) -> Any:
    """Verschachtelte Einstellungen unveränderlich kopieren."""

    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


class StoreFacade:
    """Schmale Fassade auf den Store für eine Erweiterung.

    Die Fassade ist je Anfrage an genau einen HA-Benutzer gebunden; ``can`` und
    ``require`` prüfen mit denselben Rollen wie das Panel. Sie gibt keine internen
    Objekte, Privatattribute oder Dateisystempfade heraus.
    """

    contract_version: ClassVar[int] = CONTRACT_VERSION

    def __init__(
        self,
        hass: HomeAssistant,
        runtime: Any,
        *,
        user_id: str = "",
        user_name: str = "",
    ) -> None:
        self._hass = hass
        self._runtime = runtime
        self._user_id = user_id
        self._user_name = user_name

    @property
    def hass(self) -> HomeAssistant:
        return self._hass

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def user_name(self) -> str:
        return self._user_name

    @property
    def settings(self) -> Mapping[str, Any]:
        """Einstellungen ohne Geheimnisse, nur lesend."""

        return _freeze(self._runtime.store.public_settings())

    def can(self, role: str) -> bool:
        """Rechteprüfung mit den Rollen des Panels (Default Deny)."""

        return bool(self._runtime.permissions.can(self._user_id, role))

    def require(self, role: str) -> None:
        """Wie :meth:`can`, wirft aber ``PermissionDenied``."""

        self._runtime.permissions.require(self._user_id, role)

    async def async_products(self) -> tuple[ProductSnapshot, ...]:
        """Alle Produkte inklusive archivierter, mit Bildmetadaten."""

        store = self._runtime.store
        location_ids = store.location_ids()
        snapshots: list[ProductSnapshot] = []
        for raw in store.product_dicts():
            product = Product.from_dict(raw, location_ids=location_ids)
            snapshots.append(self._snapshot_for(product))
        snapshots.sort(key=lambda item: item.article_number)
        return tuple(snapshots)

    async def async_locations(self) -> tuple[Mapping[str, str], ...]:
        """Lagerstätten als unveränderliche ``{"id", "name"}``-Zuordnungen."""

        return tuple(
            MappingProxyType({"id": str(item["id"]), "name": str(item["name"])})
            for item in self._runtime.store.locations()
        )

    async def async_article_numbers(self) -> frozenset[str]:
        """Alle vergebenen Artikelnummern."""

        return frozenset(str(number) for number in self._runtime.store.article_numbers())

    async def async_create_snapshot(self, reason: str = "manual") -> str | None:
        """Snapshot der Store-Datei anlegen; liefert den Dateinamen (kein Pfad)."""

        path = await self._runtime.store.async_snapshot(str(reason or "manual"))
        return path.name if path is not None else None

    async def async_build_export(
        self,
        *,
        catalog_path: str = CATALOG_PATH_DEFAULT,
        images_path: str = IMAGES_PATH_DEFAULT,
        visible_only: bool = False,
    ) -> ExportBundle:
        """Website-Export im Speicher bauen (nutzt ``export.build_catalog``)."""

        store = self._runtime.store
        location_ids = store.location_ids()
        products = [
            Product.from_dict(raw, location_ids=location_ids) for raw in store.product_dicts()
        ]
        locations = store.locations()
        catalog = build_catalog(products, visible_only=visible_only, locations=locations)
        artifacts: list[ExportArtifact] = []
        for product in sorted(products, key=lambda item: item.article_number):
            if product.archived:
                continue
            for image in ordered_images(product) if product.images else []:
                if not image.name:
                    continue
                try:
                    data = await store.images.async_read(product.article_number, image.name)
                except OSError:
                    _LOGGER.debug(
                        "Export: Bild %s/%s fehlt und wird übersprungen",
                        product.article_number,
                        image.name,
                    )
                    continue
                artifacts.append(
                    ExportArtifact(
                        path=_posix_join(images_path, product.article_number, image.name),
                        data=data,
                        kind="image",
                        article_number=product.article_number,
                    )
                )
        return ExportBundle(
            catalog_path=str(catalog_path),
            images_path=str(images_path),
            catalog_bytes=catalog_bytes(products, locations=locations, visible_only=visible_only),
            artifacts=tuple(artifacts),
            product_count=len(catalog["products"]),
            image_count=len(artifacts),
            empty_catalog=not catalog["products"],
            generated_at=to_iso(),
        )

    async def async_apply_import_batch(
        self,
        products: Sequence[Mapping[str, Any] | ImportItem],
        *,
        overwrite: bool = False,
    ) -> ImportBatchResult:
        """Import-Batch über den bestehenden Importer anwenden.

        ``products`` sind Katalogprodukte (Website-Schema) oder :class:`ImportItem`
        mit optionalen Bilddaten. Vor dem Schreiben entsteht ein Snapshot.
        """

        if isinstance(products, (str, bytes)) or not isinstance(products, Sequence):
            raise ValidationError("products muss eine Liste von Produkten sein.")
        items = list(products)
        snapshot_path = await self._runtime.store.async_snapshot("pre-import-batch")
        result = await async_import_products(self._runtime.store, items, overwrite=overwrite)
        return ImportBatchResult(
            created=tuple(result.created_numbers),
            updated=tuple(result.updated_numbers),
            unchanged=result.unchanged,
            skipped=result.skipped,
            failed=result.failed,
            images_written=result.images_imported,
            errors=tuple(
                ImportItemError(
                    article_number=str(item.get("articleNumber", "")),
                    message=str(item.get("message", "")),
                )
                for item in result.errors
            ),
            snapshot=snapshot_path.name if snapshot_path is not None else None,
            summary=result.summary,
        )

    @staticmethod
    def _snapshot_for(product: Product) -> ProductSnapshot:
        return ProductSnapshot(
            article_number=product.article_number,
            name=product.name,
            description=product.description,
            price=product.price,
            size=product.size,
            category=product.category,
            tags=tuple(product.tags),
            visible=product.visible,
            customizable=product.customizable,
            archived=product.archived,
            stock=MappingProxyType({str(key): int(value) for key, value in product.stock.items()}),
            images=tuple(
                ImageSnapshot(name=image.name, is_main=image.is_main, order=index)
                for index, image in enumerate(product.images)
            ),
            created_at=product.created_at,
            updated_at=product.updated_at,
        )


def _posix_join(base: str, article_number: str, name: str) -> str:
    """POSIX-Pfad im Ziel-Repository (kein lokaler Dateisystempfad)."""

    parts = [part.strip("/") for part in (base, article_number, name) if part]
    return "/".join(parts)


async def async_get_facade(hass: HomeAssistant, *, user_id: str = "") -> StoreFacade | None:
    """Fassade der eingerichteten Basis für einen HA-Benutzer, sonst ``None``."""

    from . import runtime_for

    runtime = runtime_for(hass)
    if runtime is None:
        return None
    user_name = ""
    if user_id:
        auth = getattr(hass, "auth", None)
        if auth is not None:
            try:
                user = await auth.async_get_user(user_id)
            except Exception:  # pragma: no cover - Auth-Backend nicht verfügbar
                user = None
            if user is not None:
                user_name = str(getattr(user, "name", "") or "")
    return StoreFacade(hass, runtime, user_id=user_id, user_name=user_name)


async def async_register_extension(
    hass: HomeAssistant, registration: ExtensionRegistration
) -> ExtensionRegistration:
    """Erweiterung registrieren. Idempotent bei identischer Registrierung.

    Wirft :class:`ExtensionError` mit ``contract_mismatch``, wenn die
    Vertragsversion nicht unterstützt wird, und ``already_registered``, wenn der
    Name mit einer abweichenden Registrierung belegt ist.
    """

    _validate_registration(registration)
    extensions = _mutable_extensions(hass)
    existing = extensions.get(registration.name)
    if existing is not None:
        if _signature(existing) == _signature(registration):
            return existing
        raise ExtensionError(
            "already_registered",
            f"Es ist bereits eine Erweiterung mit dem Namen '{registration.name}' "
            f"registriert (Version {existing.version}). Bitte zuerst abmelden.",
        )
    extensions[registration.name] = registration
    _LOGGER.info(
        "Frickeldave Store: Erweiterung %s %s registriert (%s Fähigkeiten)",
        registration.name,
        registration.version,
        len(registration.features),
    )
    _fire_features_changed(hass, registration.name)
    return registration


async def async_unregister_extension(hass: HomeAssistant, name: str) -> None:
    """Erweiterung abmelden (z. B. beim Unload)."""

    extensions = _mutable_extensions(hass)
    if extensions.pop(str(name), None) is None:
        raise ExtensionError(
            "not_registered",
            f"Es ist keine Erweiterung mit dem Namen '{name}' registriert.",
        )
    _LOGGER.info("Frickeldave Store: Erweiterung %s abgemeldet", name)
    _fire_features_changed(hass, str(name))


@dataclass(frozen=True, slots=True)
class _BuiltinFeature:
    title: str
    required_role: str


_BUILTIN_DETAILS: Final[dict[str, _BuiltinFeature]] = {
    FEATURE_PUBLISH: _BuiltinFeature("Veröffentlichen", ROLE_APP_ADMIN),
    FEATURE_IMPORT: _BuiltinFeature("Katalogimport", ROLE_PRODUCT),
}


def feature_details(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    """Ausführliche Feature-Beschreibung für ``/settings`` (nur ``app_admin``).

    Enthält Titel, benötigte Rolle und die Info, ob eine Fähigkeit eigene Routen
    oder Dialoge im Panel mitbringt – die Grundlage für das Feature-Gating.
    """

    details: dict[str, dict[str, Any]] = {}
    for name in BUILTIN_FEATURES:
        builtin = _BUILTIN_DETAILS[name]
        details[name] = {
            "name": name,
            "title": builtin.title,
            "required_role": builtin.required_role,
            "provides_panel_routes": True,
            "available": True,
            "reason": "",
            "provider": BASE_DOMAIN,
            "contract_version": CONTRACT_VERSION,
        }
    for registration in _extensions(hass).values():
        for name, spec in registration.features.items():
            reason = spec.reason
            if not reason and not spec.ready:
                reason = "Die Fähigkeit ist derzeit nicht verfügbar."
            details[name] = {
                "name": name,
                "title": spec.title,
                "required_role": spec.required_role,
                "provides_panel_routes": spec.provides_panel_routes,
                "available": spec.ready,
                "reason": reason,
                "provider": registration.name,
                "contract_version": registration.contract_version,
            }
    return details


async def async_get_feature_status(hass: HomeAssistant) -> dict[str, FeatureStatus]:
    """Status aller Fähigkeiten inklusive der eingebauten ``publish``/``import``."""

    return feature_status(hass)


def feature_status(hass: HomeAssistant) -> dict[str, FeatureStatus]:
    """Synchrone Variante von :func:`async_get_feature_status` (für View-Payloads)."""

    status: dict[str, FeatureStatus] = {
        name: FeatureStatus(
            available=True,
            reason="",
            provider=BASE_DOMAIN,
            contract_version=CONTRACT_VERSION,
        )
        for name in BUILTIN_FEATURES
    }
    for registration in _extensions(hass).values():
        for name, spec in registration.features.items():
            reason = spec.reason
            if not reason and not spec.ready:
                reason = "Die Fähigkeit ist derzeit nicht verfügbar."
            status[name] = FeatureStatus(
                available=spec.ready,
                reason=reason,
                provider=registration.name,
                contract_version=registration.contract_version,
            )
    return status


@dataclass(frozen=True, slots=True)
class _ResolvedFeature:
    """Interner Treffer für die HTTP-Route (nicht Teil des Vertrags)."""

    handler: FeatureHandler | None
    required_role: str | None
    provider: str
    contract_version: int
    ready: bool
    reason: str


def resolve_feature(hass: HomeAssistant, feature: str) -> _ResolvedFeature | None:
    """Handler für einen Feature-Namen finden (Features vor Panel-Aktionen)."""

    extensions = _extensions(hass)
    for registration in extensions.values():
        spec = registration.features.get(feature)
        if spec is not None:
            return _ResolvedFeature(
                handler=spec.handler,
                required_role=spec.required_role,
                provider=registration.name,
                contract_version=registration.contract_version,
                ready=spec.ready,
                reason=spec.reason,
            )
    for registration in extensions.values():
        handler = registration.panel_actions.get(feature)
        if handler is not None:
            return _ResolvedFeature(
                handler=handler,
                required_role=None,
                provider=registration.name,
                contract_version=registration.contract_version,
                ready=True,
                reason="",
            )
    return None


def _extensions(hass: HomeAssistant) -> dict[str, ExtensionRegistration]:
    """Registrierte Erweiterungen lesen, ohne Zustand anzulegen."""

    data = getattr(hass, "data", None)
    if not isinstance(data, dict):  # pragma: no cover - HA stellt data immer bereit
        return {}
    store = data.get(_STATE_KEY)
    return store if isinstance(store, dict) else {}


def _mutable_extensions(hass: HomeAssistant) -> dict[str, ExtensionRegistration]:
    """Registrierte Erweiterungen lesen und bei Bedarf die Ablage anlegen."""

    data = getattr(hass, "data", None)
    if not isinstance(data, dict):  # pragma: no cover - HA stellt data immer bereit
        return {}
    store = data.get(_STATE_KEY)
    if not isinstance(store, dict):
        store = {}
        data[_STATE_KEY] = store
    return store


def _signature(registration: ExtensionRegistration) -> tuple[Any, ...]:
    return (
        registration.name,
        registration.version,
        registration.contract_version,
        tuple(sorted(registration.features)),
        tuple(sorted(registration.panel_actions)),
    )


def _validate_registration(registration: ExtensionRegistration) -> None:
    if not isinstance(registration, ExtensionRegistration):
        raise ExtensionError(
            "invalid_registration",
            "Die Registrierung muss eine ExtensionRegistration sein.",
        )
    if not str(registration.name).strip():
        raise ExtensionError("invalid_registration", "Der Name der Erweiterung fehlt.")
    if registration.contract_version != CONTRACT_VERSION:
        raise ExtensionError(
            "contract_mismatch",
            f"Die Erweiterung '{registration.name}' spricht Vertrag "
            f"{registration.contract_version}, diese Basis unterstützt "
            f"ausschließlich Vertrag {CONTRACT_VERSION}. Bitte die Erweiterung "
            "passend aktualisieren.",
        )
    for name, spec in registration.features.items():
        if not isinstance(spec, FeatureSpec):
            raise ExtensionError(
                "invalid_registration",
                f"Feature '{name}' ist keine FeatureSpec.",
            )
        if not str(name).strip():
            raise ExtensionError("invalid_registration", "Ein Feature-Name fehlt.")
        if spec.name != name:
            raise ExtensionError(
                "invalid_registration",
                f"Feature '{name}' nennt sich in der Spec '{spec.name}'.",
            )
        if not str(spec.title).strip():
            raise ExtensionError("invalid_registration", f"Feature '{name}' braucht einen Titel.")
        if spec.required_role is not None and spec.required_role not in ALL_ROLES:
            raise ExtensionError(
                "invalid_registration",
                f"Feature '{name}' nennt eine unbekannte Rolle: {spec.required_role!r}.",
            )
    for action in registration.panel_actions:
        if not str(action).strip():
            raise ExtensionError("invalid_registration", "Ein Panel-Aktionsname fehlt.")


def _fire_features_changed(hass: HomeAssistant, changed: str) -> None:
    bus = getattr(hass, "bus", None)
    if bus is None:  # pragma: no cover - nur Test-Doubles ohne Event-Bus
        return
    try:
        bus.async_fire(
            EVENT_FEATURES_CHANGED,
            {
                "changed": changed,
                "features": {
                    name: status.as_dict() for name, status in feature_status(hass).items()
                },
            },
        )
    except Exception:  # pragma: no cover - ein Event darf das Setup nie brechen
        _LOGGER.debug("Event %s konnte nicht gefeuert werden", EVENT_FEATURES_CHANGED)


__all__ = [
    "BASE_DOMAIN",
    "BUILTIN_FEATURES",
    "CATALOG_PATH_DEFAULT",
    "CONTRACT_VERSION",
    "EVENT_FEATURES_CHANGED",
    "FEATURE_IMPORT",
    "FEATURE_PUBLISH",
    "IMAGES_PATH_DEFAULT",
    "ROLE_APP_ADMIN",
    "ROLE_FINANCE",
    "ROLE_PRODUCT",
    "ExportArtifact",
    "ExportBundle",
    "ExtensionError",
    "ExtensionRegistration",
    "FeatureHandler",
    "FeatureRequest",
    "FeatureResponse",
    "FeatureSpec",
    "FeatureStatus",
    "ImageSnapshot",
    "ImageUpload",
    "ImportBatchResult",
    "ImportItem",
    "ImportItemError",
    "ProductSnapshot",
    "StoreFacade",
    "async_get_facade",
    "async_get_feature_status",
    "async_register_extension",
    "async_unregister_extension",
]
