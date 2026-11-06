/**
 * Entscheidet, ob eine Antwort die einmalige Erneuerung des HA-Access-Tokens
 * auslösen darf. 401 bedeutet "Token abgelaufen"; 403 ist eine fehlende
 * Berechtigung und bleibt unangetastet. `alreadyRetried` begrenzt den Versuch
 * auf genau eine Wiederholung je Anfrage.
 */
export const shouldRefreshToken = (status, alreadyRetried) => status === 401 && !alreadyRetried;
