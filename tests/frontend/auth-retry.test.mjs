import assert from "node:assert/strict";
import test from "node:test";

import {
  authRequestUrl,
  shouldRefreshToken,
} from "../../custom_components/frickeldave_store/frontend/auth-retry.js";

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

test("fetchWithAuth receives only the relative path", () => {
  assert.equal(
    authRequestUrl("/api/frickeldave_store/me", "https://ha.local:8123", true),
    "/api/frickeldave_store/me",
  );
});

test("the raw fetch fallback receives an absolute URL", () => {
  assert.equal(
    authRequestUrl("/api/frickeldave_store/settings", "https://ha.local:8123", false),
    "https://ha.local:8123/api/frickeldave_store/settings",
  );
});

test("the fallback tolerates trailing slashes in the base URL", () => {
  for (const baseUrl of ["https://ha.local:8123/", "https://ha.local:8123///"]) {
    assert.equal(
      authRequestUrl("/api/frickeldave_store/me", baseUrl, false),
      "https://ha.local:8123/api/frickeldave_store/me",
    );
  }
});

test("an empty base URL keeps the path relative for the fallback", () => {
  assert.equal(authRequestUrl("/api/frickeldave_store/me", "", false), "/api/frickeldave_store/me");
});
