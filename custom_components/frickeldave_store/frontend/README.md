# Frontend

Das Verzeichnis enthält genau zwei ausgelieferte Dateien:

- `frickeldave-store-panel.js` – das Panel-Modul (`frickeldave-store-panel`,
  Lit-Element, Shadow DOM). Es wird über `panel_custom` mit `embed_iframe=False`
  geladen.
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
python -m pytest tests/test_frontend_assets.py -q
```
