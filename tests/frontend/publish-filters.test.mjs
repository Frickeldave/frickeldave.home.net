import assert from "node:assert/strict";
import test from "node:test";

import {
  FILE_FILTER_ALL,
  FILE_STATUS_ORDER,
  fileStatusCounts,
  filterFileChanges,
  statusOptions,
} from "../../custom_components/frickeldave_store/frontend/publish-filters.js";

const file = (path, status) => ({ path, status });

test("fileStatusCounts tolerates missing input and counts every status", () => {
  assert.deepEqual(fileStatusCounts(null), {
    all: 0,
    added: 0,
    changed: 0,
    removed: 0,
    unchanged: 0,
  });
  assert.deepEqual(fileStatusCounts("nope").all, 0);
  assert.deepEqual(
    fileStatusCounts([
      file("a.json", "added"),
      file("b.json", "added"),
      file("c.json", "removed"),
      file("d.json", "unchanged"),
      file("e.json", "changed"),
    ]),
    { all: 5, added: 2, changed: 1, removed: 1, unchanged: 1 },
  );
});

test("unknown or missing status counts as changed so totals stay consistent", () => {
  const counts = fileStatusCounts([
    file("a.json", "added"),
    file("b.json", "unbekannt"),
    { path: "c.json" },
    null,
    "kaputt",
  ]);

  assert.equal(counts.all, 3);
  assert.equal(counts.added, 1);
  assert.equal(counts.changed, 2);
  assert.equal(counts.added + counts.changed + counts.removed + counts.unchanged, counts.all);
});

test("filterFileChanges returns every row for all and for unknown filters", () => {
  const files = [file("a.json", "added"), file("b.json", "changed")];

  assert.deepEqual(filterFileChanges(files, FILE_FILTER_ALL), files);
  assert.deepEqual(filterFileChanges(files, "unbekannt"), files);
  assert.deepEqual(filterFileChanges(files), files);
  assert.deepEqual(filterFileChanges(null, "added"), []);
});

test("filterFileChanges keeps only matching rows without mutating input", () => {
  const files = [
    file("a.json", "added"),
    file("b.json", "changed"),
    file("c.json", "removed"),
    file("d.json", "unchanged"),
    file("e.json", "added"),
  ];

  const added = filterFileChanges(files, "added");

  assert.deepEqual(
    added.map((entry) => entry.path),
    ["a.json", "e.json"],
  );
  assert.equal(files.length, 5);
  assert.deepEqual(filterFileChanges(files, "removed").map((entry) => entry.path), ["c.json"]);
});

test("statusOptions yields Alle first, then the four statuses, zeros included", () => {
  const options = statusOptions({ all: 3, added: 2, changed: 1, removed: 0, unchanged: 0 });

  assert.deepEqual(
    options.map((option) => option.status),
    [FILE_FILTER_ALL, ...FILE_STATUS_ORDER],
  );
  assert.deepEqual(
    options.map((option) => option.count),
    [3, 2, 1, 0, 0],
  );
  assert.deepEqual(statusOptions(null), [
    { status: "all", count: 0 },
    { status: "added", count: 0 },
    { status: "changed", count: 0 },
    { status: "removed", count: 0 },
    { status: "unchanged", count: 0 },
  ]);
});
