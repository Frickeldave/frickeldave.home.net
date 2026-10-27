# Changelog

Alle nennenswerten Änderungen an diesem Projekt werden hier festgehalten.
Format angelehnt an [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
Versionierung nach [SemVer](https://semver.org/lang/de/).

## [Unreleased]

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
