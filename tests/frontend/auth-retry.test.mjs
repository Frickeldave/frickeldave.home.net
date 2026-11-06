import assert from "node:assert/strict";
import test from "node:test";

import { shouldRefreshToken } from "../../custom_components/frickeldave_store/frontend/auth-retry.js";

test("refreshes the access token on the first 401", () => {
  assert.equal(shouldRefreshToken(401, false), true);
});

test("does not refresh a second time after a 401", () => {
  assert.equal(shouldRefreshToken(401, true), false);
});

test("leaves permission and other errors alone", () => {
  for (const status of [0, 400, 403, 404, 409, 500]) {
    assert.equal(shouldRefreshToken(status, false), false);
  }
});
