# Changelog

Alle nennenswerten Änderungen an diesem Projekt werden hier festgehalten.
Format angelehnt an [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
Versionierung nach [SemVer](https://semver.org/lang/de/).

## [Unreleased]

## [0.1.7] - 2026-10-28

### Changed

- **Design-Review nach der offiziellen Home-Assistant-Design-Dokumentation:**
  semantische `--ha-color-*`-Tokens (mit Legacy-Fallbacks für ältere HA-Versionen),
  Typografie-, Spacing-, Radius- und Motion-Tokens, HA-Button-Größen und -Varianten,
  Dialog-Anatomie (Schließen oben links, Footer unten rechts, destruktive
  Bestätigungen mit Fokus auf „Abbrechen“), Listen- und Formular-Patterns,
  Empty-/Loading-/Error-Zustände, UI-Text (Erstellen/Hinzufügen,
  Entfernen/Löschen, Archivieren), Fokusringe und Reduced Motion.
- Produkte werden weiterhin ausschließlich archiviert (kein Hard-Delete);
  dauerhaft gelöscht werden nur Dateien und Tags.

## [0.1.6] - 2026-10-28

### Changed

- **Topbar im HA-Stil:** vollbreite, sticky App-Bar außerhalb des Inhaltsbereichs mit
  HA-Metriken (56 px Höhe, 48 px Icon-Buttons, `--header-height`, `--app-header-*`,
  `--ha-space-*`); die Aktionen Aktualisieren, Katalog importieren und Produkt anlegen
  sind jetzt reine Icon-Buttons mit deutschen Labels; die Unterzeile „Angemeldet als …“
  entfällt.
- **Konfigurationspanel:** eigene vollbreite Topbar mit Zurück-/Vorwärts-Navigation
  (Fallback `/config/integrations`) und sinnvollen deaktivierten Zuständen.
- **UI-Review:** Karten, Buttons, Eingabefelder, Tabs und Badges an HA-Tokens und
  -Metriken angeglichen (12 px Radien, 44 px Touch-Ziele, Fokusringe, Kontrast).

## [0.1.5] - 2026-10-28

### Added

- **Tabbed Konfigurationspanel**: Ein zweites `panel_custom`-Panel hängt über
  `config_panel_domain` am Zahnrad des Integrationseintrags
  (`/frickeldave-store-config?config_entry=<id>`, `require_admin`) und bietet die Tabs
  **Benutzerverwaltung** und **KI-Einstellungen**. Das Seitenleisten-Panel bleibt
  unverändert; beide Panels werden idempotent registriert und beim Unload entfernt.
- **Benutzerverwaltungs-API**: `GET /api/frickeldave_store/users` liefert Freigaben mit
  echten HA-Namen, Rollen-Labels und noch nicht freigegebenen aktiven Benutzern
  (`available_users`, nur ID und Name). `PUT`/`DELETE /users/{user_id}` legen Freigaben
  an, ändern Rollen oder entziehen sie, persistieren in der Option `users` und wenden
  die Änderung sofort an. Der letzte `app_admin` ist gegen Löschen und Herabstufen
  geschützt (`409 conflict`); unbekannte Benutzer-IDs bleiben bearbeitbar.
- Tests für Liste, Anlegen, Ändern, Löschen, Namensauflösung, Rollenvalidierung,
  Letzter-Admin-Schutz, sofortigen Rechteentzug und `403` ohne `app_admin`.
- Dokumentation der Oberfläche und der Endpunkte in README, `docs/permissions.md` und
  `docs/architecture.md`.

### Changed

- **Einstellungen nicht mehr im Seitenleisten-Menü**: Der Drei-Punkte-Eintrag „KI“ im
  Panel entfällt; Einstellungen liegen ausschließlich im Konfigurationspanel am
  Integrationseintrag (Frontend-Umsetzung durch das Frontend-Team). Der Options-Flow
  bleibt als Rückfallebene erhalten.

## [0.1.4] - 2026-10-28

### Changed

- **Tags-Filter jetzt ODER-verknüpft**: Bei `GET /products` und den WebSocket-Filtern
  genügt ein Produkt einem der gewählten Tags, statt alle erfüllen zu müssen; die
  Verknüpfung entspricht damit der Kategorieauswahl. Groß-/Kleinschreibung bleibt
  unerheblich, eine leere Tag-Auswahl filtert weiterhin nicht, und alle übrigen
  Filter bleiben UND-verknüpft.
- **Panel-Topbar**: Burger-Menü und Drei-Punkte-Menü ersetzen die bisherigen Tabs.
  Einstellungen (KI) und Tag-Verwaltung liegen jetzt im Menü.

## [0.1.3] - 2026-10-27

### Added

- **Katalogimport** (`POST /api/frickeldave_store/import/catalog`): Der bestehende
  Website-Katalog `https://frickeldave.de/data/handmade.json` wird samt Bildern
  (172 Dateien im Website-Repository) idempotent in den Store übernommen. Die
  Zuordnung erfolgt über die `articleNumber`; vorhandene Produkte werden nie
  überschrieben (`skipped`), Duplikate im Dokument gelten als `failed`, gelöscht
  wird nichts. Bilder werden als `<artikelnummer>-NN.jpg` (01…10, erst `.jpg`,
  dann `.jpeg`) geladen und im Panel-Layout gespeichert; Bildabrufe laufen mit
  höchstens fünf parallelen Anfragen und eigenem Zeitlimit, Bildfehler brechen
  den Produktimport nicht ab. Nur ein nicht lesbares oder strukturell ungültiges
  Katalogdokument lehnt die Anfrage ab, ohne den Store zu verändern.
- Eigene `source_url`/`image_base_url` erfordern `app_admin` und sind auf
  `https` plus die Hosts `frickeldave.de` und `raw.githubusercontent.com`
  beschränkt (SSRF-Schutz); die Standardquellen genügen der Rolle `product`.
- Tests (`tests/test_importer.py`, 38 Fälle, gemocktes aiohttp ohne Netzwerk)
  für Feldabbildung, Idempotenz, Fehlerfälle, Bilderkennung, Obergrenzen und
  die Rechteprüfung des Endpunkts.
- Dokumentation des Imports in README, `docs/data-model.md` und
  `docs/architecture.md`.

## [0.1.2] - 2026-10-27

### Fixed

- Das Panel zeigte im Browser nur eine schwarze Seite: Das ES-Modul importierte
  Lit über den nackten Spezifizierer `lit`. Home Assistant liefert keine
  Import-Map aus, sodass `panel_custom` den Import mit "Failed to resolve module
  specifier 'lit'" abbrach. Lit 3.3.3 liegt jetzt als vendored ESM-Bundle unter
  `custom_components/frickeldave_store/frontend/lit.js`; das Panel importiert
  `./lit.js`. Der Build ist über `package.json` und `npm run build:frontend`
  reproduzierbar (lit und esbuild exakt gepinnt) und wird von
  `tests/test_frontend_assets.py` abgesichert: keine nackten Spezifizierer im
  Panel, Bundle vorhanden und mit `LitElement`, `html` und `css` exportiert.

## [0.1.1] - 2026-10-27

### Fixed

- Config- und Options-Flow verwenden das von Home Assistant mitgelieferte
  Schema-Modul (`probatio` ab HA 2026.10, sonst `voluptuous`) und definieren das
  Feld `users` als serialisierbaren String. Dadurch schlägt das Laden des
  Formulars nicht mehr mit "Config flow could not be loaded: 500 Internal Server
  Error" fehl; die Freigabevalidierung erfolgt weiterhin im Flow-Schritt und
  zeigt `invalid_users` an. Neue Regressionstests serialisieren die Formularschemata
  wie der HTTP-Layer.
- Die WebSocket-Kommandos `frickeldave_store/products` und
  `frickeldave_store/subscribe` verwenden ein gültiges Filter-Schema
  (Marker als Schlüssel, Validatoren als Werte). Bisher standen die
  `Optional`-Marker als Werte im Mapping, wodurch jede Nachricht mit Filterfeldern
  mit "not a valid value at 'search'" abgelehnt wurde. Die Feldnamen für
  `_query_from_msg` liegen nun in einer separaten Konstante; ein Regressionstest
  prüft alle Kommando-Schemas mit realistischen Filterwerten und weist ungültige
  Werte (z. B. `limit: "abc"`) zurück.

## [0.1.0] - 2026-10-27

### Added

- Grundgerüst der Home-Assistant-Custom-Integration `frickeldave_store`
  (Manifest, HACS-Metadaten, CI mit ruff, mypy, pytest, hassfest und HACS-Validation).
- Datenschicht mit HA-`Store` (Schlüssel `frickeldave_store.data`, Schema-Version 1),
  Bildablage unter `config/frickeldave_store/images/<artikelnummer>/`, atomaren
  Schreibvorgängen, Snapshots vor Migrationen und `async_migrate_entry`.
- Produktdomain: Anlegen mit eindeutiger Artikelnummer, Bearbeiten, ausschließlich
  Soft-Delete, unveränderliche Kategorie, Tag-Verwaltung, Bestandsbuchungen,
  kombinierbare Filter (Kategorie ODER, Tags UND, übrige UND) und Sortierung.
- Config Flow und Options Flow inklusive deutscher und englischer Übersetzungen.
- Berechtigungsschicht mit den Rollen `product`, `finance` und `app_admin`,
  Default Deny, Prüfung je HTTP-/WebSocket-Anfrage und sofortigem Rechteentzug.
- HTTP-API unter `/api/frickeldave_store` und WebSocket-Kommandos inklusive
  Panel-Registrierung (`panel_custom`, `frickeldave-store`).
- KI-Beschreibungsvorschläge über eine Anbieter-Abstraktion mit DeepSeek-Client,
  EXIF/GPS-Bereinigung und ausschließlich manueller Übernahme.
- Idempotenter Katalogexport im Schema der Website plus CLI.
- Dokumentation (`docs/`) und Testsuite (Domain-, API- und HA-Integrationstests).

Referenz: `Frickeldave/frickeldave.github.io#289`.
