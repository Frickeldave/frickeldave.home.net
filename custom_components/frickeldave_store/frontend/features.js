/**
 * Feature-Zustand aus `GET /api/frickeldave_store/me` (Contract 1).
 *
 * Bewusst frei von Lit- und DOM-Abhängigkeiten, damit die Normalisierung ohne
 * Browser testbar bleibt (`node --test`).
 *
 * Contract 1 (Basis 0.2.0) listet die reservierten Fähigkeiten `publish` und
 * `import` auch dann noch auf, wenn keine Erweiterung registriert ist: Sie
 * kommen dann mit `provider: ""`, `available: false` und
 * `reason: FEATURE_NOT_INSTALLED_REASON`. Nur ein gesetzter `provider` (die
 * HA-Domain der Erweiterung) bedeutet, dass die Fähigkeit installiert ist.
 */

const emptyFeature = () => ({
  present: false,
  available: false,
  reason: "",
  provider: "",
  contractVersion: 0,
});

const normalizeEntry = (entry) => {
  if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
    return null;
  }
  // Contract 1: ohne registrierte Erweiterung ist `provider` leer — die
  // Fähigkeit ist damit nicht installiert und existiert für das Panel nicht.
  const provider = typeof entry.provider === "string" ? entry.provider.trim() : "";
  if (!provider) {
    return null;
  }
  const contractVersion = Number(entry.contract_version);
  return {
    available: Boolean(entry.available),
    reason: typeof entry.reason === "string" ? entry.reason.trim() : "",
    provider,
    contractVersion: Number.isFinite(contractVersion) ? contractVersion : 0,
  };
};

export const normalizeFeatures = (raw) => {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return null;
  }
  const features = {};
  for (const [name, entry] of Object.entries(raw)) {
    const feature = normalizeEntry(entry);
    if (feature) {
      features[name] = feature;
    }
  }
  return features;
};

export const featureInfo = (features, name) => {
  if (!features || typeof features !== "object") {
    return emptyFeature();
  }
  const feature = features[name];
  if (!feature || typeof feature !== "object") {
    return emptyFeature();
  }
  return { present: true, ...feature };
};
