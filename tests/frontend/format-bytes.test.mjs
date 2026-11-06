import assert from "node:assert/strict";
import test from "node:test";

import { formatBytes } from "../../custom_components/frickeldave_store/frontend/format-bytes.js";

test("formatBytes formats binary units with a German decimal comma", () => {
  assert.equal(formatBytes(900), "900 Bytes");
  assert.equal(formatBytes(1024), "1 KB");
  assert.equal(formatBytes(8192), "8 KB");
  assert.equal(formatBytes(512_000), "500 KB");
  assert.equal(formatBytes(1024 * 1024), "1,0 MB");
  assert.equal(formatBytes(1_200_000), "1,1 MB");
});

test("formatBytes tolerates missing or invalid values", () => {
  assert.equal(formatBytes(0), "0 Bytes");
  assert.equal(formatBytes(1), "1 Byte");
  assert.equal(formatBytes(undefined), "0 Bytes");
  assert.equal(formatBytes("nope"), "0 Bytes");
  assert.equal(formatBytes(-5), "0 Bytes");
});
