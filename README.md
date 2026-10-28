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
| `ai_endpoint` | Vollständige URL der Chat-Completions-API (Standard: DeepSeek) |
| `ai_model` | Modellname, Standard `deepseek-flash` (akzeptiert Bilder) |
| `ai_api_key` | Zugangsschlüssel; wird nie zurückgegeben, geloggt oder exportiert |
| `ai_prompt` | Prompt, Standard „Generiere mir eine schöne Beschreibung des Artikels für meinen Handmade-Shop.“ |
| `export_path` | Exportverzeichnis relativ zu `config/` (Standard `frickeldave_store/export`) |

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
| `app_admin` | Einstellungen, Export, Benutzerfreigaben |

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

## Daten, Speicherort und Backup

| Inhalt | Pfad |
| --- | --- |
| Metadaten (JSON) | `config/frickeldave_store/frickeldave_store.data` |
| Bilder | `config/frickeldave_store/images/<artikelnummer>/<artikelnummer>-NN.jpg` |
| Snapshots vor Migration | `config/frickeldave_store/backups/` |
| Export (konfigurierbar) | `config/frickeldave_store/export/public/data/handmade.json` und `src/assets/handmade/` |

Alle Pfade liegen unter `config/` und sind damit in Home-Assistant-Container-Backups
enthalten. Schreibvorgänge sind atomar (temporäre Datei + `os.replace`), Datei-I/O läuft
im Executor. Details: [docs/backup-restore.md](docs/backup-restore.md).

## Katalogexport

Der Export ist idempotent, enthält ausschließlich nicht archivierte Produkte und schreibt
das Schema von `public/data/handmade.json` (inklusive `stock` und `salesCabinet`).
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

- Zuordnung ausschließlich über die `articleNumber`. Bereits vorhandene Produkte werden
  **nie überschrieben** (Zähler `skipped`), doppelte Artikelnummern im gelieferten
  Dokument gelten als `failed`. Ein Import legt nur neue Produkte an und löscht nichts.
- Übernommen werden alle Felder des Live-Schemas: `name`, `description`, `category`,
  `price`, `size`, `tags`, `visible`, `customizable`, `stock`, `salesCabinet`. Neue
  Produkte erhalten `archived=false`, `schemaVersion=1` sowie `createdAt`/`updatedAt`
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
  "errors": []
}
```

Alle Felder im Anfragekörper sind optional: `source_url`, `image_base_url` und
`import_images` (Standard `true`). Die Standardquellen genügen der Rolle `product`; wer
eigene `source_url` oder `image_base_url` angibt, braucht `app_admin`. Erlaubt sind
ausschließlich `https`-URLs auf `frickeldave.de` und `raw.githubusercontent.com`
(SSRF-Schutz).

## KI-Beschreibungen

Der Vorschlag wird **nie automatisch gespeichert**. Es werden nur Produktdaten und
Produktbilder übertragen; Bilder werden vorher von EXIF/GPS und Textmetadaten befreit.
Kunden-, Finanz- und personenbezogene Daten sind ausgeschlossen. Fehler, Timeouts und
Nutzungslimits werden als lesbare Meldung angezeigt.

## API

Alle Routen liegen unter `/api/frickeldave_store` und benötigen eine HA-Anmeldung.
Fehlerformat: `{"error": {"code": "...", "message": "..."}}`.

| Methode | Pfad |
| --- | --- |
| GET | `/me` |
| GET, POST | `/products` |
| GET, PATCH | `/products/{article_number}` |
| POST | `/products/{article_number}/archive` |
| GET, POST | `/products/{article_number}/images` |
| GET, DELETE | `/products/{article_number}/images/{name}` |
| POST | `/products/{article_number}/images/order` |
| POST | `/products/{article_number}/images/main` |
| GET, POST | `/products/{article_number}/stock` |
| POST | `/products/{article_number}/description/suggest` |
| GET, POST | `/tags` |
| DELETE | `/tags/{name}` |
| GET | `/users` (nur `app_admin`) |
| PUT, DELETE | `/users/{user_id}` (nur `app_admin`) |
| GET, PATCH | `/settings` (nur `app_admin`) |
| POST | `/export` (nur `app_admin`) |
| POST | `/import/catalog` (`product`; mit eigener Quelle nur `app_admin`) |

Filter für `GET /products`: `search`, `category` (Komma = ODER), `tags` (Komma = ODER),
`customizable`, `in_stock`, `in_cabinet`, `visibility=visible|invisible`, `archived`,
`sort=name|price`, `order=asc|desc`, `limit`, `offset`.

`GET /users` liefert `users` (Freigaben mit `user_id`, `name`, `roles`, `role_labels`),
`available_users` (aktive, noch nicht freigegebene HA-Benutzer – nur ID und Name) sowie
`roles` (ID und Label). `PUT /users/{user_id}` erwartet `{"roles": ["product", ...]}`,
`DELETE /users/{user_id}` entfernt die Freigabe; beide antworten mit derselben Struktur
wie `GET /users`. Unbekannte Rollen, leere Rollenlisten und das Entfernen der letzten
`app_admin`-Freigabe werden abgelehnt (`400` bzw. `409`).

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
