# Berechtigungen

## Grundsätze

- **Default Deny:** Unbekannte HA-Benutzer haben keine Rolle und erhalten auf jeder
  geschützten HTTP- und WebSocket-Route `403` mit leerem Fehlerobjekt
  (`{"error": {"code": "forbidden", "message": "..."}}`) – ohne Produkt-, Kunden- oder
  Finanzdaten im Body.
- **Stabile Benutzer-ID:** Freigaben hängen an der HA-Benutzer-ID, nicht am Namen.
- **Prüfung je Anfrage:** `require_admin` ist keine Zugangskontrolle. Jede Route ruft
  `PermissionManager.require()` auf; das Mapping wird bei jeder Anfrage neu gelesen.
  Ein Rechteentzug greift damit beim nächsten Zugriff, auch bei geöffnetem Panel.
- **Keine impliziten Finanzrechte:** `finance` folgt weder aus `product` noch aus
  `app_admin`.

## Rollen

| Rolle | Bedeutung | Endpunkte |
| --- | --- | --- |
| `product` | Produktpflege | `/products*`, `/tags*`, `/products/*/stock`, `/products/*/description/suggest` |
| `finance` | Finanzen (spätere Ausbaustufe) | derzeit keine Endpunkte |
| `app_admin` | Anwendungsverwaltung | `/settings`, `/export` |

Rollen sind beliebig kombinierbar.

## Routen-Matrix

| Route | Methode | Rolle |
| --- | --- | --- |
| `/api/frickeldave_store/me` | GET | nur Anmeldung (liefert eigene Rechte, keine Fachdaten) |
| `/api/frickeldave_store/products` | GET, POST | `product` |
| `/api/frickeldave_store/products/{article_number}` | GET, PATCH | `product` |
| `/api/frickeldave_store/products/{article_number}/archive` | POST | `product` |
| `/api/frickeldave_store/products/{article_number}/images` | GET, POST | `product` |
| `/api/frickeldave_store/products/{article_number}/images/{name}` | GET, DELETE | `product` |
| `/api/frickeldave_store/products/{article_number}/images/order` | POST | `product` |
| `/api/frickeldave_store/products/{article_number}/images/main` | POST | `product` |
| `/api/frickeldave_store/products/{article_number}/stock` | GET, POST | `product` |
| `/api/frickeldave_store/products/{article_number}/description/suggest` | POST | `product` |
| `/api/frickeldave_store/tags` | GET, POST | `product` |
| `/api/frickeldave_store/tags/{name}` | DELETE | `product` |
| `/api/frickeldave_store/settings` | GET, PATCH | `app_admin` |
| `/api/frickeldave_store/export` | POST | `app_admin` |
| `frickeldave_store/me` | WS | nur Anmeldung |
| `frickeldave_store/products` | WS | `product` |
| `frickeldave_store/subscribe` | WS | `product` (bei jedem Push erneut geprüft) |

## Freigaben verwalten

Format in Config-/Options-Entry (`users`), eine Zeile pro Benutzer:

```
# Kommentar
<ha_user_id>: product,app_admin
<ha_user_id>: product
```

Änderungen im Options-Flow werden über einen Update-Listener sofort angewendet
(`PermissionManager.load`), ohne Neustart. Unbekannte Rollen werden beim Speichern
abgelehnt (`validation_error`).

## Antwort von `GET /me`

```json
{
  "user_id": "abc123",
  "name": "David Koenig",
  "roles": ["product", "app_admin"],
  "can": {"product": true, "finance": false, "app_admin": true}
}
```

Unbekannte Benutzer erhalten dieselbe Struktur mit leeren Rollen und ausschließlich
`false`-Werten. Das ist die einzige Route ohne Rollenpflicht, weil sie keine geschützten
Daten enthält und das Panel daraus sein Rechte-Gating ableitet.

## Geheimnisse

API-Keys sind ausschließlich serverseitig gespeichert. Sie erscheinen nicht in Antworten
(`api_key_set: true`), nicht in Logs, nicht in Diagnose-Dumps und nicht im Export.
