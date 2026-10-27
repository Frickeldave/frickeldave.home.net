# Architektur

## Überblick

`frickeldave_store` ist eine Home-Assistant-Custom-Integration ohne Supervisor-Bezug.
Sie bündelt vier Schichten:

```
HTTP (aiohttp HomeAssistantView)   WebSocket (websocket_api)
                \                  /
                 \                /
              ProductService (products.py)
                 |            |
          CatalogStore    AIService (ai.py)
           (store.py)          |
        /            \         |
  HA Store (JSON)  ImageStorage
```

- **`views.py`** – HTTP-API, JSON only, Fehlerformat `{"error": {"code", "message"}}`.
- **`websocket_api.py`** – identische Rechteprüfung, Abo für Panel-Aktualisierungen.
- **`products.py`** – Fachlogik: Artikelnummern, Filter, Sortierung, Bilder, Buchungen.
- **`store.py`** – Persistenz: HA-`Store` (atomar, versioniert), Bilddateien, Migrationen,
  Snapshots. Reine Dateiablage (`FileBackend`) für CLI und Tests ohne HA.
- **`permissions.py`** – Rollen, Default Deny, Prüfung je Anfrage.
- **`ai.py`** – Anbieter-Abstraktion, DeepSeek-Client, Metadaten-Bereinigung.
- **`export.py`** – idempotenter Katalogexport im Website-Schema.
- **`panel.py`** – statische Dateien und `panel_custom`-Panel.
- **`frontend/`** – Lit-Web-Component, gepflegt vom Frontend-Team (nicht Teil dieser Schicht).

## Anfragefluss

1. HA authentifiziert die Anfrage; `request["hass_user"]` bzw. `connection.user` liefert
   die stabile Benutzer-ID.
2. Die Route ruft `_authorize(request, role)` auf → `PermissionManager.require()`.
   Unbekannte Benutzer und fehlende Rollen enden mit `403` ohne geschützte Daten im Body.
3. Die Fachlogik arbeitet auf dem In-Memory-Store und persistiert atomar.
4. Listener benachrichtigen das WebSocket-Abo; jeder Push prüft die Rolle erneut.

`GET /api/frickeldave_store/me` ist bewusst **nicht** rollengeschützt: Es liefert nur die
eigene Identität und die effektiven Rechte (bei unbekannten Benutzern leere Listen), damit
das Panel „kein Zugriff“ anzeigen kann. Es enthält keine Produkt-, Kunden- oder Finanzdaten.

## Zentrale Entscheidungen

| Entscheidung | Begründung |
| --- | --- |
| Metadaten über HA-`Store`, Datei unter `config/frickeldave_store/` | im Container-Backup enthalten, keine zusätzliche Datenbank, atomare Schreibvorgänge |
| Bilder als Dateien unter `config/frickeldave_store/images/` | nicht in `.storage`, nicht in `/media` oder `/share`; Backup inklusive |
| Keine YAML-Konfiguration | Einrichtung ausschließlich über Config Flow / Options Flow |
| `require_admin=False` | `require_admin` steuert nur die Sichtbarkeit, nie den Datenzugriff |
| Server-seitige Prüfung je Anfrage | Rechteentzug wirkt sofort, auch bei offenem Panel |
| Artikelnummer = Kategoriepräfix + 4 Zeichen | entspricht dem Altbestand (`LAH3K3`), 6 Zeichen, ohne `I/O/0/1` |
| Kategorie unveränderlich | verhindert inkonsistente Artikelnummern; neues Produkt statt Wechsel |
| Ausschließlich Soft-Delete (`archived`) | Export und Website bleiben konsistent, kein Hard-Delete-Pfad |
| KI-Vorschläge ohne Persistenz | bestehende Beschreibungen werden nie automatisch überschrieben |
| Export im Website-Schema, idempotent | Website bleibt Konsument, keine Schemaänderung nötig |

### Speicherort des HA-`Store`

Der `Store`-Helper erhält den Schlüssel `frickeldave_store.data` (Version 1,
Minor-Version 1). Ältere HA-Versionen (bis 2025.1) kennen keinen `path`-Parameter;
dort überschreibt eine kleine Subclass die `path`-Property, sodass die Datei ebenfalls
unter `config/frickeldave_store/` liegt. Ab HA-Versionen mit `path`-Parameter wird dieser
direkt gesetzt. Der Dateiname und der Speicherort bleiben in beiden Fällen identisch.

## Betriebsgrenzen

- Kein Supervisor, kein Add-on, kein `hassio`-Aufruf.
- Kein Hintergrund-Polling externer Dienste (KI nur auf ausdrückliche Anfrage).
- Blockierendes Datei-I/O ausschließlich im Executor (`asyncio.to_thread`,
  `hass.async_add_executor_job`).
- Externe Fehler (KI, Netzwerk) verändern den gespeicherten Datenstand nicht.

## Nicht Teil dieser Ausbaustufe

PayPal-Synchronisation, Kundenverwaltung, Website-Publish-Automation, HACS-Default-Katalog,
produktive Migration. Siehe README (Roadmap).
