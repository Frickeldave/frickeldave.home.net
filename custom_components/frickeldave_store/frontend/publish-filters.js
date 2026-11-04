/**
 * Filter für die Dateiliste des Veröffentlichungsdialogs.
 *
 * Bewusst frei von Lit- und DOM-Abhängigkeiten, damit die Auswahl ohne Browser
 * testbar bleibt (`node --test`). Die Statuswerte entsprechen dem Vertrag der
 * Erweiterung; unbekannte oder fehlende Werte zählen wie `changed`, damit die
 * Summe der Optionen immer der Anzahl der Zeilen entspricht.
 */

export const FILE_FILTER_ALL = "all";

export const FILE_STATUS_ORDER = Object.freeze([
  "added",
  "changed",
  "removed",
  "unchanged",
]);

const fileStatus = (entry) => {
  const status = entry && typeof entry === "object" ? entry.status : "";
  return FILE_STATUS_ORDER.includes(status) ? status : "changed";
};

const safeCount = (value) => {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? Math.floor(number) : 0;
};

/** Zählt die Zeilen je Status; `all` ist die Gesamtzahl der Zeilen. */
export const fileStatusCounts = (files) => {
  const counts = { all: 0 };
  for (const status of FILE_STATUS_ORDER) {
    counts[status] = 0;
  }
  for (const entry of Array.isArray(files) ? files : []) {
    if (!entry || typeof entry !== "object") {
      continue;
    }
    counts.all += 1;
    counts[fileStatus(entry)] += 1;
  }
  return counts;
};

/** Liefert die Zeilen für einen Status; `all` oder Unbekanntes zeigt alles. */
export const filterFileChanges = (files, status = FILE_FILTER_ALL) => {
  const list = Array.isArray(files) ? files : [];
  if (!FILE_STATUS_ORDER.includes(status)) {
    return [...list];
  }
  return list.filter(
    (entry) => entry && typeof entry === "object" && fileStatus(entry) === status,
  );
};

/**
 * Reihenfolge der Filterknöpfe: Alle, Neu, Geändert, Entfernt, Unverändert.
 * Liefert nur Status und Anzahl — die deutschen Beschriftungen bleiben im
 * Panel, damit es genau eine Quelle für die Statuslabels gibt.
 */
export const statusOptions = (counts) => {
  const source = counts && typeof counts === "object" ? counts : {};
  return [
    { status: FILE_FILTER_ALL, count: safeCount(source.all) },
    ...FILE_STATUS_ORDER.map((status) => ({
      status,
      count: safeCount(source[status]),
    })),
  ];
};
