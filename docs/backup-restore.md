# Backup und Wiederherstellung

## Speicherort wählen

Der Datenordner ist über die Option `data_path` konfigurierbar:

| `data_path` | Effektiver Pfad | Im HA-Backup enthalten |
| --- | --- | --- |
| `config` (Standard) | `config/frickeldave_store/` | **ja** |
| `share` | `/share/frickeldave_store/` | nein |
| `media` | `/media/frickeldave_store/` | nein |

`config` bleibt der Standard. Ein Home-Assistant-Backup mit „Home Assistant
settings“ archiviert das gesamte Config-Verzeichnis; Home Assistant erlaubt
dabei **keine** Ausnahme für einzelne Unterordner (`EXCLUDE_FROM_BACKUP` ist
fest verdrahtet). Wer den Katalog vom Backup ausnehmen will, legt ihn deshalb
nach `share` oder `media`.

Unterstützt werden ausschließlich diese drei Werte. Freie Pfade werden
abgelehnt (kein Pfad-Traversal). Vor dem Speichern legt die Integration das
Zielverzeichnis an und prüft die Schreibbarkeit; ein fehlender Mount führt zu
einem klaren Fehler (`400 validation_error`) statt zu einem stillen Fallback.

Im Konfigurationspanel (Tab **Allgemeine**) schaltet die Option **„Von Backup
ausschließen“** zwischen `config` (aus) und `share` (an) um; `media` ist als
alternatives Ziel wählbar. Die Oberfläche zeigt dabei die betroffenen Pfade an.

## Was gesichert wird

Der Datenordner enthält immer **Store-Datei, Bilder und Snapshots gemeinsam**:

| Inhalt | Pfad (relativ zum Datenordner) |
| --- | --- |
| Metadaten | `frickeldave_store.data` |
| Bilder | `images/<artikelnummer>/<artikelnummer>-NN.jpg` |
| Snapshots | `backups/` |

Bei `data_path = config` sind das die bisherigen Pfade
`config/frickeldave_store/...`. Eine Sicherung nur der JSON-Datei verliert die
Bildzuordnungen.

Der **Export** ist davon unabhängig: `export_path` ist weiterhin relativ zu
`config/` (Standard `frickeldave_store/export`) und kann ebenfalls vom Backup
ausgenommen werden, indem er z. B. nach `share` oder `media` verschoben wird.

## Backup-Verhalten je Speicherort

**`config` (Standard)**

- Ein HA-Backup („Home Assistant settings“) enthält den Katalog automatisch.
- Zusätzlich empfiehlt sich ein manuelles Backup vor Updates:

  ```bash
  tar czf frickeldave_store_backup_$(date +%F).tar.gz config/frickeldave_store
  ```

**`share` / `media`**

- Der Katalog ist **nicht** im HA-Backup enthalten.
- Docker-Container-Nutzer müssen das Verzeichnis als Volume einbinden, sonst
  schlägt die Auswahl mit einer klaren Meldung fehl:
  - `/share` → `-v /pfad/auf/host:/share`
  - `/media` → `-v /pfad/auf/host:/media`
- Das Verzeichnis in die eigene Backup-Strategie aufnehmen, z. B.:

  ```bash
  tar czf frickeldave_store_backup_$(date +%F).tar.gz /pfad/auf/host/frickeldave_store
  ```

- Die Store-Datei und Bilder werden atomar geschrieben (`os.replace`); ein
  Backup während eines laufenden Schreibvorgangs sieht den alten oder neuen
  Stand, nie eine halbe Datei.

## Wechsel des Speicherorts (Migration)

Beim Ändern von `data_path` (Konfigurationspanel/`PATCH /settings` oder
Options-Flow):

1. Die Integration löst alten und neuen Ordner auf. Sind sie gleich, passiert
   nichts.
2. Enthält der Zielordner bereits eine `frickeldave_store.data` oder ein
   nicht-leeres `images/`, bricht der Wechsel mit `409 conflict` ab. Es wird
   **nichts überschrieben**.
3. Andernfalls werden Store-Datei, `images/` und `backups/` kopiert (im
   Executor). Der **alte Ordner bleibt unverändert** und kann nach einer
   Sichtprüfung manuell gelöscht werden.
4. Die Option wird gespeichert und der Config Entry neu geladen, damit der Store
   am neuen Pfad öffnet. Berechtigungen und KI-Einstellungen werden dabei erneut
   angewendet.

Die API meldet nach einer erfolgreichen Migration `"reload_required": true`.
Die Kopie läuft vollständig durch, bevor die Option geschrieben wird; es gibt
keinen Zustand, in dem Daten nur zur Hälfte kopiert sind.

Nach der Migration den alten Ordner erst löschen, wenn Produktanzahl und
Stichprobenbilder im Panel stimmen.

## Snapshots vor Migrationen

- Vor jeder Schema-Migration kopiert der Store die bestehende Datei nach
  `<Datenordner>/backups/<zeitstempel>-pre-load-v<n>.json`.
- `async_migrate_entry` legt zusätzlich
  `<Datenordner>/backups/<zeitstempel>-pre-entry-migration-v<n>.json` an.
- Snapshots werden nicht automatisch gelöscht; bei Bedarf manuell aufräumen.

## Prüfsummen

```bash
# data_path = config
cd config/frickeldave_store
sha256sum frickeldave_store.data
find images -type f -exec sha256sum {} + | sort -k2 > images.sha256
```

Bei `share`/`media` denselben Befehl im jeweiligen Ordner
(`/share/frickeldave_store` bzw. `/media/frickeldave_store`) ausführen.

## Wiederherstellung

1. Home Assistant stoppen (Container).
2. Bestehenden Datenordner beiseitelegen (nicht löschen). Der Pfad ergibt sich
   aus der Option `data_path` (siehe Tabelle oben).
3. Backup entpacken, sodass `frickeldave_store/` wieder an Ort und Stelle liegt.
4. Prüfsummen vergleichen (`sha256sum -c images.sha256`).
5. Home Assistant starten. Ist das gespeicherte Schema älter als die Integration,
   migriert der Store beim Laden und legt vorher einen Snapshot an.
6. Integrationsseite prüfen: **Einstellungen → Geräte & Dienste → Frickeldave
   Store**; Panel öffnen und Produktanzahl sowie ein Stichprobenbild kontrollieren.

Wird der Katalog nach `share`/`media` verschoben und anschließend ein
HA-Backup eingespielt, bleibt der externe Ordner unberührt – er ist nicht Teil
des Backups. Nach einem Restore des Config-Ordners zeigt die Option weiterhin
auf `share`/`media`; der Ordner muss also vorhanden sein, sonst schlägt das Setup
mit einer klaren Meldung fehl.

## Restore-Test (Protokoll)

Ein realer Restore-Test gehört zur Abnahme. Vorgehen:

1. Backup ziehen, `sha256sum` von Store-Datei und allen Bildern notieren.
2. Testinstanz starten, Ordner durch das Backup ersetzen.
3. Nach dem Start Prüfsummen erneut berechnen und vergleichen – sie müssen identisch sein.
4. `schemaVersion` in der Store-Datei und Integrationsversion notieren (Migrationsstand
   erhalten).
5. Ergebnis mit Datum und HA-Version dokumentieren.

> Hinweis: In dieser Ausbaustufe wurde der Restore-Test noch nicht gegen eine produktive
> Instanz durchgeführt. Das Runbook ist vorbereitet; die Durchführung erfolgt mit der
> produktiven Datenmigration.
