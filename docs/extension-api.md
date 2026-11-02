# Erweiterungs-API (Contract 1)

`custom_components.frickeldave_store.extension` ist der öffentliche Erweiterungspunkt
der Basisintegration. Eine zweite Custom Integration – im Referenzfall
`frickeldave_store_sync` – kann darüber Fähigkeiten (Features) beisteuern, ohne
interne Store-Objekte zu kennen.

Die Basis importiert die Erweiterung **nie**; die Abhängigkeit zeigt nur in eine
Richtung: Erweiterung → Basis.

## Stabilitätszusage

Öffentlich und damit API ist **ausschließlich**:

- das Modul `custom_components.frickeldave_store.extension` und alle Namen in
  seinem `__all__`;
- die Bedeutung der Datentypen und ihrer Felder in diesem Modul;
- `CONTRACT_VERSION`;
- die reservierten Feature-Namen `publish` und `import` samt Request-/Response-Payload
  (siehe unten und im README der Erweiterung);
- `POST /api/frickeldave_store/features/{feature}`;
- `features` in `GET /api/frickeldave_store/me` und `GET/PATCH /api/frickeldave_store/settings`;
- `feature_details` in `/settings` (nur `app_admin`);
- das Event `frickeldave_store_features_changed`.

Privat und ausdrücklich **nicht** Teil des Vertrags sind alle übrigen Module der Basis:
`store`, `products`, `views`, `permissions`, `models`, `const`, `export`, `importer`,
`panel`, `frontend/` sowie die Laufzeit (`FrickeldaveStoreRuntime`, `runtime_for`,
`hass.data["frickeldave_store"]`), der Datenordner, `frickeldave_store.data`, Bilder
und Snapshots.

Regeln für eine Erweiterung:

- Imports nur aus `custom_components.frickeldave_store.extension`.
- Kein Zugriff auf `hass.data["frickeldave_store"]`, keine Dateipfade, kein
  Monkeypatching.
- Keine eigenen HTTP-Views unter `/api/frickeldave_store/*`; die Erweiterung liefert
  Handler, die Basis routet und authentifiziert.
- Keine eigene Persistenz der Store-Daten; die Basis bleibt alleinige Eigentümerin.

## Versionierung

`CONTRACT_VERSION` ist eine Ganzzahl (aktuell `1`) und wird bei jeder inkompatiblen
Änderung erhöht. Additive, optionale Felder erhöhen sie nicht.

| Fall | Verhalten |
| --- | --- |
| `registration.contract_version == CONTRACT_VERSION` | Registrierung erfolgreich |
| abweichende Vertragsversion | `ExtensionError` mit Code `contract_mismatch` (deutsche Meldung) |

Die Erweiterung deklariert ihrerseits `MIN_CONTRACT_VERSION = 1` und verweigert den
Start, wenn die installierte Basis älter ist. Offiziell getestet werden der aktuelle
und der vorherige Vertrag (N und N-1). Ein öffentlicher Name wird erst nach einem
Contract-Bump und einer Deprecation über mindestens ein Minor-Release entfernt.

## Reihenfolge und Abhängigkeit

Die Basis muss eingerichtet sein, bevor die Erweiterung arbeitet. In der `manifest.json`
der Erweiterung:

```json
{
  "domain": "frickeldave_store_sync",
  "dependencies": ["frickeldave_store"]
}
```

`dependencies` stellt sicher, dass Home Assistant die Basis zuerst einrichtet; fehlt sie,
schlägt das Setup der Erweiterung mit einer klaren Meldung fehl. HACS löst keine
Abhängigkeiten auf – README, Release Notes und gegebenenfalls ein Repair-Issue müssen
darauf hinweisen. Falls die Ziel-HA-Version Custom-zu-Custom-`dependencies` nicht
auflöst, ist der Rückfall `after_dependencies: ["frickeldave_store"]` plus ein harter
Runtime-Check in `async_setup_entry` (Basis nicht eingerichtet → `ConfigEntryNotReady`).

## Beispielregistrierung

```python
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

from custom_components.frickeldave_store.extension import (
    CONTRACT_VERSION,
    FEATURE_IMPORT,
    FEATURE_PUBLISH,
    ROLE_APP_ADMIN,
    ROLE_PRODUCT,
    ExtensionRegistration,
    FeatureRequest,
    FeatureResponse,
    FeatureSpec,
    async_get_facade,
    async_register_extension,
    async_unregister_extension,
)


async def _async_publish(request: FeatureRequest) -> FeatureResponse:
    if not request.facade.can(ROLE_APP_ADMIN):
        request.facade.require(ROLE_APP_ADMIN)  # wirft PermissionDenied
    export = await request.facade.async_build_export()
    if export.empty_catalog and not request.payload.get("allow_empty"):
        return FeatureResponse({"warning": "Der Katalog ist leer.", "products": 0, "images": 0})
    # … Erweiterungslogik (GitHub-Commit, Workflow-Dispatch) …
    return FeatureResponse({"target": request.payload.get("target", "dev")})


async def _async_import(request: FeatureRequest) -> FeatureResponse:
    facade = request.facade
    catalog = request.payload.get("products", [])
    result = await facade.async_apply_import_batch(
        catalog, overwrite=bool(request.payload.get("overwrite"))
    )
    return FeatureResponse(result.as_dict())


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    registration = ExtensionRegistration(
        name="frickeldave_store_sync",
        version="0.1.0",
        contract_version=CONTRACT_VERSION,
        features={
            FEATURE_PUBLISH: FeatureSpec(
                name=FEATURE_PUBLISH,
                title="Veröffentlichen",
                required_role=ROLE_APP_ADMIN,
                provides_panel_routes=True,
                ready=True,
                handler=_async_publish,
            ),
            FEATURE_IMPORT: FeatureSpec(
                name=FEATURE_IMPORT,
                title="Katalogimport",
                required_role=ROLE_PRODUCT,
                provides_panel_routes=True,
                ready=True,
                handler=_async_import,
            ),
        },
    )
    await async_register_extension(hass, registration)
    entry.async_on_unload(
        lambda: hass.async_create_task(async_unregister_extension(hass, registration.name))
    )
    return True
```

Die Fassade ist auch außerhalb einer Anfrage verfügbar:

```python
facade = await async_get_facade(hass, user_id="")  # None, wenn die Basis nicht läuft
```

## Datentypen

| Typ | Zweck |
| --- | --- |
| `FeatureSpec` | Fähigkeit: `name`, `title`, `required_role`, `provides_panel_routes`, `ready`, `reason`, `handler` |
| `FeatureStatus` | Zustand für `/me` und `/settings`: `available`, `reason`, `provider`, `contract_version` |
| `ExtensionRegistration` | Registrierung: `name`, `version`, `contract_version`, `features`, `panel_actions` |
| `FeatureRequest` | Handler-Eingabe: `feature`, `payload`, `user_id`, `user_name`, `facade` |
| `FeatureResponse` | Handler-Ausgabe: `payload`, `status` (Standard 200) |
| `ImportItem` | Batch-Eintrag: `article_number`, `payload`, `images` (Tupel `ImageUpload`) |
| `ImageUpload` | `name`, `data` (Bytes), `is_main` |
| `ProductSnapshot` | `article_number`, `name`, `description`, `price`, `size`, `category`, `tags`, `visible`, `customizable`, `archived`, `stock`, `images`, `created_at`, `updated_at` |
| `ImageSnapshot` | `name`, `is_main`, `order` |
| `ExportBundle` | `catalog_path`, `images_path`, `catalog_bytes`, `artifacts`, `product_count`, `image_count`, `empty_catalog`, `generated_at` |
| `ExportArtifact` | `path` (POSIX, relativ zum Ziel-Repository), `data`, `kind` (`catalog`/`image`), `article_number` |
| `ImportBatchResult` | `created`, `updated`, `unchanged`, `skipped`, `failed`, `images_written`, `errors`, `snapshot`, `summary` (+ `as_dict()`) |
| `ImportItemError` | `article_number`, `message` |
| `ExtensionError` | `code`, `message`, `status` (+ `as_dict()`) |

`payload` von `FeatureRequest` ist immer ein JSON-Objekt (die Basis validiert nur das).
Die fachliche Validierung bleibt beim Handler, damit die Basis keine
Frickeldave-Kenntnis enthält.

## Fassade (`StoreFacade`)

| Methode / Eigenschaft | Ergebnis |
| --- | --- |
| `async_products()` | alle Produkte inkl. archivierter, mit Bildmetadaten (`ProductSnapshot`) |
| `async_locations()` | Lagerstätten als unveränderliche `{"id", "name"}`-Zuordnungen |
| `async_article_numbers()` | `frozenset` aller vergebenen Artikelnummern |
| `can(role)` / `require(role)` | Rechteprüfung mit denselben Rollen wie das Panel (`product`, `finance`, `app_admin`); die Fassade ist an den anfragenden HA-Benutzer gebunden |
| `async_create_snapshot(reason)` | Snapshot vor schreibenden Vorgängen; liefert den Dateinamen (kein Pfad) oder `None` |
| `async_build_export(catalog_path=…, images_path=…, visible_only=False)` | Website-Export im Speicher (`export.build_catalog`), inklusive Bilddaten |
| `async_apply_import_batch(products, overwrite=False)` | validierter Batch über den bestehenden Importer; legt vorher einen Snapshot an |
| `settings` | Einstellungen ohne Geheimnisse, nur lesend (`MappingProxyType`) |
| `hass`, `user_id`, `user_name`, `contract_version` | Kontext |

Die Fassade gibt keine internen Objekte, Privatattribute oder Dateisystempfade heraus.
`async_apply_import_batch` akzeptiert Katalogprodukte im Website-Schema oder `ImportItem`
mit optionalen Bilddaten. Archivierte Produkte bleiben unangetastet; `overwrite`
übernimmt ausschließlich `name`, `description`, `price`, `size`, `tags`, `visible`,
`customizable` und `stock`. Artikelnummer, Kategorie, Bilder, Buchungen und
`createdAt` bleiben lokal.

## Feature-Endpunkt

`POST /api/frickeldave_store/features/{feature}` mit optionalem JSON-Objekt als Body.

- Ist eine Erweiterung für diesen Namen registriert, prüft die Basis
  `required_role`, baut eine benutzergebundene Fassade und ruft den Handler auf.
- Ohne Erweiterung bedient die Basis `publish` und `import` selbst (Fallback,
  unverändertes Verhalten von 0.1.15).
- Unbekannte Namen: `404 feature_unknown`.
- `ready=False`: `409 feature_unavailable` mit der deutschen `reason`.
- Ungültige Handler-Antwort: `502 handler_failed`.

Fehlerformat wie im Rest der API: `{"error": {"code": ..., "message": ...}}`.

Payloads der reservierten Features:

| Feature | Rolle | Request | Response |
| --- | --- | --- | --- |
| `publish` | `app_admin` | `target` (`dev`/`prod`), `dry_run`, `message`, `allow_empty`, `confirm_removals` | `counts`, `products`, `images`, `files`, `deployment`, `summary`, `warning`, `target`, `dry_run`, `removal_guard` |
| `import` | `product`; eigene `source_url`/`image_base_url` nur `app_admin` | `source_url`?, `image_base_url`?, `import_images`, `dry_run`, `overwrite` | `created`, `overwritten`, `skipped`, `failed`, `images_imported`, `products_total`, `errors`, `dry_run`, `would_create`, `would_overwrite`, `unchanged`, `summary` |

## Feature-Status in der API

`GET /api/frickeldave_store/me` und `GET/PATCH /api/frickeldave_store/settings` enthalten
zusätzlich:

```json
{
  "features": {
    "publish": {
      "available": true,
      "reason": "",
      "provider": "frickeldave_store",
      "contract_version": 1
    },
    "import": {
      "available": true,
      "reason": "",
      "provider": "frickeldave_store",
      "contract_version": 1
    }
  }
}
```

`provider` ist der Name der Erweiterung oder `frickeldave_store` für den
eingebauten Fallback. `/settings` (nur `app_admin`) liefert zusätzlich
`feature_details` mit `name`, `title`, `required_role`, `provides_panel_routes`,
`available`, `reason`, `provider` und `contract_version` – die Grundlage für das
Feature-Gating im Panel.

Bestehende Felder von `/me` und `/settings` bleiben unverändert; die Ergänzung ist additiv.

## Event

`frickeldave_store_features_changed` feuert nach jeder Registrierung und Abmeldung:

```json
{
  "changed": "frickeldave_store_sync",
  "features": { "publish": { "available": true, "reason": "", "provider": "frickeldave_store_sync", "contract_version": 1 } }
}
```

Ein offenes Panel kann daraufhin `/me` neu laden. Registriert sich eine Erweiterung erst
nach dem Panel-Setup, bricht das nichts: Weder Panel noch Runtime hängen an der
Registrierungsreihenfolge.

## Fehlercodes

| Code | Status | Bedeutung |
| --- | --- | --- |
| `contract_mismatch` | 400 | Vertragsversion der Erweiterung passt nicht zur Basis |
| `already_registered` | 409 | Name bereits mit abweichender Registrierung belegt |
| `not_registered` | 404 | Abmeldung für einen unbekannten Namen |
| `invalid_registration` | 400 | Registrierung unvollständig (Name, Titel, Rolle, Feature-Name) |
| `feature_unknown` | 404 | Feature weder registriert noch eingebaut |
| `feature_unavailable` | 409 | Feature installiert, aber nicht eingerichtet bzw. ohne Handler |
| `handler_failed` | 502 | Handler lieferte keine `FeatureResponse` |
| `invalid_request` | 400 | Vom Handler gemeldeter fachlicher Fehler (Beispielcode) |

## Grenzen dieser Version

`0.1.16` ist die additive Brücke: `publish.py` und `importer.py` bleiben in der Basis,
die eingebauten Endpunkte `POST /publish` und `POST /import/catalog` funktionieren
unverändert weiter. Erst `0.2.0` entfernt die frickeldave-spezifischen Handler; dann
bleiben `features` und `feature_details` als Gating-Instrument und der Fallback entfällt.
