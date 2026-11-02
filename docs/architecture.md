# Architektur

## Überblick

`frickeldave_store` ist eine Home-Assistant-Custom-Integration ohne Supervisor-Bezug.
Sie bündelt vier Schichten:

```
HTTP (aiohttp HomeAssistantView)   WebSocket (websocket_api)
                \                  /
                 \                /
              ProductService (products.py)
                 |            |
          CatalogStore    AIService (ai.py)
           (store.py)          |
        /            \         |
  HA Store (JSON)  ImageStorage
```

- **`views.py`** – HTTP-API, JSON only, Fehlerformat `{"error": {"code", "message"}}`.
- **`websocket_api.py`** – identische Rechteprüfung, Abo für Panel-Aktualisierungen.
- **`products.py`** – Fachlogik: Artikelnummern, Filter, Sortierung, Bilder, Buchungen.
- **`store.py`** – Persistenz: HA-`Store` (atomar, versioniert), Bilddateien, Migrationen,
  Snapshots. Reine Dateiablage (`FileBackend`) für CLI und Tests ohne HA.
- **`storage.py`** – Auflösung und Migration des Datenordners (`config`, `share`,
  `media`), Schreibbarkeitsprüfung und Backup-Kennzeichnung für die API.
- **`permissions.py`** – Rollen, Default Deny, Prüfung je Anfrage.
- **`ai.py`** – Anbieter-Abstraktion, DeepSeek-Client, Metadaten-Bereinigung.
- **`batch_import.py`** – validierter Import-Batch für Erweiterungen
  (`async_import_products`): schreibt fertige Produkte und optionale Bilddaten in den
  Store, ohne Netzzugriff. Seit 0.2.0 die einzige Importlogik der Basis; der frühere
  Katalogabruf (`importer.py`) liegt in der Erweiterung.
- **`export.py`** – idempotenter Katalogexport im Website-Schema.
- **`extension.py`** – öffentlicher Erweiterungsvertrag (Contract 1): Registrierung,
  Feature-Status, Fassade auf den Store. Details in
  [`extension-api.md`](extension-api.md).
- **`panel.py`** – statische Dateien und zwei `panel_custom`-Panels: Seitenleiste
  (`frickeldave-store`) und Konfigurationspanel am Integrationseintrag
  (`frickeldave-store-config`).
- **`frontend/`** – Lit-Web-Component, gepflegt vom Frontend-Team (nicht Teil dieser Schicht).

## Anfragefluss

1. HA authentifiziert die Anfrage; `request["hass_user"]` bzw. `connection.user` liefert
   die stabile Benutzer-ID.
2. Die Route ruft `_authorize(request, role)` auf → `PermissionManager.require()`.
   Unbekannte Benutzer und fehlende Rollen enden mit `403` ohne geschützte Daten im Body.
3. Die Fachlogik arbeitet auf dem In-Memory-Store und persistiert atomar.
4. Listener benachrichtigen das WebSocket-Abo; jeder Push prüft die Rolle erneut.

`GET /api/frickeldave_store/me` ist bewusst **nicht** rollengeschützt: Es liefert nur die
eigene Identität und die effektiven Rechte (bei unbekannten Benutzern leere Listen), damit
das Panel „kein Zugriff“ anzeigen kann. Es enthält keine Produkt-, Kunden- oder Finanzdaten.
Zusätzlich stehen dort `version` (Integrationsversion aus `const.VERSION`),
`storage_schema_version` (Katalogschema aus `models.SCHEMA_VERSION`) und
`panel_module_url` (die tatsächlich registrierte, cache-gebustete Modul-URL) – damit kann
das Panel den geladenen Build anzeigen. `GET`/`PATCH /settings` führen ebenfalls `version`.
Beide Antworten enthalten zusätzlich `features` (Verfügbarkeit je Fähigkeit, Anbieter,
`contract_version`); `/settings` liefert für `app_admin` außerdem `feature_details` mit
Titel, benötigter Rolle und `provides_panel_routes`.

## Konfigurationspanel

Seit 0.1.5 registriert `panel.py` zusätzlich zum Seitenleisten-Panel ein zweites
`panel_custom`-Panel:

| Parameter | Wert |
| --- | --- |
| `frontend_url_path` | `frickeldave-store-config` |
| `webcomponent_name` | `frickeldave-store-config-panel` |
| `module_url` | `/frickeldave_store_frontend/frickeldave-store-config-panel.js?v=<VERSION>` |
| `config_panel_domain` | `frickeldave_store` |
| `require_admin` | `true` |
| `embed_iframe` | `false` |
| `config` | `{"api_prefix": "/api/frickeldave_store"}` |

`config_panel_domain` bewirkt, dass das Zahnrad am Integrationseintrag auf
`/frickeldave-store-config?config_entry=<id>` verlinkt statt den Options-Flow zu öffnen.
Das Panel enthält die Tabs **Allgemeine Einstellungen**, **Benutzerverwaltung**,
**Lagerstätten** und **KI-Einstellungen**; einen Tab „Veröffentlichen“ gibt es seit
0.2.0 nicht mehr, weil Repository, Branches und Token in der Erweiterung liegen. Die
Seitenleisten-Registrierung bleibt unverändert. Beide Panels werden idempotent
registriert und beim Unload gemeinsam entfernt.

### Cache-Bust der Panel-Module

Beide `module_url`-Werte enden auf `?v=<VERSION>` (`const.module_url()`). `panel_custom`
reicht den String unverändert an das Frontend weiter, das ihn als ES-Modul-URL lädt – die
Query ist dort zulässig und ergibt pro Version eine eigene Modul-Identität. Die statische
Route wird weiterhin mit `cache_headers=False` registriert (kein `max-age`); zusätzlich
liefert `FrontendAssetView` direkte Dateien des Frontend-Verzeichnisses mit
`Cache-Control: no-cache, must-revalidate`. Das betrifft insbesondere das relativ
importierte `./lit.js`, das keine Versions-Query trägt: Es wird bei jedem Laden
revalidiert, sodass kein gemischter Zustand aus neuem Panel und altem Lit-Bundle entsteht.

Die Config-Entry-Optionen bleiben die einzige Quelle der Wahrheit: Die
Benutzerverwaltungs-API (`PUT`/`DELETE /api/frickeldave_store/users/{user_id}`)
schreibt `users` über `hass.config_entries.async_update_entry` und ruft anschließend
`_async_apply_entry_config` auf. Dadurch wirkt ein Rechteentzug auf die nächste Anfrage,
und der Options-Flow bleibt als Rückfallebene funktionsfähig. Die Sicherheitsregel
„letzter `app_admin` bleibt“ verhindert das Aussperren der Verwaltung (`409 conflict`).

## Erweiterungspunkt (Contract 1)

`frickeldave_store` bleibt allein funktionsfähig, lässt sich aber um eine zweite Custom
Integration (`frickeldave_store_sync`) erweitern. Der Vertrag liegt vollständig in
`custom_components/frickeldave_store/extension.py`; alles andere bleibt privat.

```
Erweiterung (frickeldave_store_sync)
        |  importiert nur
        v
extension.py  ──registriert Handler──>  hass.data["frickeldave_store_extensions"]
        |                                        |
        |  Fassade (StoreFacade)                 |  Status
        v                                        v
CatalogStore / ImageStorage / export / batch_import   GET /me, /settings, POST /features/{feature}
```

- **Import-Richtung:** Erweiterung → Basis. Die Basis importiert die Erweiterung nie und
  kennt ihren Domainnamen nicht; sie speichert nur, was registriert wurde.
- **Registrierung:** `async_register_extension()` in `async_setup_entry` der Erweiterung,
  Abmeldung über `async_unregister_extension()` bzw. `entry.async_on_unload`. Die
  Registrierung ist idempotent und validiert `contract_version` gegen
  `CONTRACT_VERSION` (aktuell `1`).
- **Fassade:** Produkte inkl. Bildmetadaten, Lagerstätten, Artikelnummern,
  Rechteprüfung, Snapshot, Export im Website-Schema und ein validierter Import-Batch.
  Keine internen Objekte, keine Privatattribute, keine Dateipfade.
- **Routing:** `POST /api/frickeldave_store/features/{feature}` authentifiziert und
  prüft `required_role`, dann ruft die Basis den Handler der Erweiterung auf. Mit 0.2.0
  gibt es keinen eingebauten Fallback mehr: ohne Registrierung antworten `publish` und
  `import` mit `409 feature_unavailable`, unbekannte Namen mit `404 feature_unknown`.
- **Gating:** `features` in `/me` und `/settings` (plus `feature_details` für
  `app_admin`) sagen dem Panel, welche Fähigkeit verfügbar ist, wer sie anbietet und
  warum sie gegebenenfalls nicht bereit ist. Das Event
  `frickeldave_store_features_changed` aktualisiert ein offenes Panel.
- **Versionierung:** `CONTRACT_VERSION` steigt nur bei inkompatiblen Änderungen;
  additive Felder erhöhen sie nicht. Ein abweichender Vertrag endet mit
  `ExtensionError("contract_mismatch")`.

Referenz: [`extension-api.md`](extension-api.md).

## Zentrale Entscheidungen

| Entscheidung | Begründung |
| --- | --- |
| Metadaten über HA-`Store`, Datei unter `<data_dir>/frickeldave_store.data` | keine zusätzliche Datenbank, atomare Schreibvorgänge; `data_dir` ist konfigurierbar |
| Bilder als Dateien unter `<data_dir>/images/` | nicht in `.storage`; Store-Datei und Bilder liegen immer gemeinsam |
| Standard `data_path = config` | im Container-Backup enthalten; `share`/`media` sind bewusst vom Backup ausnehmbar |
| Keine YAML-Konfiguration | Einrichtung ausschließlich über Config Flow; Einstellungen primär über das Konfigurationspanel, Options Flow als Rückfallebene |
| `users`-Option bleibt Quelle der Wahrheit | Panel-UI und Options Flow schreiben dasselbe Format; sofortige Anwendung je Mutation |
| `require_admin=False` | `require_admin` steuert nur die Sichtbarkeit, nie den Datenzugriff |
| Server-seitige Prüfung je Anfrage | Rechteentzug wirkt sofort, auch bei offenem Panel |
| Artikelnummer = Kategoriepräfix + 4 Zeichen | entspricht dem Altbestand (`LAH3K3`), 6 Zeichen, ohne `I/O/0/1` |
| Kategorie unveränderlich | verhindert inkonsistente Artikelnummern; neues Produkt statt Wechsel |
| Ausschließlich Soft-Delete (`archived`) | Export und Website bleiben konsistent, kein Hard-Delete-Pfad |
| KI-Vorschläge ohne Persistenz | bestehende Beschreibungen werden nie automatisch überschrieben |
| Export im Website-Schema, idempotent | Website bleibt Konsument, keine Schemaänderung nötig |
| Import nur anlegen, nie überschreiben | Bestandsdaten und Panel-Pflege haben Vorrang; keine Datenverluste durch Wiederholung |
| Import-Quellen nur `https` + Host-Allowlist | verhindert SSRF über `source_url`/`image_base_url`; eigene Quellen nur für `app_admin` |
| Bildimport über dasselbe `ImageStorage`-Layout | Panel, Export und Import teilen sich einen Bildbestand |
| Erweiterungspunkt als versionierter Vertrag (`extension.py`) statt `hass.data`-Kopplung | klare, prüfbare Grenze; die Basis bleibt allein lauffähig, die Erweiterung sieht nur die Fassade |

## Speicherort und Backup-Ausschluss

Die Option `data_path` bestimmt den Datenordner; erlaubt sind ausschließlich
`config` (Standard), `share` und `media`. Freie Pfade werden abgelehnt.

| Wert | Auflösung | Backup |
| --- | --- | --- |
| `config` | `hass.config.path("frickeldave_store")` | enthalten |
| `share` | `/share/frickeldave_store` | ausgeschlossen |
| `media` | `/media/frickeldave_store` | ausgeschlossen |

`resolve_data_dir()` in `storage.py` löst den Wert auf; die Basis-Mounts sind
modulweite Konstanten (`SHARE_ROOT`, `MEDIA_ROOT`), die Tests auf `tmp_path`
setzen – echtes `/share`/`/media` wird nie berührt. Vor dem Speichern legt
`ensure_data_dir_sync()` den Ordner an und prüft die Schreibbarkeit. Fehlt der
Mount, schlägt das Setup mit `ConfigEntryError` fehl; es gibt **keinen** stillen
Fallback auf `config`.

`GET /settings` liefert den Abschnitt:

```json
{
  "storage": {
    "data_path": "config",
    "effective_path": "/config/frickeldave_store",
    "backup_included": true,
    "exclude_from_backup": false,
    "paths": [
      {"label": "Metadaten", "path": "/config/frickeldave_store/frickeldave_store.data"},
      {"label": "Bilder", "path": "/config/frickeldave_store/images"},
      {"label": "Snapshots", "path": "/config/frickeldave_store/backups"}
    ],
    "choices": [
      {"id": "config", "label": "Konfigurationsordner (config)", "description": "...", "backup_included": true}
    ]
  }
}
```

`PATCH /settings` akzeptiert zusätzlich `{"storage": {"data_path": "share"}}`
(unbekannte Felder → `400`). Nach einer erfolgreichen Migration antwortet die
Route mit dem aktualisierten Payload und `"reload_required": true`.

**Migration:** Sind alter und neuer Ordner verschieden, kopiert
`migrate_data_sync()` Store-Datei, `images/` und `backups/` in den Zielordner.
Ein Ziel mit vorhandener `frickeldave_store.data` oder nicht-leerem `images/`
wird mit `409 conflict` abgelehnt. Die Quelle bleibt erhalten. Danach wird die
Option über `hass.config_entries.async_update_entry` geschrieben und der Entry
per `async_schedule_reload` neu geladen. Der Update-Listener erkennt zusätzlich
Änderungen aus dem Options-Flow (`runtime.store.data_dir != resolve_data_dir()`),
migriert idempotent (Marker `runtime.migrated_to`) und plant den Reload. Schlägt
eine externe Änderung fehl, wird die Option auf den tatsächlich geöffneten
Ordner zurückgesetzt, damit nie ein leerer Store geöffnet wird.

### Speicherort des HA-`Store`

Der `Store`-Helper erhält den Schlüssel `frickeldave_store.data` (Version 1,
Minor-Version 1) und den aufgelösten `data_dir`-Pfad. Ältere HA-Versionen (bis
2025.1) kennen keinen `path`-Parameter; dort überschreibt eine kleine Subclass
die `path`-Property, sodass die Datei ebenfalls unter
`<data_dir>/frickeldave_store.data` liegt. Ab HA-Versionen mit `path`-Parameter
wird dieser direkt gesetzt. Der Dateiname bleibt in beiden Fällen identisch.

Konsequenz für Backups: Home Assistant archiviert bei „Home Assistant settings“
den gesamten Config-Ordner ohne Ausnahmen für Unterordner. `config` ist damit
automatisch gesichert; `share`/`media` müssen separat gesichert werden
(siehe [Backup & Restore](backup-restore.md)).

## Betriebsgrenzen

- Kein Supervisor, kein Add-on, kein `hassio`-Aufruf.
- Kein Hintergrund-Polling externer Dienste (KI nur auf ausdrückliche Anfrage).
- Blockierendes Datei-I/O ausschließlich im Executor (`asyncio.to_thread`,
  `hass.async_add_executor_job`).
- Externe Fehler (KI, Netzwerk) verändern den gespeicherten Datenstand nicht. Der
  Import-Batch (`batch_import.py`) hat keinen Netzzugriff: die Erweiterung übergibt
  geprüfte Produkte, ungültige Einträge landen gesammelt in `errors`, und der Store
  wird erst nach vollständiger Prüfung verändert.
- Datei-I/O bleibt im Executor (`ImageStorage.async_write`). Netzabrufe (Katalog,
  Bilder, GitHub) finden ausschließlich in der Erweiterung statt.

## Nicht Teil dieser Ausbaustufe

PayPal-Synchronisation, Kundenverwaltung, Website-Publish-Automation in dieser Basis
(sie liegt in der Erweiterung), HACS-Default-Katalog, produktive Migration. Siehe README
(Roadmap).
