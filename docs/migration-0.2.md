# Migration auf 0.2.0

**Betroffen:** Installationen, die aus dieser Integration heraus
nach `dev`/`prod` veröffentlicht oder den Katalog von `frickeldave.de`
importiert haben.

**Kurzfassung:** Beides liegt ab 0.2.0 in der zweiten Custom Integration
`frickeldave_store_syncfd`. Die Basis bleibt ohne sie vollständig nutzbar:
Produkte, Bilder, Tags, Lagerstätten, Bestände, Rechte, KI-Vorschläge und der
Website-Export funktionieren unverändert.

## Was sich ändert

| Bereich | 0.1.x | 0.2.0 |
| --- | --- | --- |
| Veröffentlichen | `POST /api/frickeldave_store/publish`, Modul `publish.py` | `POST /api/frickeldave_store/features/publish` über die Erweiterung |
| Katalogimport | `POST /api/frickeldave_store/import/catalog`, Modul `importer.py` | `POST /api/frickeldave_store/features/import` über die Erweiterung |
| Repository, Branches, Workflow | Optionen `github_repository`, `github_dev_branch`, `github_prod_workflow` der Basis | Options-Flow der Erweiterung |
| GitHub-Token | Option `github_token` der Basis, gespiegelt nach `settings.publish.token` | ausschließlich in der Erweiterung |
| Vorgaben für Repository/Quellen | Konstanten in `const.py` der Basis | in der Erweiterung |
| Feature-Status | `publish`/`import` ohne Erweiterung `available: true` | ohne Erweiterung `available: false` mit deutscher Begründung |
| Einstellungen-Panel | Tab „Veröffentlichen“ | entfällt; Hinweis auf die Erweiterung |

Die fachlichen Regeln selbst sind unverändert: `dev` ist der einzige Schreibpunkt,
`prod` committet auf `dev` und startet `fd-deploy-prd.yml`; Dry-Run teilt Code und
Antwortformat; Löschschutz, `allow_empty`, `confirm_removals` und die
Import-Regeln (`overwrite`, Feldübernahme, SSRF-Allowlist) bleiben identisch.

## Was zu tun ist

1. **Basis aktualisieren.** Frickeldave Store auf `0.2.0` heben (HACS → Update)
   und Home Assistant neu starten.
2. **Erweiterung installieren.** In HACS ein zweites benutzerdefiniertes
   Repository `https://github.com/Frickeldave/homeassistant-frickeldave-store-syncfd`
   mit Kategorie **Integration** eintragen, „Frickeldave Store Sync“ installieren
   und Home Assistant neu starten. Reihenfolge: erst die Basis, dann die
   Erweiterung – HACS löst keine Abhängigkeiten zwischen Custom-Integrationen auf.
3. **Erweiterung einrichten.** Einstellungen → Geräte & Dienste → Integration
   hinzufügen → „Frickeldave Store Sync“. Dort Repository, `dev`-Branch,
   Produktions-Workflow und Katalogquelle eintragen. Die Vorgaben entsprechen den
   bisherigen Standardwerten (`Frickeldave/frickeldave.github.io`, `dev`,
   `fd-deploy-prd.yml`).
4. **Neuen GitHub-Token anlegen und hinterlegen.** Fine-grained Token mit
   `Contents: write` und `Actions: write` für das Website-Repository erzeugen und
   im Options-Flow der Erweiterung speichern.
5. **Alten Token widerrufen.** GitHub → Settings → Developer settings →
   Fine-grained tokens → alten Token löschen. Der Token wird bewusst **nicht**
   automatisch übernommen: er ist write-only, bleibt es, und ein frischer Token
   mit minimalen Rechten ist sicherer als ein Migrationspfad, der das Geheimnis
   durch die Basis schleust.
6. **Prüfen.** Im Seitenleisten-Panel erscheinen die Schaltflächen „Nach dev
   veröffentlichen“, „Nach prod veröffentlichen“ und „Katalog importieren“ erst,
   wenn `/me` `features.*.available: true` meldet. Ein Dry-Run
   (`"dry_run": true`) bestätigt die Verbindung ohne Schreibwirkung.

Ohne Schritt 2–4 bleibt das Panel nutzbar; die drei Schaltflächen fehlen
beziehungsweise sind mit Begründung deaktiviert. `POST /features/publish` und
`POST /features/import` antworten dann `409 feature_unavailable` mit:

> Die Synchronisation ist nicht installiert. Installiere Frickeldave Store Sync über HACS.

## Was erhalten bleibt

- **Produkte, Bilder, Tags, Lagerstätten, Bestände und Buchungen** unverändert in
  `frickeldave_store.data` beziehungsweise im Bilderordner.
- **Alle Snapshots** unter `backups/`; das Schema bleibt `schemaVersion: 2`.
- **KI-Einstellungen** (`settings.ai`).
- **Die gespeicherten `settings.publish`-Daten.** Sie werden ab 0.2.0 weder
  gelesen noch ausgeliefert (`GET /settings` enthält kein `publish` mehr,
  `PATCH /settings` lehnt das Feld mit `400` ab) und **nicht gelöscht**. Das ist
  der Rückfallweg: eine 0.1.x-Installation findet ihre Repository-, Branch- und
  Token-Angaben unverändert vor.
- **Der Website-Export** (`POST /export`, CLI) und das Format von
  `public/data/handmade.json` samt der stabilen Abbildung erste Lagerstätte →
  `stock`, zweite → `salesCabinet`.

## Rollback auf 0.1.x

1. Erweiterung deinstallieren (oder nur deaktivieren) und die Basis auf die
   letzte 0.1.x-Version zurücksetzen.
2. Home Assistant neu starten.

Danach greift wieder der eingebaute Pfad: `POST /publish` und
`POST /import/catalog` lesen `settings.publish` wie bisher. Ein Token, das
inzwischen nur in der Erweiterung hinterlegt wurde, muss für den Rollback wieder
im Basis-Options-Flow eingetragen werden – die Erweiterung schreibt bewusst nie
in den Basis-Store.

Ein Rollback ist gefahrlos, weil 0.2.0 keine Daten umschreibt oder entfernt: das
Store-Schema bleibt `schemaVersion: 2`, `settings.publish` bleibt stehen, und das
neue Modul `batch_import.py` ändert nur den Ort des generischen Import-Batches.

## Wenn etwas fehlt

- **Schaltflächen fehlen:** Erweiterung nicht installiert oder nicht eingerichtet
  (`features.*.reason` nennt den Grund). `POST /features/publish` liefert in
  diesem Fall `409 feature_unavailable`.
- **`provider` ist leer:** Es ist keine Erweiterung registriert; `provider` nennt
  sonst die HA-Domain der Erweiterung (`frickeldave_store_syncfd`).
- **`contract_mismatch`:** Die Basis ist älter als die Erweiterung erwartet.
  Zuerst die Basis aktualisieren.
