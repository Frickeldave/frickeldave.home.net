#!/usr/bin/env node
/**
 * Baut das vendored Lit-Bundle für das Panel.
 *
 * Home Assistant stellt im Browser keine Import-Map für den nackten
 * Spezifizierer "lit" bereit. `panel_custom` importiert das Panel-Modul direkt,
 * deshalb muss Lit als eigenständiges ESM-Bundle neben dem Panel liegen.
 *
 * Aufruf: npm ci && npm run build:frontend
 */

import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { build } from "esbuild";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const outfile = path.join(root, "custom_components", "frickeldave_store", "frontend", "lit.js");

await build({
  absWorkingDir: root,
  entryPoints: ["node_modules/lit/index.js"],
  bundle: true,
  format: "esm",
  minify: true,
  legalComments: "inline",
  outfile,
  logLevel: "info",
});

const code = await readFile(outfile, "utf8");
if (!code.includes("@license") || !/export\s*\{/.test(code) || !code.includes("LitElement")) {
  throw new Error("lit.js sieht nicht wie ein gültiges Lit-ESM-Bundle aus.");
}

console.log(`Geschrieben: ${path.relative(root, outfile)} (${code.length} Bytes)`);
