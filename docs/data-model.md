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

## Tags

Tags liegen zusätzlich als sortierte Liste im Store (`tags`). Das Löschen eines Tags
entfernt es aus allen Produkten und aktualisiert deren `updatedAt`.

## Filter- und Sortierregeln

- `category` – Komma-Liste, **ODER**-Verknüpfung
- `tags` – Komma-Liste, **UND**-Verknüpfung (case-insensitiv)
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
