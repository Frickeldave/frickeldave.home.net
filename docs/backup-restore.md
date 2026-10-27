# Backup und Wiederherstellung

## Was gesichert wird

Alle Daten liegen unter `config/` und sind damit in Home-Assistant-Container-Backups
enthalten (inklusive `.storage` und `config/www`).

| Inhalt | Pfad |
| --- | --- |
| Metadaten | `config/frickeldave_store/frickeldave_store.data` |
| Bilder | `config/frickeldave_store/images/<artikelnummer>/` |
| Snapshots | `config/frickeldave_store/backups/` |
| Export | `config/frickeldave_store/export/` (bzw. konfigurierter `export_path`) |

Ein Backup umfasst immer **Store-Datei und Bildordner gemeinsam**. Eine Sicherung nur der
JSON-Datei verliert die Bildzuordnungen.

## Snapshots vor Migrationen

- Vor jeder Schema-Migration kopiert der Store die bestehende Datei nach
  `config/frickeldave_store/backups/<zeitstempel>-pre-load-v<n>.json`.
- `async_migrate_entry` legt zusätzlich `<zeitstempel>-pre-entry-migration-v<n>.json` an.
- Snapshots werden nicht automatisch gelöscht; bei Bedarf manuell aufräumen.

## Manuelles Backup

```bash
# Im Home-Assistant-Container bzw. auf dem Host
tar czf frickeldave_store_backup_$(date +%F).tar.gz \
  config/frickeldave_store
```

Prüfsummen zur späteren Kontrolle:

```bash
sha256sum config/frickeldave_store/frickeldave_store.data
find config/frickeldave_store/images -type f -exec sha256sum {} + | sort -k2 \
  > images.sha256
```

## Wiederherstellung

1. Home Assistant stoppen (Container).
2. Bestehenden Ordner `config/frickeldave_store/` beiseitelegen (nicht löschen).
3. Backup entpacken, sodass `config/frickeldave_store/` wieder an Ort und Stelle liegt.
4. Prüfsummen vergleichen (`sha256sum -c images.sha256`).
5. Home Assistant starten. Ist das gespeicherte Schema älter als die Integration, migriert
   der Store beim Laden und legt vorher einen Snapshot an.
6. Integrationsseite prüfen: **Einstellungen → Geräte & Dienste → Frickeldave Store**;
   Panel öffnen und Produktanzahl sowie ein Stichprobenbild kontrollieren.

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
