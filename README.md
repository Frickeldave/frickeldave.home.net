# Frickeldave Store

![CI](https://github.com/Frickeldave/frickeldave.home.net/actions/workflows/ci.yml/badge.svg)

Home-Assistant-Custom-Integration für die Verwaltung des Frickeldave-Handmade-Katalogs:
Produkte, Bilder, Tags, Bestände, Berechtigungen und KI-Beschreibungsvorschläge – alles
in einem deutschen Panel in Home Assistant. Der Katalogexport versorgt die Website.

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
Seitenleiste. Das Panel wird als `panel_custom`-Web-Component ohne iframe geladen
(`/frickeldave_store_frontend/frickeldave-store-panel.js`). Die JavaScript-Datei liegt
unter `custom_components/frickeldave_store/frontend/` und wird vom Frontend-Team gepflegt;
fehlt sie, bleiben Integration und API vollständig funktionsfähig.

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
| GET, PATCH | `/settings` (nur `app_admin`) |
| POST | `/export` (nur `app_admin`) |

Filter für `GET /products`: `search`, `category` (Komma = ODER), `tags` (Komma = UND),
`customizable`, `in_stock`, `in_cabinet`, `visibility=visible|invisible`, `archived`,
`sort=name|price`, `order=asc|desc`, `limit`, `offset`.

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
```

Dokumentation: [Architektur](docs/architecture.md), [Datenmodell](docs/data-model.md),
[Berechtigungen](docs/permissions.md), [Backup & Restore](docs/backup-restore.md).

## Roadmap / ausdrückliche Nicht-Ziele dieser Ausbaustufe

Nicht enthalten und bewusst auf später verschoben:

- PayPal-Synchronisation (Artikel und Kunden)
- vollständige Kundenverwaltung (CRUD, Rechnungs-/Lieferadresse)
- Website-Publish-Workflow oder PR-Automation (nur Export)
- Aufnahme in den offiziellen HACS-Katalog
- produktive Datenmigration des Altbestands
- Finanzfunktionen (Einkaufskosten, Margen, Steuerrücklagen)

## Lizenz

[MIT](LICENSE) – Copyright (c) 2026 David Koenig
