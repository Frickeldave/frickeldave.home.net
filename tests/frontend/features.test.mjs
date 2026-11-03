import assert from "node:assert/strict";
import test from "node:test";

import {
  featureInfo,
  normalizeFeatures,
} from "../../custom_components/frickeldave_store/frontend/features.js";

const NOT_INSTALLED = "Die Synchronisation ist nicht installiert.";

test("normalizeFeatures returns null for missing or non-object input", () => {
  assert.equal(normalizeFeatures(null), null);
  assert.equal(normalizeFeatures(undefined), null);
  assert.equal(normalizeFeatures([]), null);
  assert.equal(normalizeFeatures("publish"), null);
});

test("reserved feature without provider counts as not installed", () => {
  const features = normalizeFeatures({
    publish: {
      available: false,
      reason: NOT_INSTALLED,
      provider: "",
      contract_version: 1,
    },
  });

  assert.equal(features.publish, undefined);
  assert.deepEqual(featureInfo(features, "publish"), {
    present: false,
    available: false,
    reason: "",
    provider: "",
    contractVersion: 0,
  });
});

test("installed but not ready keeps the reason", () => {
  const features = normalizeFeatures({
    publish: {
      available: false,
      reason: "Token fehlt.",
      provider: "frickeldave_store_syncfd",
      contract_version: 1,
    },
  });

  assert.deepEqual(featureInfo(features, "publish"), {
    present: true,
    available: false,
    reason: "Token fehlt.",
    provider: "frickeldave_store_syncfd",
    contractVersion: 1,
  });
});

test("installed and ready is available", () => {
  const features = normalizeFeatures({
    import: {
      available: true,
      reason: "",
      provider: "frickeldave_store_syncfd",
      contract_version: 1,
    },
  });

  const info = featureInfo(features, "import");
  assert.equal(info.present, true);
  assert.equal(info.available, true);
});

test("provider is trimmed and malformed entries are ignored", () => {
  const features = normalizeFeatures({
    publish: {
      available: true,
      reason: "  Hinweis  ",
      provider: "  frickeldave_store_syncfd  ",
      contract_version: "1",
    },
    import: { provider: "   " },
    sync: null,
    other: "frickeldave_store_syncfd",
  });

  assert.deepEqual(features, {
    publish: {
      available: true,
      reason: "Hinweis",
      provider: "frickeldave_store_syncfd",
      contractVersion: 1,
    },
  });
  assert.equal(featureInfo(features, "import").present, false);
  assert.equal(featureInfo(features, "unknown").present, false);
});
