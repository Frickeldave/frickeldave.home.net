/**
 * Entscheidet, ob eine Antwort die einmalige Erneuerung des HA-Access-Tokens
 * auslösen darf. 401 bedeutet "Token abgelaufen"; 403 ist eine fehlende
 * Berechtigung und bleibt unangetastet. `alreadyRetried` begrenzt den Versuch
 * auf genau eine Wiederholung je Anfrage.
 */
export const shouldRefreshToken = (status, alreadyRetried) => status === 401 && !alreadyRetried;

/**
 * Baut die Anfrage-URL für die HTTP-Schicht. `hass.fetchWithAuth` ist in Home
 * Assistant als `(path, init) => fetchWithAuth(auth, `${auth.data.hassUrl}${path}`, init)`
 * gebunden und stellt die Instanz-URL selbst voran. Deshalb bekommt sie nur den
 * relativen Pfad; der rohe fetch-Fallback braucht die absolute URL.
 */
export const authRequestUrl = (path, baseUrl, useAuthFetch) => {
  if (useAuthFetch) {
    return path;
  }
  return `${String(baseUrl ?? "").replace(/\/+$/, "")}${path}`;
};
