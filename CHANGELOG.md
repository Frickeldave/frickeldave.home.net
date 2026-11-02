# Changelog

Alle nennenswerten Änderungen an diesem Projekt werden hier festgehalten.
Format angelehnt an [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
Versionierung nach [SemVer](https://semver.org/lang/de/).

## [Unreleased]

## [0.1.12] - 2026-11-02

### Added

- **Weitere Sortierschlüssel für die Produktliste (#289).** `sort` kennt neben
  `name` und `price` jetzt `category` (nach sichtbarem Kategorie-Label, nicht nach
  Rohcode), `stock` (Gesamtbestand über alle Lagerstätten, unabhängig vom
  Lagerstätten-Filter) und `status` (aktiv vor archiviert, sichtbar vor unsichtbar).
  Innerhalb gleicher Werte gilt Name, dann Artikelnummer. `order=desc` dreht nur das
  Hauptkriterium; der Tiebreaker bleibt aufsteigend und damit deterministisch.
  HTTP- und WebSocket-API akzeptieren dieselben Werte.
- **Katalogimport mit Vorschau und wählbarem Überschreiben (#289).**
  `POST /api/frickeldave_store/import/catalog` kennt zwei neue optionale
  Body-Felder, beide Standard `false`. `dry_run: true` lädt und prüft den Katalog,
  schreibt aber nichts – kein Store-Schreibzugriff, keine Bilddatei, kein Snapshot,
  kein `updatedAt` – und liefert die vollständige Vorschau mit `dry_run`,
  `would_create`, `would_overwrite`, `unchanged`, `failed`, `images_imported`,
  `errors` und einer deutschen `summary`. `overwrite: true` überschreibt vorhandene,
  nicht archivierte Produkte mit dem Katalogstand und zählt sie getrennt als
  `overwritten`: übernommen werden Name, Beschreibung, Preis, Größe, Tags,
  Sichtbarkeit, Personalisierbarkeit und Bestände; Artikelnummer, `createdAt`,
  Bilder, Buchungen, Archiv-Status und Kategorie bleiben lokal. Bilder werden nur
  ergänzt, nie gelöscht; archivierte Produkte werden nicht reaktiviert, sondern als
  `skipped` gemeldet. Ohne die neuen Felder bleibt die Antwort abwärtskompatibel
  (neue Zähler sind dann `0`). Nicht-boolesche Werte für `dry_run`/`overwrite`
  lehnt der Endpunkt mit einer deutschen `validation_error` ab; `overwrite` bleibt
  bei der Rolle `product`, weil es ein Sammel-Update auf ohnehin änderbare Daten ist
  und der Store vor dem ersten Schreiben einen Snapshot anlegt.
- **Artikelnummer-Vorschlag für den Anlegen-Dialog (#289).**
  `GET /api/frickeldave_store/products/article-number?category=LA` liefert
  `{"articleNumber", "category", "reserved": false}` (Recht `product`). Die Nummer
  entsteht nach derselben Regel wie beim Speichern und wird gegen alle vorhandenen
  Artikelnummern geprüft, auch gegen archivierte. Sie wird bewusst **nicht**
  reserviert: maßgeblich bleibt `POST /products`, dessen Antwort die tatsächlich
  vergebene Nummer enthält.

### Changed

- **Einheitliches Symbol für beide Veröffentlichungs-Aktionen (#289).** „Nach dev
  veröffentlichen“ und „Nach prod veröffentlichen“ nutzen in der App-Bar jetzt
  dasselbe Branch-Symbol; die Rakete ist entfallen. Damit die beiden Buttons
  unterscheidbar bleiben, trägt jeder ein kleines Kürzel in der unteren rechten
  Ecke („dev“ bzw. „prd“). Das Kürzel ist rein visuell (`aria-hidden`), die
  Unterscheidung für Screenreader läuft weiter über `aria-label` und `title`.

## [0.1.11] - 2026-11-02

### Fixed

- **Datenverlust beim Update behoben (#289).** Nach dem Einspielen einer neuen
  Version waren alle Produkte verschwunden. Ursache: `HassStoreBackend` las die
  Store-Datei über `homeassistant.helpers.storage.Store`. Dessen interner
  `_StoreManager` cached beim HA-Start die Dateiliste von `<config>/.storage` und
  beantwortet jeden Schlüssel, der dort nicht liegt, mit „existiert nicht“
  (`async_fetch` → `(False, None)`). Der Katalog liegt aber in
  `<config>/frickeldave_store/frickeldave_store.data`; `Store.async_load()`
  lieferte deshalb `None`, der Store startete leer und der nächste `async_save`
  überschrieb die echte Datei mit `"products": []`. Da HACS-Updates einen
  HA-Neustart auslösen, trat der Fehler bei jedem Update auf.
- **Eigenes Datei-Backend (#289).** `HassStoreBackend` liest und schreibt die
  Datei jetzt selbst (atomar, gleiches HA-Umschlagformat
  `{"version", "minor_version", "key", "data"}`). Bestehende Dateien, Snapshots
  und ältere Integrationen bleiben kompatibel; `STORAGE_VERSION` bleibt deshalb
  bei `2`.
- **Payload-Schema statt Envelope-Version (#289).** Maßgeblich für die Migration
  ist `data.schemaVersion`. Weicht die Envelope-Version ab (z. B. Store-Version 1
  bei bereits migriertem Payload), wird das protokolliert und nicht erneut
  migriert.
- **Kein stilles Verwerfen von Produkten (#289).** `coerce_payload` überspringt
  keine Produkte mehr: nicht lesbare Einträge führen zu
  `StoreError("products_unreadable")`, der Ladevorgang bricht ab und die
  Store-Datei bleibt unverändert.

### Added

- **Sicherheitsnetz gegen leere Overwrites (#289).** Enthielt die Store-Datei
  Produkte, schreibt `CatalogStore.async_save` niemals einen Stand mit null
  Produkten — außer der Aufrufer bestätigt es ausdrücklich (`allow_empty=True`).
  Im Zweifel bricht das Schreiben mit `refuse_empty_overwrite` ab und die Datei
  bleibt unangetastet.
- **Snapshot vor jedem Schreiben (#289).** Vor Schema-Migrationen entsteht
  weiterhin `<Datenordner>/backups/<zeitstempel>-pre-load-v<n>.json`; zusätzlich
  legt der erste Schreibvorgang nach dem Laden einen Snapshot
  `<zeitstempel>-pre-save.json` an (identische Stände werden nicht dupliziert).

### Changed

- **Lagerstätten-Filter wirkt standardmäßig (#289).** Die Auswahl einer
  Lagerstätte im Panel sendet jetzt immer `in_location=true`; angezeigt werden
  nur Produkte mit Bestand > 0 in dieser Lagerstätte. Die bisherige Checkbox
  „Nur mit Bestand in …“ wurde zum Opt-out „Auch Produkte ohne Bestand anzeigen“
  (Standard: aus, sendet `in_location=false`). Sie ist ohne gewählte Lagerstätte
  deaktiviert und wird beim Zurücksetzen auf „Alle Lagerstätten“ zurückgesetzt.
  Der Hinweistext unter dem Auswahlfeld beschreibt genau dieses Verhalten;
  „Filter zurücksetzen“ und der Hinweis „Filter aktiv“ folgen der neuen Semantik.

### Wiederherstellung

Ein Snapshot lässt sich ohne Neuinstallation zurückholen: HA stoppen, den
gewünschten `*-pre-load-v*.json`/`*-pre-save.json`-Snapshot aus
`config/frickeldave_store/backups/` nach `config/frickeldave_store/frickeldave_store.data`
kopieren, HA starten. Details in `docs/backup-restore.md`.

## [0.1.10] - 2026-11-02

### Added

- **Versionsauskunft (#289):** `GET /api/frickeldave_store/me` liefert zusätzlich
  `version`, `storage_schema_version` und `panel_module_url`; `GET`/`PATCH`
  `/api/frickeldave_store/settings` führen ebenfalls `version`. Damit kann das Panel
  anzeigen, welcher Build geladen ist.
- **Geladene Version sichtbar (#289):** Panel und Konfigurationspanel zeigen die von
  `/me` gelieferte Version dezent als Fußzeile (`v0.1.10`) an. Nach einem Update ist
  damit sofort erkennbar, welcher Build im Browser geladen ist.
- **Kompaktere Listenansicht (#289):** kleinere Vorschaubilder (40 px), engere
  Zeilenabstände und Zell-Paddings, Tags fließen einzeilig mit Umbruch. „Bearbeiten“
  und „Archivieren“ sind jetzt Icon-Buttons (Stift/Archivbox) nebeneinander mit
  `aria-label` und `title`; das Touch-Ziel bleibt 44 px.
- **Einklappbare Suche und Filter (#289):** die Filterleiste hat einen Kopfbereich
  „Suche und Filter“ mit Chevron-Schaltfläche (`aria-expanded`/`aria-controls`). Der
  Zustand bleibt über `localStorage` erhalten; bei aktiven Filtern zeigt ein Badge
  „Filter aktiv“ auch im zugeklappten Zustand, dass gefiltert wird.
- **Bündige Filterleiste (#289):** Suchfeld, Ansichtsumschalter und „Filter
  zurücksetzen“ nutzen eine gemeinsame Steuerelementhöhe (48 px, 44 px Touch-Ziel im
  Umschalter) und stehen auch umgebrochen bündig auf einer Linie; das Suchlabel ist
  für Screenreader ausgeblendet.
- **Veröffentlichen in der Kopfzeile (#289):** die Karte „Website veröffentlichen“
  entfällt. Zwei Icon-Buttons (Branch für dev, Rakete für prod) sitzen für
  `app_admin` in der Topbar-Aktionsgruppe und öffnen unverändert den Dry-Run-Dialog.
- **Bestands-Editor (#289):** im Tab „Bestand“ stehen pro Lagerstätte Label,
  Plus-Button, Nummernfeld und Minus-Button untereinander in gleicher Breite; die
  Buttons tragen Plus-/Minus-Symbole mit sprechendem `aria-label`.

### Fixed

- **Cache-Bust für Panel-Module (#289):** Beide Panel-Module werden wieder mit
  `?v=<VERSION>` ausgeliefert, sodass ein Update eine neue Modul-URL ergibt. Die
  statische Route bleibt ohne lange Cache-Zeiten; direkte Frontend-Dateien
  (inklusive des relativ importierten `lit.js`) werden über einen eigenen View mit
  `Cache-Control: no-cache, must-revalidate` ausgeliefert.
- **Ursache:** Browser hielten die feste Modul-URL über das Update hinweg im Cache;
  die Android-App zeigte die neue Oberfläche bereits, weil sie ihren Cache anders
  verwaltet.

## [0.1.9] - 2026-11-02

### Added

- **Listenansicht mit Umschalter (#289):** Produkte lassen sich neben den Kacheln als
  Tabelle mit Bild, Name, Artikelnummer, Kategorie, Preis, Tags, Sichtbarkeit und
  Beständen anzeigen. Der Umschalter („Kacheln“/„Liste“) sitzt in der Filterleiste,
  ist tastaturbedienbar, nutzt `aria-pressed` und merkt sich die Wahl in
  `localStorage`.
- **Veröffentlichen mit Dry-Run-Dialog (#289):** zwei Aktionen „Nach dev
  veröffentlichen“ und „Nach prod veröffentlichen“ (nur `app_admin`) öffnen zuerst den
  Dry-Run mit Zielbranch, Repository, Zusammenfassung, Zählungen für Dateien, Produkte
  und Bilder sowie der Dateiliste (max. 500, Kürzungshinweis). Erst „Jetzt
  veröffentlichen“ schreibt; danach nennen Toast und Dialog Ziel, Commit und
  Deployment-Status. Fehlt das Token, erscheint die deutsche Servermeldung mit Hinweis
  auf Einstellungen → Frickeldave Store → Veröffentlichen.
- **Produktdialog mit Tabs (#289):** „Grunddaten“, „Bilder“ und „Bestand“ ersetzen die
  separaten Bild- und Bestandsdialoge. Bilder (max. 10, Reihenfolge, Hauptbild,
  Löschen) werden im Tab verwaltet; beim Anlegen wechselt der Dialog nach dem ersten
  Speichern automatisch in den Bilder-Tab. Der Bestand-Tab zeigt je Lagerstätte eine
  Nummernbox mit Erhöhen/Verringern-Buttons und die Buchungshistorie.
- **Lagerstätten und Veröffentlichung in den Einstellungen (#289):** eigene Tabs im
  Konfigurationspanel pflegen Namen, Reihenfolge und Löschung der Lagerstätten
  (Löschschutz mit deutscher Meldung) sowie Repository, dev-/prod-Branch,
  prod-Workflow und ein write-only GitHub-Token mit Statusanzeige.
- **KI-Bedienung (#289):** optionales Feld „Eigene Hinweise“ (≤2000 Zeichen) für den
  Beschreibungsvorschlag, vorgeschlagene Tags als übernehmbare Chips und ein klarer
  Hinweis für verworfene Tags („Diese Tags gibt es noch nicht …“). Übernommen werden
  ausschließlich vorhandene Tags.

- **Veröffentlichen nach dev und prod (#289):** `POST /api/frickeldave_store/publish`
  committet den Export über die GitHub-Daten-API auf den Integrationsbranch `dev`
  (löst `fd-ci` und `fd-deploy-dev` aus) oder dispatcht zusätzlich `fd-deploy-prd`,
  der als einziger Weg `main` aktualisiert. GitHub-Token und Repository liegen in den
  Einstellungen, das Token wird nie zurückgegeben oder geloggt.
- **Dry-Run (#289):** derselbe Endpunkt liefert mit `dry_run: true` einen vollständigen
  Diff je Datei (`added`/`changed`/`removed`/`unchanged`), Zählungen für Produkte und
  Bilder, Zielbranch, Deployment-Hinweis und eine deutsche Zusammenfassung – ohne
  Commit, Workflow-Dispatch oder Store-Schreibzugriff.
- **Konfigurierbare Lagerstätten (#289):** Bestände werden je Lagerstätte geführt
  (Default „Lager“ und „Verkaufsschrank“). Umbenennen erhält den Bestand, Löschen ist
  gesperrt, solange dort Stück gebucht sind. `GET /locations` und `PATCH /settings`
  (`locations`) liefern bzw. ändern die Liste; der Website-Export bildet Lagerstätte 1
  weiterhin auf `stock` und Lagerstätte 2 auf `salesCabinet` ab.
- **KI: eigene Anweisungen und Tag-Vorschläge (#289):** der Beschreibungsvorschlag
  akzeptiert optional `instructions`, schlägt Tags ausschließlich aus der vorhandenen
  Tag-Liste vor und meldet verworfene Vorschläge (`rejected_tags`) für den Hinweis
  „Neue Tags musst du selbst anlegen“.

### Changed

- **Bestandsanzeigen folgen der Konfiguration (#289):** Kacheln, Listenansicht,
  Filter und Produktdialog lesen Lagerstätten und Namen aus `GET /locations`; die
  festen Filter „Auf Lager“/„Im Schrank“ und die Felder `stock`/`salesCabinet` im
  Panel entfallen. Die Bestandspflege erfolgt im Produktformular und wird beim
  Speichern persistiert.
- **Store-Schema 2:** Migration von `stock`/`salesCabinet` auf Bestände je Lagerstätte,
  verlustfrei auf die beiden Standard-Lagerstätten; `STORAGE_VERSION` 2.

## [0.1.8] - 2026-10-28

### Added

- **Konfigurierbarer Speicherort (`data_path`) mit Migration:** Der Katalog liegt
  wahlweise unter `config/frickeldave_store` (Standard), `/share/frickeldave_store`
  oder `/media/frickeldave_store`. `share`/`media` sind vom Home-Assistant-Backup
  ausgeschlossen; `config` bleibt der Standard und ist weiterhin im Backup enthalten.
  Beim Wechsel werden Store-Datei, Bilder und Snapshots kopiert, die alte Kopie
  bleibt erhalten; ein belegtes Ziel wird mit `409 conflict` abgelehnt. Freie Pfade
  und fehlende Mounts werden mit einer klaren Meldung abgelehnt.
- **Settings-API:** `GET /settings` liefert `storage` mit `data_path`,
  `effective_path`, `backup_included`, `exclude_from_backup`, `paths` und `choices`;
  `PATCH /settings` akzeptiert `{"storage": {"data_path": ...}}` und meldet
  `reload_required`.
- **Options-Flow:** Auswahlfeld `data_path` als Rückfallebene zum Konfigurationspanel.


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
