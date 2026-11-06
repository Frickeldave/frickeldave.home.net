# Frontend

Das Verzeichnis enthält die ausgelieferten Dateien des Panels:

- `frickeldave-store-panel.js` – das Panel-Modul (`frickeldave-store-panel`,
  Lit-Element, Shadow DOM). Es wird über `panel_custom` mit `embed_iframe=False`
  geladen.
- `features.js` – reine Feature-Normalisierung aus `GET /me` (Contract 1), ohne
  Lit- oder DOM-Abhängigkeiten. Wird vom Panel relativ importiert und in
  `tests/frontend/features.test.mjs` direkt mit `node --test` geprüft.
- `auth-retry.js` – reine Entscheidung, ob ein 401 genau eine Erneuerung des
  HA-Access-Tokens auslöst (403 nie) und wie die Anfrage-URL aussieht:
  relativer Pfad für `hass.fetchWithAuth` (die Instanz-URL stellt HA selbst
  voran), absolute URL nur für den rohen `fetch`-Fallback; geprüft in
  `tests/frontend/auth-retry.test.mjs`.
- `format-bytes.js` – reine Größenformatierung (dezimal, deutsches
  Dezimalkomma) für Belegt-Zeile und Größen-Spalte; geprüft in
  `tests/frontend/format-bytes.test.mjs`.
- `lit.js` – Lit 3.3.3 als vendored, minifiziertes ESM-Bundle inklusive
  Lizenzbanner. Das Bundle wird **nicht** von Hand bearbeitet.

## Warum ein vendored Bundle?

`panel_custom` importiert `module_url` direkt als ES-Modul. Home Assistant liefert
im Browser keine Import-Map aus, deshalb lässt sich der nackte Spezifizierer
`from "lit"` nicht auflösen ("Failed to resolve module specifier 'lit'") und das
Panel bliebe leer. Aus demselben Grund darf das Panel **keine** weiteren nackten
Spezifizierer importieren; `tests/test_frontend_assets.py` prüft, dass alle
Importe relativ (`./`, `../`) oder absolut (`/`) sind und dass `lit.js` die
Exports `LitElement`, `html` und `css` bereitstellt.

## Neu bauen

```bash
npm ci
npm run build:frontend
```

Das Skript `scripts/build_frontend.mjs` bündelt `node_modules/lit/index.js` mit
esbuild (`--bundle --format=esm --minify --legal-comments=inline`) nach
`frontend/lit.js`. Die Versionen von `lit` und `esbuild` sind in `package.json`
exakt gepinnt; das Ergebnis ist etwa 15 kB groß.

## Prüfen

```bash
node --check custom_components/frickeldave_store/frontend/lit.js
node --check custom_components/frickeldave_store/frontend/frickeldave-store-panel.js
node --check custom_components/frickeldave_store/frontend/frickeldave-store-config-panel.js
node --test tests/frontend/features.test.mjs
node --test tests/frontend/auth-retry.test.mjs
node --test tests/frontend/format-bytes.test.mjs
python -m pytest tests/test_frontend_assets.py -q
```
