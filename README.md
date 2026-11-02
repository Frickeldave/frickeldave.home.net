# Frickeldave Store

![CI](https://github.com/Frickeldave/frickeldave.home.net/actions/workflows/ci.yml/badge.svg)

Home-Assistant-Custom-Integration für die Verwaltung des Frickeldave-Handmade-Katalogs:
Produkte, Bilder, Tags, Bestände, Berechtigungen und KI-Beschreibungsvorschläge – alles
in einem deutschen Panel in Home Assistant. Der Katalogexport versorgt die Website,
der Katalogimport übernimmt den bestehenden Bestand zurück in die Verwaltung.

Die Integration läuft mit **Home Assistant Container** – ohne Supervisor, Add-on oder
zusätzlichen Anwendungscontainer. Es gibt keine `hassio`- oder Supervisor-Aufrufe.

> Repository-Hinweis: Die ursprüngliche Spezifikation nennt `Frickeldave/frickeldave-store`.
> Auf ausdrückliche Anweisung liegt die Integration in **`Frickeldave/frickeldave.home.net`**.
> Alle URLs in Manifest, HACS-Metadaten und Dokumentation verweisen auf dieses Repository.

## Voraussetzungen

- Home Assistant ab **2024.6** (getestet mit 2025.1)
- HACS (für die Installation als benutzerdefiniertes Repository)
- Ein Home-Assistant-Benutzerkonto für jede Person, die das Panel nutzt

## Installation über HACS

1. HACS öffnen → **Integrationen** → Drei-Punkte-Menü → **Benutzerdefinierte Repositories**.
2. Repository `https://github.com/Frickeldave/frickeldave.home.net` eintragen,
   Kategorie **Integration** wählen.
3. „Frickeldave Store“ installieren.
4. Home Assistant neu starten.

Manuelle Installation ohne HACS: Ordner `custom_components/frickeldave_store/` nach
`config/custom_components/` kopieren und Home Assistant neu starten.

## Einrichtung

**Einstellungen → Geräte & Dienste → Integration hinzufügen → „Frickeldave Store“.**

Im Einrichtungsdialog werden die ersten freigegebenen Benutzer eingetragen – eine Zeile
pro Home-Assistant-Benutzer-ID:

```
<ha_user_id>: product,app_admin
<ha_user_id>: product
```

Die stabile HA-Benutzer-ID findet man in Home Assistant unter **Einstellungen → Personen
→ Person → Erweiterte Einstellungen** (bzw. in `.storage/auth`). Ohne Eintrag gilt
**Default Deny**: Die Person sieht das Panel, erhält aber auf allen Datenrouten `403`.

### Optionen

| Option | Bedeutung |
| --- | --- |
| `users` | Freigaben im Format `<ha_user_id>: role,role` |
| `data_path` | Datenordner: `config` (Standard, im Backup), `share` oder `media` (vom Backup ausgeschlossen) |
| `ai_endpoint` | Vollständige URL der Chat-Completions-API (Standard: DeepSeek) |
| `ai_model` | Modellname, Standard `deepseek-flash` (akzeptiert Bilder) |
| `ai_api_key` | Zugangsschlüssel; wird nie zurückgegeben, geloggt oder exportiert |
| `ai_prompt` | Prompt, Standard „Generiere mir eine schöne Beschreibung des Artikels für meinen Handmade-Shop.“ |
| `export_path` | Exportverzeichnis relativ zu `config/` (Standard `frickeldave_store/export`) |
| `stock_locations` | Lagerstätten, eine pro Zeile (Standard: `Lager` und `Verkaufsschrank`); Zeile 1 → `stock`, Zeile 2 → `salesCabinet` im Website-Export |
| `github_repository` | Website-Repository, Standard `Frickeldave/frickeldave.github.io` |
| `github_dev_branch` | Integrationsbranch, Standard `dev` |
| `github_prod_workflow` | Produktions-Workflow, Standard `fd-deploy-prd.yml` |
| `github_token` | Fine-grained Token mit `Contents: write` und `Actions: write`; wird nie zurückgegeben, geloggt oder exportiert |

YAML-Konfiguration wird nicht unterstützt (`config_entry_only_config_schema`).

### Benutzerverwaltung

Das Zahnrad am Eintrag **Einstellungen → Geräte & Dienste → Frickeldave Store** öffnet
das tabbed Konfigurationspanel (`/frickeldave-store-config?config_entry=<id>`) mit den
Tabs **Benutzerverwaltung** und **KI-Einstellungen**. Dort lassen sich Freigaben bequem
über echte HA-Benutzernamen anlegen, bearbeiten und löschen; die Rollenauswahl nutzt die
Labels `Produktpflege`, `Finanzen` und `Anwendungsverwaltung`.

- **Hinzufügen:** In der Benutzerverwaltung eine noch nicht freigegebene aktive
  HA-Person wählen und mindestens eine Rolle vergeben.
- **Bearbeiten:** Rollen einer bestehenden Freigabe ändern. Unbekannte Benutzer-IDs
  (z. B. nach dem Löschen eines HA-Kontos) bleiben als Eintrag ohne Namen sichtbar und
  können weiter bearbeitet oder entfernt werden.
- **Löschen:** Freigabe entziehen; der Zugriff endet mit der nächsten Anfrage.
- **Schutz:** Die letzte Freigabe mit der Rolle `app_admin` kann weder gelöscht noch
  herabgestuft werden (`409 conflict`).

Der Options-Flow bleibt als Rückfallebene erhalten. Die Option `users` bleibt die einzige
Quelle der Wahrheit: Die Oberfläche schreibt Änderungen dorthin und wendet sie sofort an,
sodass ein Rechteentzug ohne Neustart greift.

## Rollen und Rechte

| Rolle | Umfang |
| --- | --- |
| `product` | Produkte, Bilder, Tags, Bestände, Sichtbarkeit, Archivierung, KI-Vorschläge |
| `finance` | spätere Finanzfunktionen (in dieser Ausbaustufe keine Endpunkte) |
| `app_admin` | Einstellungen, Export, Veröffentlichen (dev/prod), Benutzerfreigaben |

Rollen sind kombinierbar. Finanzrechte folgen **nie** implizit aus `product` oder
`app_admin`. Jede geschützte HTTP- und WebSocket-Route prüft serverseitig pro Anfrage;
ein Rechteentzug greift beim nächsten Zugriff, auch bei geöffnetem Panel.
Details: [docs/permissions.md](docs/permissions.md).

## Bedienung

Nach der Einrichtung erscheint **Frickeldave Store** mit dem Icon `mdi:store-cog` in der
Seitenleiste. Beide Panels werden als `panel_custom`-Web-Components ohne iframe geladen:
das Seitenleisten-Panel (`.../frickeldave-store-panel.js`) für die Katalogarbeit und das
Konfigurationspanel (`.../frickeldave-store-config-panel.js`, `require_admin`) am Zahnrad
des Integrationseintrags. Einstellungen liegen damit nicht mehr im Drei-Punkte-Menü des
Seitenleisten-Panels. Die JavaScript-Dateien liegen unter
`custom_components/frickeldave_store/frontend/` und werden vom Frontend-Team gepflegt;
fehlt eine Datei, bleiben Integration und API vollständig funktionsfähig.

Der Bilder-Tab im Produktdialog zeigt je Bild Maße und Dateigröße, warnt vor zu
großen Bildern und fasst sie über „Bilder optimieren“ zusammen. Produkte mit
mindestens einem zu großen Bild tragen in Liste und Kachel ein Warndreieck, das
den Dialog direkt im Bilder-Tab öffnet.

## Daten, Speicherort und Backup

Der Datenordner ist über `data_path` wählbar: `config` (Standard),
`share` oder `media`. Freie Pfade werden abgelehnt; fehlt der Mount, schlägt
die Auswahl mit einer klaren Meldung fehl.

| Inhalt | Pfad bei `data_path = config` |
| --- | --- |
| Metadaten (JSON) | `config/frickeldave_store/frickeldave_store.data` |
| Bilder | `config/frickeldave_store/images/<artikelnummer>/<artikelnummer>-NN.jpg` |
| Snapshots vor Migration | `config/frickeldave_store/backups/` |
| Export (konfigurierbar) | `config/frickeldave_store/export/public/data/handmade.json` und `src/assets/handmade/` |

Bei `share` bzw. `media` liegt derselbe Aufbau unter
`/share/frickeldave_store/` bzw. `/media/frickeldave_store/`.

**Backup:** Ein HA-Backup mit „Home Assistant settings“ archiviert das gesamte
Config-Verzeichnis und damit `config/frickeldave_store` automatisch. Eine Ausnahme
für einzelne Unterordner ist in HA nicht möglich. Wer den Katalog nicht im
HA-Backup haben will, wählt `share` oder `media` und sichert den Ordner selbst
(Docker: Volume für `/share` bzw. `/media` einbinden). Der Exportpfad ist davon
unabhängig und kann ebenfalls außerhalb von `config` liegen.

Im Konfigurationspanel (Tab **Allgemeine Einstellungen**) zeigt ein Schalter den
aus `data_path` abgeleiteten Backup-Status und erlaubt den Wechsel des
Speicherorts; nach einem Wechsel verlangt die Integration ein Neuladen.

Beim Wechsel werden Store-Datei, Bilder und Snapshots kopiert; der alte Ordner
bleibt als Sicherheitskopie erhalten. Ist der Zielordner bereits belegt, bricht
der Wechsel mit `409 conflict` ab. Schreibvorgänge sind atomar (temporäre Datei +
`os.replace`), Datei-I/O läuft im Executor.
Details: [docs/backup-restore.md](docs/backup-restore.md).

## Lagerstätten und Bestände

Bestände werden **je Lagerstätte** geführt. Standard sind `Lager` und
`Verkaufsschrank`; damit bleibt das bisherige Verhalten unverändert. Die Administration
pflegt die Liste an zwei Stellen, die dieselbe Quelle nutzen:

- **Konfigurationspanel** (Zahnrad am Integrationseintrag, `PATCH /settings`)
- **Options-Flow** (Einstellungen → Geräte & Dienste → Frickeldave Store → Konfigurieren),
  eine Zeile pro Lagerstätte

Regeln: mindestens eine Lagerstätte, eindeutige Namen, keine leeren Namen, maximal 40
Zeichen und 20 Einträge. **Umbenennen erhält den Bestand** (die interne ID bleibt
stabil). Das **Löschen** einer Lagerstätte mit Bestand wird mit `409 conflict`
abgelehnt und nennt Produkte und Stückzahl; erst umbuchen, dann löschen.

Buchungen (`POST /products/{article_number}/stock`) tragen `locationId`; eine
Umlagerung zusätzlich `targetLocationId`. Ohne Angabe gilt die erste Lagerstätte als
Quelle und die zweite als Ziel. `GET /locations` liefert `id`, `name`, `position`,
`stock_total` und `products_with_stock` für alle mit der Rolle `product`.

Beim Laden migriert die Integration Bestände der Schema-Version 1 verlustfrei:
`stock` → erste Lagerstätte, `salesCabinet` → zweite. Vorher wird ein Snapshot unter
`backups/pre-load-v1-*.json` abgelegt.

## Katalogexport

Der Export ist idempotent, enthält ausschließlich nicht archivierte Produkte und schreibt
das Schema von `public/data/handmade.json`. Weil die Website nur zwei Zahlen kennt,
bildet der Export die **erste** Lagerstätte auf `stock` und die **zweite** auf
`salesCabinet` ab; ohne zweite Lagerstätte bleibt `salesCabinet` bei `0`. Diese
Abbildung ist unabhängig von den Namen und bleibt stabil, solange die Reihenfolge bleibt.
Artikelnummer bleibt der Schlüssel; Bilder werden als
`src/assets/handmade/<kategorie>/<artikelnummer>/<artikelnummer>-NN.jpg` abgelegt, das
Hauptbild zuerst.

CLI (ohne laufendes Home Assistant):

```bash
python -m custom_components.frickeldave_store.export \
  config/frickeldave_store/frickeldave_store.data ./export
```

## Katalogimport

Der Import übernimmt den bestehenden Website-Katalog
(`https://frickeldave.de/data/handmade.json`) samt Bildern in den Store. Er ist für die
Übernahme des Altbestands gedacht und bleibt gefahrlos wiederholbar:

- Zuordnung ausschließlich über die `articleNumber`. Ohne `overwrite` werden bereits
  vorhandene Produkte **nie überschrieben** (Zähler `skipped`), doppelte Artikelnummern
  im gelieferten Dokument gelten als `failed`. Ein Import löscht nichts (kein
  Hard-Delete-Pfad).
- `dry_run: true` lädt den Katalog und liefert eine vollständige Vorschau, schreibt aber
  nichts: kein Store-Schreibzugriff, keine Bilddatei, kein Snapshot, kein `updatedAt`.
  Der Plan trennt `would_create`, `would_overwrite` und `unchanged`.
- `overwrite: true` überschreibt vorhandene, nicht archivierte Produkte mit dem
  Katalogstand (Zähler `overwritten`). Übernommen werden `name`, `description`, `price`,
  `size`, `tags`, `visible`, `customizable` und `stock`/`salesCabinet`. Lokal bleiben
  Artikelnummer, `createdAt`, Bilder, Buchungen und der Archiv-Status; die Kategorie
  bleibt ebenfalls lokal, weil die Artikelnummer sie kodiert. Archivierte Produkte
  werden nie reaktiviert, sondern als `skipped` gemeldet. Vorhandene Bilder werden
  nicht überschrieben, nur fehlende ergänzt. Der Bestand wird auf den Katalogstand
  gesetzt; die Buchungshistorie bleibt dabei erhalten, sodass sie vom neuen Bestand
  abweichen kann.
- Übernommen werden alle Felder des Live-Schemas: `name`, `description`, `category`,
  `price`, `size`, `tags`, `visible`, `customizable`, `stock`, `salesCabinet`. Neue
  Produkte erhalten `archived=false`, `schemaVersion=2` sowie `createdAt`/`updatedAt`
  des Imports.
- Bilder werden aus dem Website-Repository geladen
  (`<image_base_url>/<kategorie>/<artikelnummer>/<artikelnummer>-NN.jpg`, `NN` = 01…10,
  zuerst `.jpg`, dann `.jpeg`) und im selben Layout wie Panel-Uploads abgelegt
  (`config/frickeldave_store/images/<artikelnummer>/`); das erste gefundene Bild ist
  das Hauptbild. Geprüft werden alle Indizes 01…10, Lücken (z. B. 01, 03, 05, 08) werden
  übersprungen; höchstens zehn Bilder je Produkt.
- Bildfehler brechen den Produktimport nicht ab, sondern erscheinen in `errors`. Nur ein
  nicht lesbares oder strukturell ungültiges Katalogdokument lehnt die Anfrage
  vollständig ab – dann bleibt der Store unverändert.
- Bildabrufe laufen mit höchstens fünf parallelen Anfragen und je 30 s Zeitlimit; es
  wird nie im Event-Loop blockierend gelesen oder geschrieben. Geprüft wird die
  Dateisignatur (JPEG, PNG, WebP, GIF), nicht der Content-Type – GitHub liefert große
  Binärdateien als `application/octet-stream`. Der Import akzeptiert bis 25 MB pro Bild,
  weil der Altbestand eine ~12-MB-Datei enthält; Panel-Uploads bleiben bei 10 MB.

```bash
curl -sS -X POST http://homeassistant.local:8123/api/frickeldave_store/import/catalog \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{}'
```

Beispielantwort für den aktuellen Bestand (63 Produkte):

```json
{
  "created": 63,
  "skipped": 0,
  "failed": 0,
  "images_imported": 170,
  "products_total": 63,
  "errors": [],
  "dry_run": false,
  "would_create": 0,
  "would_overwrite": 0,
  "overwritten": 0,
  "unchanged": 0,
  "summary": "Import abgeschlossen: 63 neu angelegt, 0 überschrieben, 0 unverändert, 0 fehlerhaft, 170 Bilder importiert."
}
```

Vorschau desselben Laufs:

```json
{
  "created": 0,
  "skipped": 58,
  "failed": 0,
  "images_imported": 4,
  "products_total": 63,
  "errors": [],
  "dry_run": true,
  "would_create": 2,
  "would_overwrite": 3,
  "overwritten": 0,
  "unchanged": 58,
  "summary": "Vorschau: 63 Produkte im Katalog, 2 neu, 3 zu überschreiben, 58 unverändert, 0 fehlerhaft, 4 Bilder würden importiert. Es wurde nichts geschrieben."
}
```

Alle Felder im Anfragekörper sind optional: `source_url`, `image_base_url`,
`import_images` (Standard `true`), `dry_run` (Standard `false`) und `overwrite`
(Standard `false`). Nicht-boolesche Werte für `dry_run`/`overwrite` lehnt der Endpunkt
mit einer deutschen `validation_error` ab. Die Standardquellen genügen der Rolle
`product`; wer eigene `source_url` oder `image_base_url` angibt, braucht `app_admin`.
Auch `overwrite: true` bleibt bei `product`, weil es ein Sammel-Update auf Daten ist,
die diese Rolle einzeln ändern darf; der Store legt vor dem ersten Schreiben einen
Snapshot an. Erlaubt sind ausschließlich `https`-URLs auf `frickeldave.de` und
`raw.githubusercontent.com` (SSRF-Schutz).

## Veröffentlichen nach dev und prod

`POST /publish` (`app_admin`) überträgt den Export über die GitHub-Daten-API in das
Website-Repository und löst das passende Deployment aus:

| Ziel | Ablauf |
| --- | --- |
| `dev` | Commit auf den Integrationsbranch `dev` → `fd-ci` → `fd-deploy-dev` (Pre-Release `dev-v<version>`) |
| `prod` | Commit auf `dev`, danach `workflow_dispatch` von `fd-deploy-prd` – der einzige Weg, `main` zu aktualisieren (Merge `dev` → `main`, Pages-Deploy, stabiles Release) |

Ein direkter Commit auf `main` ist im Website-Repository unzulässig und wird von der
Integration nie versucht. Ist der Stand bereits identisch, entsteht kein zweiter Commit;
`prod` dispatcht dennoch, damit ein liegengebliebener Stand promotet werden kann.

Mit `"dry_run": true` liefert derselbe Endpunkt einen vollständigen Plan je Datei
(`added`/`changed`/`removed`/`unchanged`), Zählungen für Produkte und Bilder,
Zielbranch, Deployment-Hinweis und eine deutsche Zusammenfassung. Der Dry-Run liest
ausschließlich: kein Commit, kein Workflow-Dispatch, kein Store-Schreibzugriff.

Jede Antwort enthält `warning` (`string|null`). Ist der lokale Store leer (oder
enthält nur archivierte Produkte), würde eine Veröffentlichung den kompletten
Website-Katalog löschen. Deshalb:

| Fall | Verhalten |
| --- | --- |
| `dry_run: true`, leerer Store | `200` mit vollständiger Vorschau, `warning` gesetzt, Hinweis in `summary`; es wird nichts geschrieben |
| Veröffentlichen, leerer Store | `409 publish_empty_catalog`, kein Commit |
| Veröffentlichen mit `"allow_empty": true` | Commit wie angefordert (Katalog wird geleert), `warning` bleibt gesetzt |

`allow_empty` ist ein boolescher Notausgang (Standard `false`) und nur über
`POST /publish` mit `app_admin` erreichbar. Die Oberfläche muss ihn bewusst
bestätigen lassen. Fehlt eine Bilddatei oder ist sie nicht lesbar, antwortet der
Endpunkt mit `409 publish_artifacts_unavailable` und nennt Artikelnummer und
Datei, statt still Bilder aus dem Katalog zu entfernen.

### Löschschutz

Eine Veröffentlichung entfernt nur, was diese Integration selbst verantwortet:
`public/data/handmade.json` und Produktbilder im Muster
`src/assets/handmade/<Kategorie>/<Artikelnummer>/<Artikelnummer>-NN.<Endung>`
(Kategorien `3D`, `EP`, `LA`, `SB`, `WO`). Alles andere unter
`src/assets/handmade` – etwa die 3D-Modellquellen unter `3dmodels/**` oder eine
`.gitkeep` – gehört der Website und wird nie als entfernt gemeldet.

Zusätzlich gilt ein Löschschutz: würde ein Lauf mehr als 10 Bilder oder mehr als
ein Drittel des bestehenden Bildbestands entfernen (und mindestens 3), bricht er
mit `409 publish_removals_blocked` und deutscher Meldung ab. Im Dry-Run erscheint
dieselbe Meldung als Warnung. Übergehen lässt sich der Schutz nur mit
`"confirm_removals": true` (boolesch, Standard `false`, `app_admin`); die
Bestätigung gilt für genau einen Lauf. Die Antwort enthält dazu
`removal_guard` (`triggered`, `removed`, `existing`, `maxFiles`, `share`,
`confirmed`).

Bilddateinamen bleiben stabil: das Hauptbild heißt `<Artikelnummer>-01`, alle
übrigen Bilder behalten ihre eigene Nummer aus dem Store, solange sie frei ist.
Ein Löschen oder Hinzufügen im Store benennt daher nicht die übrigen Bilder um.

Fehlt das Token, antwortet der Endpunkt mit `400 publish_token_missing` und einer
deutschen Meldung. Ein abgelehntes Token ergibt `502 github_unauthorized`, ein
unbekanntes Repository `404 github_not_found`, ein währenddessen bewegter Branch
`409 github_conflict`, eine nicht-JSON-Antwort von GitHub (Störungsseite)
`502 github_unavailable`. Unerwartete Serverfehler werden mit vollständigem
Traceback im HA-Protokoll geloggt und als `500 internal_error` mit deutscher
Meldung beantwortet.

## KI-Beschreibungen

Der Vorschlag wird **nie automatisch gespeichert**. Es werden nur Produktdaten und
Produktbilder übertragen; Bilder werden vorher von EXIF/GPS und Textmetadaten befreit.
Kunden-, Finanz- und personenbezogene Daten sind ausgeschlossen. Fehler, Timeouts und
Nutzungslimits werden als lesbare Meldung angezeigt.

`POST /products/{article_number}/description/suggest` akzeptiert optional
`{"instructions": "..."}` (max. 2000 Zeichen); der Text wird als zusätzlicher Abschnitt
an den konfigurierten Basis-Prompt gehängt. Ohne das Feld bleibt das Verhalten
unverändert. Die Antwort enthält neben `suggestion` die Tag-Vorschläge:

```json
{
  "suggestion": "…",
  "tags": ["Holz"],
  "rejected_tags": ["Rakete"],
  "available_tags": ["Deko", "Holz"],
  "instructions_used": true
}
```

Serverseitig werden ausschließlich **vorhandene** Tags durchgelassen (Vergleich ohne
Groß-/Kleinschreibung, Ausgabe in der gespeicherten Schreibweise). Neue Tags legt nur der
Nutzer über `POST /tags` an; verworfene Vorschläge stehen in `rejected_tags`, damit die
Oberfläche auf „Neue Tags musst du selbst anlegen“ hinweisen kann.

## API

Alle Routen liegen unter `/api/frickeldave_store` und benötigen eine HA-Anmeldung.
Fehlerformat: `{"error": {"code": "...", "message": "..."}}`.

| Methode | Pfad |
| --- | --- |
| GET | `/me` |
| GET, POST | `/products` |
| GET | `/products/article-number` (`product`; Vorschlag, nicht reserviert) |
| GET, PATCH | `/products/{article_number}` |
| POST | `/products/{article_number}/archive` |
| GET, POST | `/products/{article_number}/images` |
| GET, DELETE | `/products/{article_number}/images/{name}` |
| POST | `/products/{article_number}/images/order` |
| POST | `/products/{article_number}/images/main` |
| POST | `/products/{article_number}/images/optimize` (`product`; Bestandsbilder verkleinern) |
| GET, POST | `/products/{article_number}/stock` |
| POST | `/products/{article_number}/description/suggest` |
| GET, POST | `/tags` |
| DELETE | `/tags/{name}` |
| GET | `/users` (nur `app_admin`) |
| PUT, DELETE | `/users/{user_id}` (nur `app_admin`) |
| GET, PATCH | `/settings` (nur `app_admin`) |
| GET | `/locations` (`product`) |
| POST | `/publish` (nur `app_admin`; `dry_run` möglich) |
| POST | `/export` (nur `app_admin`) |
| POST | `/import/catalog` (`product`; mit eigener Quelle nur `app_admin`) |
| POST | `/features/{feature}` (Erweiterungs-Fähigkeit; ohne Erweiterung `publish`/`import`) |

`GET /me` und `GET /settings` liefern zusätzlich `features` (Verfügbarkeit je Fähigkeit
mit `available`, `reason`, `provider`, `contract_version`); `/settings` enthält für
`app_admin` außerdem `feature_details` mit Titel, benötigter Rolle und
`provides_panel_routes`. Die zweite Custom Integration `frickeldave_store_sync` nutzt
`POST /features/publish` und `/features/import`. Der öffentliche Erweiterungsvertrag
(Contract 1) ist in [`docs/extension-api.md`](docs/extension-api.md) beschrieben.

Filter für `GET /products`: `search`, `category` (Komma = ODER), `tags` (Komma = ODER),
`customizable`, `in_stock`/`in_cabinet` (erste/zweite Lagerstätte), `location` +
`in_location` (beliebige Lagerstätte), `visibility=visible|invisible`, `archived`,
`sort=name|price|category|stock|status`, `order=asc|desc`, `limit`, `offset`.

Sortierung: `category` ordnet nach dem sichtbaren Kategorie-Label, `stock` nach dem
Gesamtbestand über alle Lagerstätten (unabhängig von `location`/`in_location`),
`status` nach aktiv vor archiviert und sichtbar vor unsichtbar. Bei Gleichstand gilt
Name, dann Artikelnummer – auch bei `order=desc`, das nur das Hauptkriterium dreht.

`GET /products/article-number?category=LA` liefert
`{"articleNumber": "LAH3K3", "category": "LA", "reserved": false}` für den
Anlegen-Dialog (Recht `product`). Der Vorschlag wird nicht reserviert; beim Anlegen
erzeugt `POST /products` die Nummer erneut und gibt die tatsächliche zurück.

`GET /settings` liefert neben `ai`, `publish` (ohne Token, nur `token_set`), `users`,
`export_path` und `locations` den Abschnitt `storage` mit `data_path`,
`effective_path`, `backup_included`, `exclude_from_backup`, `paths` (Metadaten, Bilder,
Snapshots) und `choices`. `PATCH /settings` akzeptiert `{"ai": {...}}`,
`{"publish": {"repository|dev_branch|prod_branch|prod_workflow|token": ...}}`,
`{"locations": [{"id"?: "...", "name": "..."}]}` und `{"storage": {"data_path":
"config|share|media"}}`; nach einer erfolgreichen Migration enthält die Antwort
`"reload_required": true`. Ungültige Werte oder unbekannte Felder → `400`, ein
belegtes Ziel oder eine Lagerstätte mit Bestand → `409`.

`GET /users` liefert `users` (Freigaben mit `user_id`, `name`, `roles`, `role_labels`),
`available_users` (aktive, noch nicht freigegebene HA-Benutzer – nur ID und Name) sowie
`roles` (ID und Label). `PUT /users/{user_id}` erwartet `{"roles": ["product", ...]}`,
`DELETE /users/{user_id}` entfernt die Freigabe; beide antworten mit derselben Struktur
wie `GET /users`. Unbekannte Rollen, leere Rollenlisten und das Entfernen der letzten
`app_admin`-Freigabe werden abgelehnt (`400` bzw. `409`).

## Bilder: Upload, Verkleinerung und Metadaten

`POST /products/{article_number}/images` (multipart, Feld `file`) nimmt JPEG, PNG,
WebP und GIF bis 10 MB an und **verkleinert automatisch**:

| Ausgangsbild | Ergebnis |
| --- | --- |
| Lange Kante > 1600 px | auf 1600 px verkleinert (nie hochskaliert) |
| JPEG | neu als JPEG, Qualität 82, progressiv, ohne EXIF/GPS/Kommentare |
| PNG/WebP **mit** Transparenz | WebP, Qualität 85, Alpha bleibt erhalten |
| PNG/WebP **ohne** Transparenz | JPEG, Qualität 82 |
| GIF (auch animiert) und animiertes WebP | unverändert durchgereicht |
| Bereits passend (≤ 1600 px **und** ≤ 400 KiB) | byteweise übernommen, kein Re-Encoding |

Die Dateiendung folgt dem Ergebnis: aus `LA0001-03.png` mit Transparenz wird
`LA0001-03.webp`. Defekte Dateien werden mit `400 image_invalid` und deutscher
Meldung abgelehnt; fehlt Pillow, antwortet die Route mit `pillow_missing`.

Die Antwort enthält zusätzlich zu `images`:

```json
{
  "original": {"bytes": 12582912, "width": 4032, "height": 3024, "content_type": "image/jpeg"},
  "stored": {"bytes": 285000, "width": 1600, "height": 1200, "content_type": "image/jpeg"},
  "resized": true,
  "optimize_message": "Bild von 12,0 MB auf 278 KB verkleinert (4032×3024 → 1600×1200)."
}
```

`GET /products/{article_number}/images`, die Produktliste und das Produktdetail
liefern je Bild `bytes`, `width`, `height`, `content_type` und `oversized`
(`true`, wenn die lange Kante > 1600 px **oder** die Datei > 400 KiB ist). Die
Werte werden beim Lesen aus den Dateien ermittelt und nicht im Store gespeichert.

`POST /products/{article_number}/images/optimize` verkleinert alle zu großen
Bilder eines Produkts nach denselben Regeln, behält Reihenfolge und Hauptbild und
ist idempotent (zweiter Aufruf: `resized: false`). Vor dem ersten Überschreiben
entsteht ein Snapshot der Metadaten (`backups/*-pre-image-optimize.json`).

```json
{
  "results": [
    {"name": "LA0001-01.webp", "previous_name": "LA0001-01.png",
     "before": {"bytes": 2400000, "width": 3000, "height": 2000, "content_type": "image/png"},
     "after": {"bytes": 210000, "width": 1600, "height": 1067, "content_type": "image/webp"},
     "resized": true, "message": "Bild von 2,3 MB auf 205 KB verkleinert (3000×2000 → 1600×1067).",
     "error": null}
  ],
  "summary": {"count": 1, "bytes_before": 2400000, "bytes_after": 210000, "saved_percent": 91.2},
  "images": [{"name": "LA0001-01.webp", "width": 1600, "height": 1067, "bytes": 210000,
              "content_type": "image/webp", "oversized": false}]
}
```

Fehler einzelner Bilder brechen den Lauf nicht ab; sie erscheinen mit `error`
und `resized: false`, die übrigen Bilder werden weiter verarbeitet.

WebSocket-Kommandos: `frickeldave_store/me`, `frickeldave_store/products`,
`frickeldave_store/subscribe`.

## Entwicklung

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install ruff mypy pytest homeassistant pytest-homeassistant-custom-component aiohttp
ruff check .
ruff format --check .
mypy custom_components
pytest -q
python -m json.tool custom_components/frickeldave_store/manifest.json

# Panel-Frontend (Lit-Bundle)
npm ci
npm run build:frontend
node --check custom_components/frickeldave_store/frontend/frickeldave-store-panel.js
node --check custom_components/frickeldave_store/frontend/lit.js
```

`custom_components/frickeldave_store/frontend/lit.js` ist ein vendored Build von
Lit 3.3.3. Home Assistant liefert keine Import-Map für den Spezifizierer `lit`,
deshalb importiert das Panel `./lit.js`; das Bundle wird nie von Hand bearbeitet.
Details: [frontend/README.md](custom_components/frickeldave_store/frontend/README.md).

Dokumentation: [Architektur](docs/architecture.md), [Datenmodell](docs/data-model.md),
[Berechtigungen](docs/permissions.md), [Backup & Restore](docs/backup-restore.md).

## Roadmap / ausdrückliche Nicht-Ziele dieser Ausbaustufe

Nicht enthalten und bewusst auf später verschoben:

- PayPal-Synchronisation (Artikel und Kunden)
- vollständige Kundenverwaltung (CRUD, Rechnungs-/Lieferadresse)
- Website-Publish-Workflow oder PR-Automation (nur Export und Katalogimport)
- Aufnahme in den offiziellen HACS-Katalog
- Finanzfunktionen (Einkaufskosten, Margen, Steuerrücklagen)

## Lizenz

[MIT](LICENSE) – Copyright (c) 2026 David Koenig
