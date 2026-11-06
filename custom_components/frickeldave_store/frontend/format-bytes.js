/**
 * Größenangaben für Panel und Konfigurationspanel.
 *
 * Bewusst frei von Lit- und DOM-Abhängigkeiten, damit die Formatierung ohne
 * Browser testbar bleibt (`node --test`). Binäre Einheiten wie in den
 * Bild-Metadaten (1 KB = 1024 B), deutsches Dezimalkomma.
 */

const numberFormatter = new Intl.NumberFormat("de-DE");

/** Dateigröße wie der Server: „12,0 MB“, „278 KB“, „900 Bytes“. */
export const formatBytes = (value) => {
  const bytes = Math.max(0, Math.round(Number(value) || 0));
  if (bytes >= 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1).replace(".", ",")} MB`;
  }
  if (bytes >= 1024) {
    return `${numberFormatter.format(Math.round(bytes / 1024))} KB`;
  }
  return `${numberFormatter.format(bytes)} ${bytes === 1 ? "Byte" : "Bytes"}`;
};
