# Datenmodell

## Store-Payload (Schema-Version 1)

Datei: `config/frickeldave_store/frickeldave_store.data`
Schlüssel im HA-`Store`: `frickeldave_store.data`, Version `1`, Minor-Version `1`.

```json
{
  "schemaVersion": 1,
  "products": [],
  "tags": [],
  "bookings": [],
  "settings": {
    "ai": {
      "endpoint": "https://api.deepseek.com/chat/completions",
      "model": "deepseek-flash",
      "prompt": "Generiere mir eine schöne Beschreibung ...",
      "api_key": ""
    }
  },
  "updatedAt": "2026-10-27T12:00:00Z"
}
```

Der API-Key liegt ausschließlich serverseitig im Store. Er wird nie über die API
zurückgegeben (`api_key_set: true` genügt), nie geloggt und nie exportiert.

## Produkt

| Feld | Typ | Regel |
| --- | --- | --- |
| `articleNumber` | string | 6 Zeichen, Kategoriepräfix + 4 Zufallszeichen, Primärschlüssel, unveränderlich |
| `name` | string | Pflicht, max. 200 Zeichen |
| `description` | string | optional, max. 20 000 Zeichen |
| `category` | string | `3D`, `EP`, `LA`, `SB`, `WO`; nach dem Anlegen unveränderlich |
| `price` | number | EUR, ≥ 0, zwei Nachkommastellen |
| `size` | string | optional, max. 200 Zeichen |
| `tags` | string[] | frei wählbar, case-insensitiv dedupliziert |
| `customizable` | bool | Standard `true` |
| `visible` | bool | Standard `true` |
| `stock` | int | Lagerbestand, ≥ 0 |
| `salesCabinet` | int | Verkaufsschrank, ≥ 0 |
| `images` | object[] | max. 10, geordnet, genau ein Hauptbild |
| `archived` | bool | Soft-Delete |
| `createdAt`, `updatedAt` | string | ISO-8601 mit `Z` |
| `schemaVersion` | int | derzeit `1` |

Kategoriebedeutung: `3D` 3D-Druck, `EP` Epoxidharz, `LA` Laser, `SB` Steinmasse, `WO` Holz.

### Bilder

```json
{"name": "LAH3K3-01.jpg", "is_main": true}
```

Datei: `config/frickeldave_store/images/<artikelnummer>/<artikelnummer>-NN.jpg`
(`NN` zweistellig, aufsteigend). Erlaubte Uploads: JPEG, PNG, WebP, GIF, max. 10 MB.
Ein elfter Upload wird mit Code `image_limit` und Status `409` abgelehnt; es entsteht
keine Teilpersistenz (Datei wird erst nach der Prüfung geschrieben).

### Bestandsbuchung

| Feld | Bedeutung |
| --- | --- |
| `id` | zufällige Kennung |
| `articleNumber` | betroffenes Produkt |
| `type` | `in` (Zugang), `out` (Abgang), `transfer` (Umlagerung Lager → Schrank), `correction` (Korrektur auf Zielwert) |
| `quantity` | Menge, > 0 |
| `note` | optionaler Text |
| `at` | Zeitstempel |
| `ha_user_id` | auslösender HA-Benutzer (stabile ID) |

Formeln: `in` → `stock += quantity`; `out` → `stock = max(0, stock - quantity)`;
`transfer` → `moved = min(quantity, stock)`, `stock -= moved`, `salesCabinet += moved`;
`correction` → `stock = quantity`.

Einzelstück = Produkt mit Menge 1. Personalisierbare Produkte werden durch Verkäufe
nicht automatisch abgebucht.

## Katalogimport

`POST /api/frickeldave_store/import/catalog` übernimmt den Live-Katalog der Website
(`https://frickeldave.de/data/handmade.json`) und die zugehörigen Bilder in den Store.
Der Import ist eine reine Anlege-Operation: bestehende Produkte bleiben unverändert, es
gibt keinen Hard-Delete-Pfad.

### Feldabbildung

| Katalogfeld | Zielfeld | Regel |
| --- | --- | --- |
| `articleNumber` | `articleNumber` | Primärschlüssel, Großbuchstaben; 1–12 Zeichen `A-Z0-9` |
| `category` | `category` | muss `3D`, `EP`, `LA`, `SB` oder `WO` sein |
| `name` | `name` | Pflicht, max. 200 Zeichen |
| `description` | `description` | optional, max. 20 000 Zeichen |
| `price` | `price` | Zahl ≥ 0, zwei Nachkommastellen |
| `size` | `size` | optional, max. 200 Zeichen |
| `tags` | `tags` | Liste, case-insensitiv dedupliziert, zusätzlich in die Store-Tag-Liste |
| `visible`, `customizable` | dito | boolesch, Standard `true` |
| `stock`, `salesCabinet` | dito | ganze Zahl ≥ 0 |
| – | `archived` | immer `false` |
| – | `schemaVersion` | immer `1` |
| – | `createdAt`, `updatedAt` | Zeitstempel des Imports (ISO-8601, `Z`) |

Panel und Import erzeugen sechsstellige Artikelnummern (Kategoriepräfix + 4 Zeichen). Der
Import akzeptiert zusätzlich kurze Altbestandsnummern wie `LAX`, damit der gewachsene
Katalog vollständig übernommen wird; neu angelegte Produkte bleiben sechsstellig.

### Idempotenz und Zähler

| Zähler | Bedeutung |
| --- | --- |
| `created` | neu angelegte Produkte |
| `skipped` | Artikelnummer existiert bereits (Produkt bleibt unverändert) |
| `failed` | ungültiges Produkt oder doppelte Artikelnummer im Dokument |
| `images_imported` | in diesem Lauf geschriebene Bilddateien neu angelegter Produkte |
| `products_total` | Anzahl der Produkte im gelieferten Dokument |
| `errors` | Liste aus `{"articleNumber", "message"}` |

Ein zweiter Lauf über dasselbe Dokument liefert `created=0` und `skipped=<products_total>`.
Doppelte Artikelnummern innerhalb eines Dokuments werden nur beim ersten Vorkommen
angelegt; alle weiteren zählen als `failed`. Produktfehler sind nicht fatal – nur ein
nicht lesbares oder strukturell ungültiges Dokument (`catalog_invalid`, HTTP 422) oder ein
nicht erreichbarer Katalog (`catalog_unavailable`, HTTP 502) lehnt die Anfrage ab; in
diesem Fall wird der Store überhaupt nicht verändert.

### Bilder

`<image_base_url>/<kategorie>/<artikelnummer>/<artikelnummer>-NN.jpg` mit `NN` = 01…10.
Alle zehn Indizes werden geprüft, je Index zuerst `.jpg`, dann `.jpeg`; fehlt ein Index in
beiden Endungen (HTTP 404), wird der nächste probiert. Dadurch bleiben Lücken erhalten
(im aktuellen Bestand `LAKBCD` mit 01, 03, 05, 08 – alle vier werden übernommen).
Höchstens zehn Bilder pro Produkt, das erste gefundene Bild ist das Hauptbild. Dateien landen unter
`config/frickeldave_store/images/<artikelnummer>/` – identisch zu Panel-Uploads, damit
Export und Panel dieselben Bilder nutzen. Bildfehler (HTTP ≠ 200, Timeout, leerer
Inhalt, fehlende Bildsignatur) werden als `errors`-Eintrag gemeldet, brechen den
Produktimport aber nicht ab. Abrufe laufen mit höchstens fünf gleichzeitigen Anfragen und
30 s Zeitlimit je Anfrage.

Der Import prüft die Dateisignatur (JPEG, PNG, WebP, GIF) statt des Content-Type, weil
GitHub große Binärdateien als `application/octet-stream` ausliefert, und akzeptiert bis
25 MB pro Bild (der Altbestand enthält eine ~12-MB-Datei); Panel-Uploads bleiben bei
10 MB. Pro Produkt fallen damit höchstens 20 Bildanfragen an (zehn Indizes mal zwei
Endungen), was für den einmaligen Import unkritisch ist.

## Tags

Tags liegen zusätzlich als sortierte Liste im Store (`tags`). Das Löschen eines Tags
entfernt es aus allen Produkten und aktualisiert deren `updatedAt`.

## Filter- und Sortierregeln

- `category` – Komma-Liste, **ODER**-Verknüpfung
- `tags` – Komma-Liste, **ODER**-Verknüpfung (case-insensitiv); ein Produkt genügt
  einem der gewählten Tags
- `search` – Teilstring über Name, Beschreibung, Artikelnummer und Tags
- `customizable`, `in_stock` (`stock > 0`), `in_cabinet` (`salesCabinet > 0`),
  `visibility`, `archived` – jeweils **UND**
- Standard: archivierte Produkte werden ausgeblendet (`archived=false`)
- Sortierung `name` oder `price`, `order=asc|desc`, Sekundärschlüssel Artikelnummer
  (deterministisch und stabil)

## Migration und Snapshots

- `SCHEMA_VERSION` in `models.py` = 1, `config_flow.VERSION` = 1, `MINOR_VERSION` = 1.
- Vor einer Migration kopiert der Store die bestehende Datei nach
  `config/frickeldave_store/backups/<zeitstempel>-pre-*.json`.
- `migrate_payload()` ist idempotent; ein höheres gespeichertes Schema wird mit
  `schema_too_new` abgelehnt.
- `async_migrate_entry` sichert zusätzlich die Store-Datei vor der Entry-Migration.
