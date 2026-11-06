/**
 * Frickeldave Store - Konfigurationspanel für Home Assistant.
 *
 * Wird über `panel_custom` mit `config_panel_domain` als eigenes
 * Konfigurationspanel des Eintrags geöffnet. Die Komponente nutzt dieselbe
 * HTTP-Schicht wie das Seitenleisten-Panel: Bearer-Token aus `hass.auth`,
 * Basis-URL aus `hass.auth.data.hassUrl`.
 *
 * Aufbau:
 *   1. Konstanten, Normalisierung und API-Schicht
 *   2. Zugriff, Tabs und Datenzustände
 *   3. Benutzerverwaltung (Liste, Rollen-Dialog, Entfernen)
 *   4. KI-Einstellungen
 *   5. Render-Methoden und Styles (HA-Design-Tokens plus --fd-* Schicht)
 */

import { LitElement, html, css } from "./lit.js";
import { formatBytes } from "./format-bytes.js";

const API_ROOT = "/api/frickeldave_store";
const REQUEST_TIMEOUT_MS = 30000;
const TOAST_TIMEOUT_MS = 6000;
// Event der Basis, sobald sich registrierte Erweiterungen ändern (Contract 1).
const FEATURES_EVENT = "frickeldave_store_features_changed";

// Grenzen der einstellbaren Bildgröße; der Server prüft denselben Bereich.
const IMAGE_MAX_BYTES_DEFAULT = 500 * 1024;
const IMAGE_MAX_KB_MIN = 50;
const IMAGE_MAX_KB_MAX = 10240;

const TABS = Object.freeze([
  { id: "general", label: "Allgemeine Einstellungen", panelId: "fd-config-panel-general" },
  { id: "users", label: "Benutzerverwaltung", panelId: "fd-config-panel-users" },
  { id: "locations", label: "Lagerstätten", panelId: "fd-config-panel-locations" },
  { id: "ai", label: "KI-Einstellungen", panelId: "fd-config-panel-ai" },
]);

const DEFAULT_LOCATION_NAMES = Object.freeze(["Lager", "Verkaufsschrank"]);

class ApiError extends Error {
  constructor(message, status = 0, code = "") {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

const isAbortError = (error) => error?.name === "AbortError";

const asText = (value) => (typeof value === "string" ? value : "");

const isObject = (value) => Boolean(value) && typeof value === "object" && !Array.isArray(value);

const numberFormatter = new Intl.NumberFormat("de-DE");

const formatNumber = (value) => {
  const number = Number(value);
  return Number.isFinite(number) ? numberFormatter.format(number) : "0";
};

const normalizeUser = (raw) => {
  const userId = raw?.user_id ?? raw?.userId ?? "";
  return {
    userId: String(userId ?? ""),
    name: asText(raw?.name),
    roles: Array.isArray(raw?.roles)
      ? raw.roles.filter((role) => typeof role === "string")
      : [],
    roleLabels: Array.isArray(raw?.role_labels)
      ? raw.role_labels.filter((label) => typeof label === "string")
      : [],
  };
};

const normalizeUsersPayload = (data) => {
  const payload = data && typeof data === "object" ? data : {};
  const users = Array.isArray(payload.users) ? payload.users : [];
  const available = Array.isArray(payload.available_users) ? payload.available_users : [];
  const roles = Array.isArray(payload.roles) ? payload.roles : [];

  return {
    users: users.map(normalizeUser).filter((user) => user.userId),
    availableUsers: available.map(normalizeUser).filter((user) => user.userId),
    roles: roles
      .map((role) => ({
        id: String(role?.id ?? ""),
        label: asText(role?.label),
      }))
      .filter((role) => role.id),
  };
};

const asCount = (value) => {
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(0, Math.trunc(number)) : 0;
};

const normalizeLocations = (data) => {
  const raw = Array.isArray(data) ? data : [];
  const locations = raw
    .filter((entry) => entry && typeof entry === "object")
    .map((entry, index) => ({
      id: String(entry.id ?? ""),
      name: asText(entry.name),
      position: Number.isFinite(Number(entry.position)) ? Number(entry.position) : index,
      stockTotal: asCount(entry.stock_total),
      productsWithStock: asCount(entry.products_with_stock),
    }))
    .filter((entry) => entry.name)
    .sort((a, b) => a.position - b.position);
  if (locations.length > 0) {
    return locations;
  }
  return DEFAULT_LOCATION_NAMES.map((name, index) => ({
    id: "",
    name,
    position: index,
    stockTotal: 0,
    productsWithStock: 0,
  }));
};

const normalizeStorageUsage = (data) => {
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    return null;
  }
  return {
    totalBytes: asCount(data.total_bytes),
    dataBytes: asCount(data.data_bytes),
    imagesBytes: asCount(data.images_bytes),
    backupsBytes: asCount(data.backups_bytes),
    files: asCount(data.files),
  };
};

const normalizeStorage = (data) => {
  const payload = data && typeof data === "object" ? data : {};
  const dataPath = asText(payload.data_path);
  const choices = Array.isArray(payload.choices)
    ? payload.choices
        .filter((entry) => entry && typeof entry === "object" && asText(entry.id))
        .map((entry) => ({
          id: asText(entry.id),
          label: asText(entry.label) || asText(entry.id),
          description: asText(entry.description),
          backupIncluded: Boolean(entry.backup_included),
        }))
    : [];
  const paths = Array.isArray(payload.paths)
    ? payload.paths
        .filter((entry) => entry && typeof entry === "object" && asText(entry.path))
        .map((entry) => ({ label: asText(entry.label), path: asText(entry.path) }))
    : [];
  const backupIncluded =
    typeof payload.backup_included === "boolean" ? payload.backup_included : dataPath === "config";
  return {
    dataPath,
    effectivePath: asText(payload.effective_path),
    backupIncluded,
    excludeFromBackup:
      typeof payload.exclude_from_backup === "boolean" ? payload.exclude_from_backup : !backupIncluded,
    paths,
    choices,
    usage: normalizeStorageUsage(payload.usage),
  };
};

const normalizeImages = (data) => {
  const payload = data && typeof data === "object" && !Array.isArray(data) ? data : {};
  const maxBytes = Number(payload.max_bytes);
  return {
    maxBytes:
      Number.isFinite(maxBytes) && maxBytes > 0 ? Math.round(maxBytes) : IMAGE_MAX_BYTES_DEFAULT,
  };
};

const normalizeFeatureStatus = (entry) => {
  if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
    return null;
  }
  const contractVersion = Number(entry.contract_version);
  return {
    available: Boolean(entry.available),
    reason: asText(entry.reason).trim(),
    provider: asText(entry.provider),
    contractVersion: Number.isFinite(contractVersion) ? contractVersion : 0,
    title: asText(entry.title),
    requiredRole: asText(entry.required_role),
    providesPanelRoutes: Boolean(entry.provides_panel_routes),
  };
};

/**
 * Feature-Status aus `/me` bzw. `/settings`. `null` bedeutet: die Basis liefert
 * die Angabe noch nicht; dann bleibt nur der allgemeine Erweiterungshinweis.
 */
const normalizeFeatures = (raw) => {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return null;
  }
  const features = {};
  for (const [name, entry] of Object.entries(raw)) {
    const normalized = normalizeFeatureStatus(entry);
    if (normalized) {
      features[name] = normalized;
    }
  }
  return features;
};

const normalizeSettingsPayload = (data) => {
  const payload = data && typeof data === "object" ? data : {};
  const ai = payload.ai && typeof payload.ai === "object" ? payload.ai : payload;
  return {
    form: {
      endpoint: asText(ai?.endpoint),
      model: asText(ai?.model),
      prompt: asText(ai?.prompt),
      api_key: "",
    },
    apiKeySet: Boolean(ai?.api_key_set),
    locations: normalizeLocations(payload.locations),
    storage: normalizeStorage(payload.storage),
    images: normalizeImages(payload.images),
    features: normalizeFeatures(payload.features),
  };
};

export class FrickeldaveStoreConfigPanel extends LitElement {
  static properties = {
    hass: { attribute: false, noAccessor: true },

    _accessState: { state: true },
    _accessError: { state: true },
    _me: { state: true },
    _features: { state: true },

    _tab: { state: true },

    _data: { state: true },
    _usersLoading: { state: true },
    _usersError: { state: true },
    _usersBusy: { state: true },

    _roleDialog: { state: true },
    _addUserFilter: { state: true },
    _addUserSelection: { state: true },
    _deleteDialog: { state: true },

    _ai: { state: true },
    _locations: { state: true },
    _storage: { state: true },
    _images: { state: true },
    _toast: { state: true },

    _forwardAvailable: { state: true },
  };

  constructor() {
    super();

    this._hass = undefined;
    this._controllers = new Set();
    this._toastTimer = undefined;
    this._focusReturn = null;

    this._accessState = "idle";
    this._accessError = "";
    this._me = null;
    this._features = null;
    this._featureUnsubscribe = null;
    this._featureConnection = null;
    this._featureSubscribing = false;
    this._featuresTimer = undefined;

    this._tab = "users";

    this._data = normalizeUsersPayload(null);
    this._usersLoading = false;
    this._usersError = "";
    this._usersBusy = false;

    this._roleDialog = null;
    this._addUserFilter = "";
    this._addUserSelection = "";
    this._deleteDialog = null;

    this._ai = {
      loading: false,
      saving: false,
      loaded: false,
      error: "",
      notice: "",
      form: { endpoint: "", model: "", prompt: "", api_key: "" },
      apiKeySet: false,
    };

    this._locations = {
      loading: false,
      saving: false,
      error: "",
      notice: "",
      items: normalizeLocations(null).map((entry) => ({ ...entry, originalName: entry.name })),
      loaded: false,
    };

    this._storage = {
      loading: false,
      saving: false,
      loaded: false,
      error: "",
      notice: "",
      reloadRequired: false,
      dataPath: "",
      draftPath: "",
      effectivePath: "",
      backupIncluded: false,
      excludeFromBackup: true,
      paths: [],
      choices: [],
      usage: null,
    };

    this._images = {
      loading: false,
      saving: false,
      loaded: false,
      error: "",
      notice: "",
      maxBytes: IMAGE_MAX_BYTES_DEFAULT,
      draftKb: String(IMAGE_MAX_BYTES_DEFAULT / 1024),
    };

    this._toast = null;
    this._roleDialogSnapshot = "";
    this._roleShakeTimer = undefined;

    this._forwardAvailable = false;
    this._awaitingForwardPopstate = false;
    this._onPopState = () => {
      if (this._awaitingForwardPopstate) {
        this._awaitingForwardPopstate = false;
        return;
      }
      this._forwardAvailable = true;
    };
  }

  set hass(value) {
    const previous = this._hass;
    this._hass = value;
    if (value && value !== previous) {
      this._bootstrap();
    }
    this.requestUpdate();
  }

  get hass() {
    return this._hass;
  }

  connectedCallback() {
    super.connectedCallback();
    window.addEventListener("popstate", this._onPopState);
    this._bootstrap();
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this._unsubscribeFeatureEvents();
    clearTimeout(this._featuresTimer);
    for (const controller of this._controllers) {
      controller.abort();
    }
    this._controllers.clear();
    clearTimeout(this._toastTimer);
    clearTimeout(this._roleShakeTimer);
    window.removeEventListener("popstate", this._onPopState);
  }

  updated() {
    this._syncDialogs();
  }

  /* --------------------------------------------------------------------- */
  /* Navigation der Konfigurationsseite                                     */
  /* --------------------------------------------------------------------- */

  _sameOriginReferrer() {
    if (!document.referrer) {
      return false;
    }
    try {
      return new URL(document.referrer).origin === window.location.origin;
    } catch {
      return false;
    }
  }

  _canGoBack() {
    return (window.history?.length ?? 1) > 1;
  }

  _goBack() {
    if (!this._canGoBack()) {
      return;
    }
    if (this._sameOriginReferrer()) {
      this._forwardAvailable = true;
      window.history.back();
      return;
    }
    window.location.assign("/config/integrations");
  }

  _goForward() {
    if (!this._forwardAvailable) {
      return;
    }
    this._forwardAvailable = false;
    this._awaitingForwardPopstate = true;
    window.history.forward();
  }

  /* --------------------------------------------------------------------- */
  /* Basis: Sitzung, Basis-URL, HTTP                                        */
  /* --------------------------------------------------------------------- */

  _token() {
    const auth = this.hass?.auth;
    const token = auth?.data?.access_token || auth?.accessToken || "";
    return typeof token === "string" ? token : "";
  }

  _baseUrl() {
    const raw = this.hass?.auth?.data?.hassUrl || window.location.origin || "";
    return String(raw).replace(/\/+$/, "");
  }

  _httpFallbackMessage(status) {
    if (status === 400) {
      return "Die Anfrage ist ungültig. Bitte die Eingaben prüfen.";
    }
    if (status === 401) {
      return "Die Sitzung ist abgelaufen. Bitte die Seite neu laden.";
    }
    if (status === 403) {
      return "Kein Zugriff auf diese Funktion.";
    }
    if (status === 404) {
      return "Der Eintrag wurde nicht gefunden.";
    }
    if (status === 409) {
      return "Der Vorgang steht im Konflikt mit dem aktuellen Datenstand.";
    }
    if (status >= 500) {
      return "Der Server hat einen Fehler gemeldet. Bitte erneut versuchen.";
    }
    return "Die Anfrage ist fehlgeschlagen.";
  }

  _errorText(error) {
    if (error instanceof ApiError) {
      return error.message || this._httpFallbackMessage(error.status);
    }
    return "Unerwarteter Fehler. Bitte erneut versuchen.";
  }

  async _api(path, options = {}) {
    const { method = "GET", body, timeoutMs = REQUEST_TIMEOUT_MS } = options;

    if (!this.hass) {
      throw new ApiError("Home Assistant ist noch nicht verbunden. Bitte kurz warten.", 0, "no_hass");
    }
    const token = this._token();
    if (!token) {
      throw new ApiError("Keine gültige Sitzung gefunden. Bitte die Seite neu laden.", 401, "no_token");
    }

    const controller = new AbortController();
    this._controllers.add(controller);
    let timedOut = false;
    const timeoutId = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);

    const headers = {
      Authorization: `Bearer ${token}`,
      Accept: "application/json",
    };

    let payload;
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }

    try {
      const response = await fetch(`${this._baseUrl()}${path}`, {
        method,
        headers,
        body: payload,
        signal: controller.signal,
        credentials: "same-origin",
      });

      if (!response.ok) {
        let message = "";
        let code = "";
        try {
          const data = await response.json();
          message = typeof data?.error?.message === "string" ? data.error.message : "";
          code = typeof data?.error?.code === "string" ? data.error.code : "";
        } catch {
          message = "";
        }
        throw new ApiError(message || this._httpFallbackMessage(response.status), response.status, code);
      }

      if (response.status === 204) {
        return null;
      }

      const text = await response.text();
      if (!text) {
        return null;
      }
      try {
        return JSON.parse(text);
      } catch {
        return null;
      }
    } catch (error) {
      if (timedOut) {
        throw new ApiError("Zeitüberschreitung bei der Serveranfrage. Bitte erneut versuchen.", 408, "timeout");
      }
      if (isAbortError(error) || error instanceof ApiError) {
        throw error;
      }
      throw new ApiError("Verbindung zum Server fehlgeschlagen. Bitte erneut versuchen.", 0, "network_error");
    } finally {
      clearTimeout(timeoutId);
      this._controllers.delete(controller);
    }
  }

  _can(permission) {
    return Boolean(this._me?.can?.[permission]);
  }

  /** Hinweis auf den Ort der Veröffentlichungs-Einstellungen (nur Text, kein Formular). */
  _publishHint() {
    const feature = this._features?.publish;
    if (!feature) {
      return "Veröffentlichen wird von einer Erweiterung bereitgestellt. Token und Ziel-Repository pflegst du in deren Einstellungen; im Basis-Panel wird kein Token gespeichert.";
    }
    if (!feature.available) {
      return feature.reason || "Veröffentlichen ist derzeit nicht verfügbar.";
    }
    return `Veröffentlichen wird von „${feature.provider || "einer Erweiterung"}“ verwaltet. Token und Ziel-Repository pflegst du in deren Einstellungen; im Basis-Panel wird kein Token gespeichert.`;
  }

  _visibleTabs() {
    return TABS;
  }

  _ensureValidTab() {
    const tabs = this._visibleTabs();
    if (!tabs.some((tab) => tab.id === this._tab)) {
      this._tab = tabs[0]?.id || "users";
    }
  }

  _ensureFeatureSubscription() {
    const connection = this.hass?.connection;
    if (!connection || typeof connection.subscribeEvents !== "function" || !this._token()) {
      return;
    }
    if (
      this._featureConnection === connection &&
      (this._featureSubscribing || this._featureUnsubscribe)
    ) {
      return;
    }
    this._unsubscribeFeatureEvents();
    this._featureConnection = connection;
    this._featureSubscribing = true;

    let pending;
    try {
      pending = connection.subscribeEvents(
        () => this._scheduleFeatureRefresh(),
        FEATURES_EVENT,
      );
    } catch {
      this._featureSubscribing = false;
      this._featureConnection = null;
      return;
    }

    Promise.resolve(pending)
      .then((unsubscribe) => {
        this._featureSubscribing = false;
        if (this._featureConnection !== connection || !this.isConnected) {
          if (typeof unsubscribe === "function") {
            unsubscribe();
          }
          return;
        }
        this._featureUnsubscribe = unsubscribe;
      })
      .catch(() => {
        this._featureSubscribing = false;
      });
  }

  _unsubscribeFeatureEvents() {
    this._featureSubscribing = false;
    this._featureConnection = null;
    const unsubscribe = this._featureUnsubscribe;
    this._featureUnsubscribe = null;
    if (typeof unsubscribe === "function") {
      try {
        unsubscribe();
      } catch {
        // Die Verbindung kann beim Trennen bereits geschlossen sein.
      }
    }
  }

  _scheduleFeatureRefresh() {
    clearTimeout(this._featuresTimer);
    this._featuresTimer = setTimeout(() => {
      this._featuresTimer = undefined;
      this._reloadFeatures();
    }, 150);
  }

  /** Nur `/settings` neu laden, damit der Anbieterwechsel ohne Formularverlust sichtbar wird. */
  async _reloadFeatures() {
    if (!this.hass || !this._token() || this._accessState !== "ready" || !this._can("app_admin")) {
      return;
    }
    if (this._ai.loading) {
      return;
    }
    await this._loadSettings();
  }

  /* --------------------------------------------------------------------- */
  /* Zugriff und Tabs                                                       */
  /* --------------------------------------------------------------------- */

  _bootstrap() {
    if (!this.hass || !this._token()) {
      return;
    }
    this._ensureFeatureSubscription();
    if (this._accessState !== "idle") {
      return;
    }
    this._loadMe();
  }

  async _loadMe() {
    this._accessState = "loading";
    this._accessError = "";
    try {
      const me = await this._api(`${API_ROOT}/me`);
      this._me = {
        userId: me?.user_id || "",
        name: asText(me?.name),
        roles: Array.isArray(me?.roles) ? me.roles.filter((role) => typeof role === "string") : [],
        can: {
          product: Boolean(me?.can?.product),
          finance: Boolean(me?.can?.finance),
          app_admin: Boolean(me?.can?.app_admin),
        },
        version: typeof me?.version === "string" ? me.version : "",
      };
      this._features = normalizeFeatures(me?.features);
      if (this._can("app_admin")) {
        this._accessState = "ready";
        this._loadUsers();
        this._loadSettings();
      } else {
        this._accessState = "denied";
      }
    } catch (error) {
      if (isAbortError(error)) {
        if (this._accessState === "loading") {
          this._accessState = "idle";
        }
        return;
      }
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        this._accessState = "denied";
        this._accessError = this._errorText(error);
        return;
      }
      this._accessError = this._errorText(error);
      this._accessState = "error";
    }
  }

  _retryAccess() {
    this._accessState = "idle";
    this._accessError = "";
    this._bootstrap();
  }

  _selectTab(tab) {
    const tabs = this._visibleTabs();
    if (!tabs.some((entry) => entry.id === tab) || this._tab === tab) {
      return;
    }
    this._tab = tab;
    if (
      tab !== "users" &&
      this._accessState === "ready" &&
      !this._ai.loaded &&
      !this._ai.loading &&
      !this._locations.loading
    ) {
      this._loadSettings();
    }
  }

  _onTabKeydown(event) {
    const tabs = this._visibleTabs();
    const currentIndex = tabs.findIndex((entry) => entry.id === this._tab);
    let nextIndex = currentIndex;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      nextIndex = (currentIndex + 1) % tabs.length;
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = tabs.length - 1;
    } else {
      return;
    }
    event.preventDefault();
    const next = tabs[nextIndex];
    this._selectTab(next.id);
    this.updateComplete
      .then(() => {
        this.renderRoot?.querySelector(`#fd-config-tab-${next.id}`)?.focus?.();
      })
      .catch(() => {});
  }

  /* --------------------------------------------------------------------- */
  /* Benutzerdaten                                                          */
  /* --------------------------------------------------------------------- */

  async _loadUsers() {
    if (!this._can("app_admin") || this._usersLoading) {
      return;
    }
    this._usersLoading = true;
    this._usersError = "";
    try {
      const data = await this._api(`${API_ROOT}/users`);
      this._data = normalizeUsersPayload(data);
    } catch (error) {
      if (isAbortError(error)) {
        return;
      }
      this._usersError = this._errorText(error);
    } finally {
      this._usersLoading = false;
    }
  }

  _applyUsersPayload(data) {
    if (!data || !Array.isArray(data.users)) {
      return false;
    }
    this._data = normalizeUsersPayload(data);
    this._usersError = "";
    return true;
  }

  _roleLabel(roleId) {
    const role = this._data.roles.find((entry) => entry.id === roleId);
    return role?.label || roleId;
  }

  _userRoleLabels(user) {
    return user.roles.map((roleId, index) => user.roleLabels[index] || this._roleLabel(roleId));
  }

  _orderRoles(roleIds) {
    const known = this._data.roles.map((role) => role.id).filter((id) => roleIds.includes(id));
    const extras = roleIds.filter((id) => !known.includes(id));
    return [...known, ...extras];
  }

  _toastMessage(message) {
    clearTimeout(this._toastTimer);
    this._toast = { message };
    this._toastTimer = setTimeout(() => {
      this._toast = null;
    }, TOAST_TIMEOUT_MS);
  }

  /* --------------------------------------------------------------------- */
  /* Fokus- und Dialogverwaltung                                            */
  /* --------------------------------------------------------------------- */

  _captureFocus() {
    try {
      const active = this.renderRoot?.activeElement;
      this._focusReturn = active && typeof active.focus === "function" ? active : null;
    } catch {
      this._focusReturn = null;
    }
  }

  _restoreFocus() {
    const target = this._focusReturn;
    this._focusReturn = null;
    this.updateComplete
      .then(() => {
        const element =
          target && target.isConnected
            ? target
            : this.renderRoot?.querySelector("#fd-config-heading");
        if (element && typeof element.focus === "function") {
          element.focus();
        }
      })
      .catch(() => {});
  }

  _syncDialogs() {
    const entries = [
      ["#fd-role-dialog", Boolean(this._roleDialog)],
      ["#fd-delete-dialog", Boolean(this._deleteDialog)],
    ];
    for (const [selector, shouldOpen] of entries) {
      const dialog = this.renderRoot?.querySelector(selector);
      if (!dialog || !shouldOpen || dialog.open) {
        continue;
      }
      try {
        dialog.showModal();
        const target = dialog.querySelector("[data-autofocus]");
        if (target && typeof target.focus === "function") {
          target.focus();
        }
      } catch {
        // showModal kann fehlschlagen, wenn der Dialog bereits geschlossen wird.
      }
    }
  }

  _handleDialogCancel(event, close) {
    event.preventDefault();
    close();
  }

  /* --------------------------------------------------------------------- */
  /* Rollen-Dialog (Hinzufügen und Bearbeiten)                              */
  /* --------------------------------------------------------------------- */

  _openAddDialog() {
    if (!this._can("app_admin") || this._usersLoading || this._usersBusy) {
      return;
    }
    this._captureFocus();
    this._addUserFilter = "";
    this._addUserSelection = "";
    this._roleDialog = {
      mode: "add",
      userId: "",
      name: "",
      roles: [],
      leftSelection: [],
      rightSelection: [],
      busy: false,
      error: "",
      shake: false,
    };
    this._roleDialogSnapshot = this._roleDialogState();
  }

  _openEditDialog(user) {
    if (!this._can("app_admin") || !user || this._usersBusy) {
      return;
    }
    this._captureFocus();
    this._addUserFilter = "";
    this._addUserSelection = "";
    this._roleDialog = {
      mode: "edit",
      userId: user.userId,
      name: user.name || user.userId,
      roles: this._orderRoles([...user.roles]),
      leftSelection: [],
      rightSelection: [],
      busy: false,
      error: "",
      shake: false,
    };
    this._roleDialogSnapshot = this._roleDialogState();
  }

  _roleDialogState() {
    const dialog = this._roleDialog;
    if (!dialog) {
      return "";
    }
    return JSON.stringify({ userId: dialog.userId, roles: dialog.roles });
  }

  _roleDialogHasChanges() {
    return Boolean(this._roleDialog) && this._roleDialogSnapshot !== this._roleDialogState();
  }

  _shakeRoleDialog() {
    if (!this._roleDialog) {
      return;
    }
    clearTimeout(this._roleShakeTimer);
    this._updateRoleDialog({ shake: true });
    this._roleShakeTimer = setTimeout(() => {
      if (this._roleDialog) {
        this._updateRoleDialog({ shake: false });
      }
    }, 600);
  }

  _closeRoleDialog({ force = false } = {}) {
    if (this._roleDialog?.busy) {
      return;
    }
    if (!force && this._roleDialogHasChanges()) {
      this._shakeRoleDialog();
      return;
    }
    clearTimeout(this._roleShakeTimer);
    this._roleDialogSnapshot = "";
    this._roleDialog = null;
    this._addUserFilter = "";
    this._addUserSelection = "";
    this._restoreFocus();
  }

  _discardRoleDialog() {
    this._closeRoleDialog({ force: true });
  }

  _updateRoleDialog(patch) {
    if (!this._roleDialog) {
      return;
    }
    this._roleDialog = { ...this._roleDialog, ...patch };
  }

  _filteredAvailableUsers() {
    const granted = new Set(this._data.users.map((user) => user.userId));
    const needle = this._addUserFilter.trim().toLowerCase();
    return this._data.availableUsers.filter((user) => {
      if (granted.has(user.userId)) {
        return false;
      }
      if (!needle) {
        return true;
      }
      return `${user.name} ${user.userId}`.toLowerCase().includes(needle);
    });
  }

  _onAddFilterInput(value) {
    this._addUserFilter = value;
    const visible = this._filteredAvailableUsers();
    if (this._addUserSelection && !visible.some((user) => user.userId === this._addUserSelection)) {
      this._addUserSelection = "";
    }
  }

  _onAddUserChange(event) {
    this._addUserSelection = event.target?.value || "";
    this._updateRoleDialog({ error: "" });
  }

  _availableRoles() {
    const assigned = new Set(this._roleDialog?.roles ?? []);
    return this._data.roles.filter((role) => !assigned.has(role.id));
  }

  _assignedRoles() {
    const dialog = this._roleDialog;
    if (!dialog) {
      return [];
    }
    return this._orderRoles(dialog.roles)
      .map((roleId) => {
        const role = this._data.roles.find((entry) => entry.id === roleId);
        return { id: roleId, label: role?.label || roleId };
      });
  }

  _selectedValues(select) {
    if (!select) {
      return [];
    }
    const selected = select.selectedOptions;
    if (selected && selected.length) {
      return [...selected].map((option) => option.value);
    }
    return [...(select.options ?? [])]
      .filter((option) => option.selected)
      .map((option) => option.value);
  }

  _onTransferSelection(side, event) {
    const values = this._selectedValues(event.target);
    this._updateRoleDialog(side === "left" ? { leftSelection: values } : { rightSelection: values });
  }

  _clearTransferSelections() {
    this.updateComplete
      .then(() => {
        const selects = this.renderRoot?.querySelectorAll("select[multiple]") ?? [];
        for (const select of selects) {
          select.selectedIndex = -1;
        }
      })
      .catch(() => {});
  }

  _moveToAssigned() {
    const dialog = this._roleDialog;
    if (!dialog || dialog.busy) {
      return;
    }
    const selected = dialog.leftSelection.filter((roleId) => !dialog.roles.includes(roleId));
    if (selected.length === 0) {
      return;
    }
    this._roleDialog = {
      ...dialog,
      roles: this._orderRoles([...dialog.roles, ...selected]),
      leftSelection: [],
      error: "",
    };
    this._clearTransferSelections();
  }

  _moveToAvailable() {
    const dialog = this._roleDialog;
    if (!dialog || dialog.busy) {
      return;
    }
    const selected = dialog.rightSelection.filter((roleId) => dialog.roles.includes(roleId));
    if (selected.length === 0) {
      return;
    }
    this._roleDialog = {
      ...dialog,
      roles: dialog.roles.filter((roleId) => !selected.includes(roleId)),
      rightSelection: [],
      error: "",
    };
    this._clearTransferSelections();
  }

  async _saveRoleDialog() {
    const dialog = this._roleDialog;
    if (!dialog || dialog.busy) {
      return;
    }
    const userId = dialog.mode === "add" ? this._addUserSelection : dialog.userId;
    if (!userId) {
      this._updateRoleDialog({ error: "Bitte einen Home-Assistant-Benutzer auswählen." });
      return;
    }

    this._updateRoleDialog({ busy: true, error: "" });
    this._usersBusy = true;
    try {
      const data = await this._api(`${API_ROOT}/users/${encodeURIComponent(userId)}`, {
        method: "PUT",
        body: { roles: this._orderRoles(dialog.roles) },
      });
      const applied = this._applyUsersPayload(data);
      this._roleDialog = null;
      this._addUserFilter = "";
      this._addUserSelection = "";
      this._usersBusy = false;
      this._toastMessage(dialog.mode === "add" ? "Benutzer hinzugefügt." : "Rollen gespeichert.");
      this._restoreFocus();
      if (!applied) {
        await this._loadUsers();
      }
    } catch (error) {
      this._usersBusy = false;
      if (isAbortError(error)) {
        return;
      }
      this._updateRoleDialog({ busy: false, error: this._errorText(error) });
    }
  }

  /* --------------------------------------------------------------------- */
  /* Benutzer entfernen                                                     */
  /* --------------------------------------------------------------------- */

  _openDeleteDialog(user) {
    if (!this._can("app_admin") || !user || this._usersBusy) {
      return;
    }
    this._captureFocus();
    this._deleteDialog = {
      userId: user.userId,
      name: user.name || user.userId,
      busy: false,
      error: "",
    };
  }

  _closeDeleteDialog() {
    if (this._deleteDialog?.busy) {
      return;
    }
    this._deleteDialog = null;
    this._restoreFocus();
  }

  async _confirmDelete() {
    const dialog = this._deleteDialog;
    if (!dialog || dialog.busy) {
      return;
    }
    this._deleteDialog = { ...dialog, busy: true, error: "" };
    this._usersBusy = true;
    try {
      const data = await this._api(`${API_ROOT}/users/${encodeURIComponent(dialog.userId)}`, {
        method: "DELETE",
      });
      const applied = this._applyUsersPayload(data);
      this._deleteDialog = null;
      this._usersBusy = false;
      this._toastMessage("Benutzer entfernt.");
      this._restoreFocus();
      if (!applied) {
        await this._loadUsers();
      }
    } catch (error) {
      this._usersBusy = false;
      if (isAbortError(error)) {
        return;
      }
      this._deleteDialog = { ...this._deleteDialog, busy: false, error: this._errorText(error) };
    }
  }

  /* --------------------------------------------------------------------- */
  /* Einstellungen (KI, Lagerstätten, Speicherort)                         */
  /* --------------------------------------------------------------------- */

  _applySettingsPayload(data) {
    const normalized = normalizeSettingsPayload(data);
    this._features = normalized.features ?? this._features;
    this._ensureValidTab();
    this._ai = {
      ...this._ai,
      loading: false,
      loaded: true,
      form: normalized.form,
      apiKeySet: normalized.apiKeySet,
    };
    this._locations = {
      ...this._locations,
      loading: false,
      loaded: true,
      items: normalized.locations.map((entry) => ({ ...entry, originalName: entry.name })),
    };
    const storage = normalized.storage;
    this._storage = {
      ...this._storage,
      loading: false,
      loaded: Boolean(storage.dataPath || storage.choices.length > 0),
      dataPath: storage.dataPath,
      draftPath: storage.dataPath,
      effectivePath: storage.effectivePath,
      backupIncluded: storage.backupIncluded,
      excludeFromBackup: storage.excludeFromBackup,
      paths: storage.paths,
      choices: storage.choices,
      usage: storage.usage,
    };
    const images = normalized.images;
    this._images = {
      ...this._images,
      loading: false,
      loaded: true,
      maxBytes: images.maxBytes,
      draftKb: String(Math.round(images.maxBytes / 1024)),
    };
  }

  async _loadSettings() {
    if (!this._can("app_admin") || this._ai.loading) {
      return;
    }
    this._ai = { ...this._ai, loading: true, error: "", notice: "" };
    this._locations = { ...this._locations, loading: true, error: "", notice: "" };
    this._storage = { ...this._storage, loading: true, error: "", notice: "" };
    this._images = { ...this._images, loading: true, error: "", notice: "" };
    try {
      const data = await this._api(`${API_ROOT}/settings`);
      this._applySettingsPayload(data);
    } catch (error) {
      if (isAbortError(error)) {
        this._ai = { ...this._ai, loading: false };
        this._locations = { ...this._locations, loading: false };
        this._storage = { ...this._storage, loading: false };
        this._images = { ...this._images, loading: false };
        return;
      }
      const message = this._errorText(error);
      this._ai = { ...this._ai, loading: false, error: message };
      this._locations = { ...this._locations, loading: false, error: message };
      this._storage = { ...this._storage, loading: false, error: message };
      this._images = { ...this._images, loading: false, error: message };
    }
  }

  _setAiField(field, value) {
    this._ai = {
      ...this._ai,
      notice: "",
      error: "",
      form: { ...this._ai.form, [field]: value },
    };
  }

  async _saveSettings(event) {
    event?.preventDefault();
    if (!this._can("app_admin") || this._ai.saving) {
      return;
    }
    const form = this._ai.form;
    if (!form.endpoint.trim()) {
      this._ai = { ...this._ai, error: "Bitte einen KI-Endpunkt angeben.", notice: "" };
      return;
    }

    const ai = {
      endpoint: form.endpoint.trim(),
      model: form.model.trim(),
      prompt: form.prompt,
    };
    const key = form.api_key.trim();
    if (key) {
      ai.api_key = key;
    }

    this._ai = { ...this._ai, saving: true, error: "", notice: "" };
    try {
      const data = await this._api(`${API_ROOT}/settings`, { method: "PATCH", body: { ai } });
      const hasPayload = Boolean(data && typeof data === "object" && (data.ai || "endpoint" in data));
      if (hasPayload) {
        this._applySettingsPayload(data);
        this._ai = { ...this._ai, saving: false, notice: "Einstellungen gespeichert." };
      } else {
        this._ai = {
          ...this._ai,
          saving: false,
          loaded: true,
          notice: "Einstellungen gespeichert.",
          form: { ...this._ai.form, api_key: "" },
          apiKeySet: this._ai.apiKeySet || Boolean(key),
        };
      }
    } catch (error) {
      if (isAbortError(error)) {
        this._ai = { ...this._ai, saving: false };
        return;
      }
      this._ai = { ...this._ai, saving: false, error: this._errorText(error) };
    }
  }

  _setImageMaxKb(value) {
    this._images = { ...this._images, draftKb: value, error: "", notice: "" };
  }

  _validateImageMaxKb(value) {
    const text = String(value ?? "").trim().replace(",", ".");
    if (!text) {
      return "Bitte eine Maximalgröße in kB angeben.";
    }
    const kb = Number(text);
    if (!Number.isFinite(kb) || !Number.isInteger(kb)) {
      return "Bitte eine ganze Zahl in kB angeben.";
    }
    if (kb < IMAGE_MAX_KB_MIN || kb > IMAGE_MAX_KB_MAX) {
      return `Die Maximalgröße muss zwischen ${IMAGE_MAX_KB_MIN} und ${IMAGE_MAX_KB_MAX} kB liegen.`;
    }
    return "";
  }

  async _saveImages(event) {
    event?.preventDefault();
    const state = this._images;
    if (!this._can("app_admin") || state.saving || state.loading) {
      return;
    }
    if (!state.loaded) {
      this._images = {
        ...state,
        error: "Die Einstellungen sind noch nicht geladen. Bitte „Neu laden“ wählen.",
        notice: "",
      };
      return;
    }
    const validationError = this._validateImageMaxKb(state.draftKb);
    if (validationError) {
      this._images = { ...state, error: validationError, notice: "" };
      return;
    }

    const maxBytes = Number(String(state.draftKb).trim().replace(",", ".")) * 1024;
    this._images = { ...state, saving: true, error: "", notice: "" };
    try {
      const data = await this._api(`${API_ROOT}/settings`, {
        method: "PATCH",
        body: { images: { max_bytes: maxBytes } },
      });
      // Nur ein vollständiges Einstellungs-Payload ersetzt die übrigen Bereiche.
      if (isObject(data) && isObject(data.storage)) {
        this._applySettingsPayload(data);
      }
      const confirmed = isObject(data) && isObject(data.images);
      const appliedBytes = confirmed ? normalizeImages(data.images).maxBytes : maxBytes;
      this._images = {
        ...this._images,
        saving: false,
        loaded: true,
        maxBytes: appliedBytes,
        draftKb: String(Math.round(appliedBytes / 1024)),
        notice: "Bildgröße gespeichert.",
      };
    } catch (error) {
      if (isAbortError(error)) {
        this._images = { ...this._images, saving: false };
        return;
      }
      this._images = { ...this._images, saving: false, error: this._errorText(error) };
    }
  }

  _setLocationName(index, value) {
    const items = this._locations.items.map((item, position) =>
      position === index ? { ...item, name: value } : item,
    );
    this._locations = { ...this._locations, items, error: "", notice: "" };
  }

  _addLocation() {
    const items = [
      ...this._locations.items,
      { id: "", name: "", position: this._locations.items.length, stockTotal: 0, productsWithStock: 0, originalName: "" },
    ];
    this._locations = { ...this._locations, items, error: "", notice: "" };
    this.updateComplete
      .then(() => {
        this.renderRoot?.querySelector(`#fd-location-name-${items.length - 1}`)?.focus?.();
      })
      .catch(() => {});
  }

  _removeLocation(index) {
    const removed = this._locations.items[index];
    const items = this._locations.items.filter((_, position) => position !== index);
    const notice = removed?.id
      ? `„${removed.name}“ wird beim Speichern gelöscht, sofern dort kein Bestand gebucht ist.`
      : "";
    this._locations = { ...this._locations, items, error: "", notice };
  }

  _validateLocations(items) {
    if (items.length === 0) {
      return "Mindestens eine Lagerstätte ist erforderlich.";
    }
    const seen = new Set();
    for (const item of items) {
      const name = item.name.trim();
      if (!name) {
        return "Bitte für jede Lagerstätte einen Namen angeben.";
      }
      if (name.length > 60) {
        return "Ein Lagerstätten-Name darf höchstens 60 Zeichen enthalten.";
      }
      const key = name.toLocaleLowerCase("de");
      if (seen.has(key)) {
        return `Der Name „${name}“ wird mehrfach verwendet.`;
      }
      seen.add(key);
    }
    return "";
  }

  async _saveLocations(event) {
    event?.preventDefault();
    if (!this._can("app_admin") || this._locations.saving || this._locations.loading) {
      return;
    }
    if (!this._locations.loaded) {
      this._locations = {
        ...this._locations,
        error: "Die Einstellungen sind noch nicht geladen. Bitte „Neu laden“ wählen.",
        notice: "",
      };
      return;
    }
    const items = this._locations.items;
    const validationError = this._validateLocations(items);
    if (validationError) {
      this._locations = { ...this._locations, error: validationError, notice: "" };
      return;
    }

    const payload = {
      locations: items.map((item) => {
        const name = item.name.trim();
        return item.id ? { id: item.id, name } : { name };
      }),
    };

    this._locations = { ...this._locations, saving: true, error: "", notice: "" };
    try {
      const data = await this._api(`${API_ROOT}/settings`, { method: "PATCH", body: payload });
      if (Array.isArray(data?.locations)) {
        this._locations = {
          ...this._locations,
          saving: false,
          loaded: true,
          items: normalizeLocations(data.locations).map((entry) => ({ ...entry, originalName: entry.name })),
          notice: "Lagerstätten gespeichert.",
        };
      } else {
        await this._loadSettings();
        this._locations = { ...this._locations, saving: false, notice: "Lagerstätten gespeichert." };
      }
    } catch (error) {
      if (isAbortError(error)) {
        this._locations = { ...this._locations, saving: false };
        return;
      }
      this._locations = { ...this._locations, saving: false, error: this._errorText(error) };
    }
  }

  _storageDraftChoice() {
    const state = this._storage;
    return state.choices.find((entry) => entry.id === state.draftPath) || null;
  }

  _storageDraftBackupIncluded() {
    const choice = this._storageDraftChoice();
    return choice ? choice.backupIncluded : this._storage.draftPath === "config";
  }

  _setStoragePath(path) {
    if (this._storage.saving || !this._storage.choices.some((entry) => entry.id === path)) {
      return;
    }
    this._storage = {
      ...this._storage,
      draftPath: path,
      error: "",
      notice: "",
      reloadRequired: false,
    };
  }

  _setStorageBackup(enabled) {
    const state = this._storage;
    if (state.saving || state.loading || !state.loaded) {
      return;
    }
    if (enabled === this._storageDraftBackupIncluded()) {
      return;
    }
    const target = enabled
      ? state.choices.find((entry) => entry.backupIncluded)
      : state.choices.find((entry) => !entry.backupIncluded);
    if (!target) {
      this._storage = {
        ...state,
        error: enabled
          ? "Es steht kein Speicherort im Backup zur Verfügung."
          : "Es steht kein Speicherort außerhalb des Backups zur Verfügung.",
        notice: "",
        reloadRequired: false,
      };
      return;
    }
    this._setStoragePath(target.id);
  }

  async _saveStorage(event) {
    event?.preventDefault();
    const state = this._storage;
    if (!this._can("app_admin") || state.saving || state.loading) {
      return;
    }
    if (!state.loaded) {
      this._storage = {
        ...state,
        error: "Die Einstellungen sind noch nicht geladen. Bitte „Neu laden“ wählen.",
        notice: "",
      };
      return;
    }
    if (!state.draftPath || state.draftPath === state.dataPath) {
      this._storage = {
        ...state,
        error: "",
        notice: "Der Speicherort ist unverändert.",
        reloadRequired: false,
      };
      return;
    }

    this._storage = { ...state, saving: true, error: "", notice: "", reloadRequired: false };
    try {
      const data = await this._api(`${API_ROOT}/settings`, {
        method: "PATCH",
        body: { storage: { data_path: state.draftPath } },
      });
      const reloadRequired = Boolean(data?.reload_required);
      if (data && typeof data === "object" && data.storage) {
        this._applySettingsPayload(data);
      }
      this._storage = {
        ...this._storage,
        saving: false,
        loaded: true,
        reloadRequired,
        notice: reloadRequired
          ? "Speicherort gespeichert. Lade die Integration neu, damit der neue Ordner verwendet wird."
          : "Speicherort gespeichert.",
      };
    } catch (error) {
      if (isAbortError(error)) {
        this._storage = { ...this._storage, saving: false };
        return;
      }
      this._storage = {
        ...this._storage,
        saving: false,
        // Auswahl zurückrollen: gespeicherten Speicherort wieder anzeigen.
        draftPath: this._storage.dataPath,
        error: this._errorText(error),
        notice: "",
        reloadRequired: false,
      };
    }
  }

  /* --------------------------------------------------------------------- */
  /* Render: Zugriff und Rahmen                                             */
  /* --------------------------------------------------------------------- */

  render() {
    return html`
      ${this._renderAccess()} ${this._renderRoleDialog()} ${this._renderDeleteDialog()}
      ${this._renderToast()}
    `;
  }

  _renderAccess() {
    if (this._accessState === "ready") {
      return this._renderMain();
    }
    if (this._accessState === "denied") {
      return this._renderDenied();
    }
    if (this._accessState === "error") {
      return this._renderAccessError();
    }
    return this._renderBoot();
  }

  _renderBoot() {
    return html`
      <div class="fd-shell">
        <section class="fd-card fd-state" role="status" aria-live="polite">
          <span class="fd-spinner" aria-hidden="true"></span>
          <h1 id="fd-config-heading" tabindex="-1">Frickeldave Store – Konfiguration</h1>
          <p class="fd-muted">
            ${this.hass ? "Zugriff wird geprüft …" : "Verbindung zu Home Assistant wird hergestellt …"}
          </p>
        </section>
      </div>
    `;
  }

  _renderDenied() {
    return html`
      <div class="fd-shell">
        <section class="fd-card fd-state" role="status">
          <h1 id="fd-config-heading" tabindex="-1">Keine Berechtigung</h1>
          <p>Diese Konfiguration ist der Anwendungsverwaltung vorbehalten.</p>
          <p class="fd-muted">${this._accessError || "Bitte wenden Sie sich an die Anwendungsverwaltung."}</p>
        </section>
      </div>
    `;
  }

  _renderAccessError() {
    return html`
      <div class="fd-shell">
        <section class="fd-card fd-state" role="alert">
          <h1 id="fd-config-heading" tabindex="-1">Verbindung fehlgeschlagen</h1>
          <p>${this._accessError}</p>
          <button type="button" class="fd-button fd-button--primary" @click=${() => this._retryAccess()}>
            Erneut versuchen
          </button>
        </section>
      </div>
    `;
  }

  _renderMain() {
    const publishHint = this._publishHint();
    return html`
      <div class="fd-app">
        <header class="fd-app-bar">
          <button
            type="button"
            class="fd-bar-icon"
            @click=${() => this._goBack()}
            ?disabled=${!this._canGoBack()}
            aria-label="Zurück"
            title="Zurück"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
              <path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z" />
            </svg>
          </button>
          <button
            type="button"
            class="fd-bar-icon"
            @click=${() => this._goForward()}
            ?disabled=${!this._forwardAvailable}
            aria-label="Vorwärts"
            title="Vorwärts"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
              <path d="M12 4l-1.41 1.41L16.17 11H4v2h12.17l-5.58 5.59L12 20l8-8z" />
            </svg>
          </button>
          <h1 id="fd-config-heading" class="fd-bar-title" tabindex="-1">Frickeldave Store</h1>
        </header>

        <div class="fd-shell">
          <div class="fd-tabs" role="tablist" aria-label="Konfigurationsbereiche">
            ${this._visibleTabs().map((tab) => {
              const active = this._tab === tab.id;
              return html`
                <button
                  id="fd-config-tab-${tab.id}"
                  type="button"
                  class="fd-tab"
                  role="tab"
                  aria-selected=${active ? "true" : "false"}
                  aria-controls=${tab.panelId}
                  tabindex=${active ? "0" : "-1"}
                  @click=${() => this._selectTab(tab.id)}
                  @keydown=${(event) => this._onTabKeydown(event)}
                >
                  ${tab.label}
                </button>
              `;
            })}
          </div>

          ${publishHint
            ? html`
                <div class="fd-banner fd-config-feature-hint">
                  <div>
                    <strong>Veröffentlichen</strong>
                    <p class="fd-hint">${publishHint}</p>
                  </div>
                </div>
              `
            : ""}

          ${this._tab === "general"
            ? this._renderGeneralTab()
            : this._tab === "users"
              ? this._renderUsersTab()
              : this._tab === "locations"
                ? this._renderLocationsTab()
                : this._renderAiTab()}
          ${this._me?.version
            ? html`<footer class="fd-config-footer" aria-label="Geladene Panel-Version">
                v${this._me.version}
              </footer>`
            : ""}
        </div>
      </div>
    `;
  }

  /* --------------------------------------------------------------------- */
  /* Render: Allgemeine Einstellungen                                      */
  /* --------------------------------------------------------------------- */

  _renderGeneralTab() {
    const state = this._storage;
    const busy = state.loading || state.saving || !state.loaded;
    const draftChoice = this._storageDraftChoice();
    const backupIncluded = this._storageDraftBackupIncluded();
    const changed = Boolean(state.draftPath) && state.draftPath !== state.dataPath;
    const images = this._images;
    const imagesBusy = images.loading || images.saving || !images.loaded;
    const imagesChanged =
      String(images.draftKb).trim() !== String(Math.round(images.maxBytes / 1024));

    return html`
      <section
        id="fd-config-panel-general"
        role="tabpanel"
        aria-labelledby="fd-config-tab-general"
        class="fd-card fd-config-section"
      >
        <div class="fd-config-toolbar">
          <div>
            <h2>Allgemeine Einstellungen</h2>
            <p class="fd-muted fd-small">
              Speicherort des Produktkatalogs, Aufnahme in das Home-Assistant-Backup und
              Maximalgröße für Bilder.
            </p>
          </div>
          <button
            type="button"
            class="fd-button"
            @click=${() => this._loadSettings()}
            ?disabled=${state.loading || state.saving || images.loading || images.saving}
          >
            Neu laden
          </button>
        </div>

        <div class="fd-live" role="status" aria-live="polite">
          ${state.loading ? "Einstellungen werden geladen …" : ""}
        </div>
        ${state.error ? html`<div class="fd-banner fd-banner--error" role="alert">${state.error}</div>` : ""}
        ${state.notice && !state.reloadRequired
          ? html`<div class="fd-banner fd-banner--success" role="status">${state.notice}</div>`
          : ""}
        ${state.reloadRequired
          ? html`<div class="fd-banner fd-banner--warning" role="alert">
              <p>
                ${state.notice ||
                "Speicherort gespeichert. Lade die Integration neu, damit der neue Ordner verwendet wird."}
              </p>
              <p class="fd-small">
                Einstellungen → Geräte &amp; Dienste → Frickeldave Store → Neu laden.
              </p>
            </div>`
          : ""}

        ${!state.loaded
          ? html`<p class="fd-muted">Die Einstellungen sind noch nicht geladen.</p>`
          : html`
              <form class="fd-storage-form" @submit=${(event) => this._saveStorage(event)} novalidate>
                <fieldset class="fd-fieldset" ?disabled=${busy}>
                  <legend>Backup</legend>
                  <label class="fd-switch">
                    <input
                      type="checkbox"
                      .checked=${backupIncluded}
                      ?disabled=${busy}
                      @change=${(event) => this._setStorageBackup(event.target.checked)}
                    />
                    <span>Produktkatalog im Home-Assistant-Backup enthalten</span>
                  </label>
                  <p class="fd-hint">
                    ${backupIncluded
                      ? "Der Speicherort „config“ liegt im Home-Assistant-Backup. Der Katalog wird dort mitgesichert."
                      : "Der Speicherort liegt unter „share“ oder „media“. Diese Ordner sind nicht Teil des Home-Assistant-Backups. Für ein Backup den Speicherort „config“ wählen."}
                  </p>
                </fieldset>

                <div class="fd-field">
                  <label class="fd-label" for="fd-storage-path">Speicherort</label>
                  <select
                    id="fd-storage-path"
                    class="fd-input fd-select"
                    .value=${state.draftPath}
                    ?disabled=${busy}
                    @change=${(event) => this._setStoragePath(event.target.value)}
                  >
                    ${state.choices.length === 0
                      ? html`<option value=${state.draftPath}>
                          ${state.draftPath || "Keine Auswahl verfügbar"}
                        </option>`
                      : state.choices.map(
                          (choice) => html`
                            <option value=${choice.id} ?selected=${choice.id === state.draftPath}>
                              ${choice.label}${choice.backupIncluded ? " (im Backup)" : ""}
                            </option>
                          `,
                        )}
                  </select>
                  <p class="fd-hint">
                    ${draftChoice?.description ||
                    "Der Ordner, in dem Metadaten, Bilder und Snapshots liegen."}
                  </p>
                </div>

                ${state.usage ? this._renderStorageUsage(state.usage) : ""}

                <dl class="fd-storage-meta" aria-label="Pfade des Speicherorts">
                  <div>
                    <dt>Aktiver Ordner</dt>
                    <dd><code>${state.effectivePath || "—"}</code></dd>
                  </div>
                  ${state.paths.map(
                    (entry) => html`
                      <div>
                        <dt>${entry.label}</dt>
                        <dd><code>${entry.path}</code></dd>
                      </div>
                    `,
                  )}
                </dl>
                <p class="fd-hint">
                  Diese Pfade sind schreibgeschützt und ergeben sich aus dem Speicherort.
                </p>

                <div class="fd-inline-actions">
                  <button
                    type="submit"
                    class="fd-button fd-button--primary"
                    ?disabled=${busy || !changed}
                    aria-busy=${state.saving ? "true" : "false"}
                  >
                    ${state.saving ? "Speichern …" : "Einstellungen speichern"}
                  </button>
                  ${changed
                    ? html`<button
                        type="button"
                        class="fd-button"
                        @click=${() => this._setStoragePath(state.dataPath)}
                        ?disabled=${busy}
                      >
                        Änderung verwerfen
                      </button>`
                    : ""}
                  ${state.loaded
                    ? html`<span class="fd-status ${state.error ? "fd-status--warn" : "fd-status--ok"}">
                        ${state.error ? "Nicht gespeichert" : "Konfiguration geladen"}
                      </span>`
                    : ""}
                </div>
              </form>

              <form class="fd-image-form" @submit=${(event) => this._saveImages(event)} novalidate>
                <fieldset class="fd-fieldset" ?disabled=${imagesBusy}>
                  <legend>Bilder</legend>
                  <div class="fd-field">
                    <label class="fd-label" for="fd-image-max-kb">Maximalgröße (kB)</label>
                    <input
                      id="fd-image-max-kb"
                      class="fd-input fd-input--number"
                      type="number"
                      inputmode="numeric"
                      min=${IMAGE_MAX_KB_MIN}
                      max=${IMAGE_MAX_KB_MAX}
                      step="1"
                      .value=${images.draftKb}
                      ?disabled=${imagesBusy}
                      aria-invalid=${images.error ? "true" : "false"}
                      @input=${(event) => this._setImageMaxKb(event.target.value)}
                    />
                    <p class="fd-hint">
                      Ab dieser Dateigröße gilt ein Bild als zu groß; das Panel bietet dann das
                      Verkleinern an.
                    </p>
                  </div>
                  ${images.error
                    ? html`<div class="fd-banner fd-banner--error" role="alert">${images.error}</div>`
                    : ""}
                  ${images.notice
                    ? html`<div class="fd-banner fd-banner--success" role="status">
                        ${images.notice}
                      </div>`
                    : ""}
                  <div class="fd-inline-actions">
                    <button
                      type="submit"
                      class="fd-button fd-button--primary"
                      ?disabled=${imagesBusy || !imagesChanged}
                      aria-busy=${images.saving ? "true" : "false"}
                    >
                      ${images.saving ? "Speichern …" : "Bildgröße speichern"}
                    </button>
                  </div>
                </fieldset>
              </form>
            `}
      </section>
    `;
  }

  _renderStorageUsage(usage) {
    return html`
      <p class="fd-storage-usage">
        <span>Belegt: <strong>${formatBytes(usage.totalBytes)}</strong></span>
        <span>Bilder ${formatBytes(usage.imagesBytes)}</span>
        <span>Metadaten ${formatBytes(usage.dataBytes)}</span>
        <span>Snapshots ${formatBytes(usage.backupsBytes)}</span>
        ${usage.files > 0
          ? html`<span class="fd-muted fd-small">${formatNumber(usage.files)} Dateien</span>`
          : ""}
      </p>
    `;
  }

  /* --------------------------------------------------------------------- */
  /* Render: Benutzerverwaltung                                             */
  /* --------------------------------------------------------------------- */

  _renderUsersTab() {
    return html`
      <section
        id="fd-config-panel-users"
        role="tabpanel"
        aria-labelledby="fd-config-tab-users"
        class="fd-card fd-config-section"
      >
        <div class="fd-config-toolbar">
          <div>
            <h2>Benutzerverwaltung</h2>
            <p class="fd-muted fd-small">
              Freigegebene Home-Assistant-Benutzer und ihre Rollen im Frickeldave Store.
            </p>
          </div>
          <button
            type="button"
            class="fd-button fd-button--primary"
            @click=${() => this._openAddDialog()}
            ?disabled=${this._usersLoading || this._usersBusy}
          >
            Benutzer hinzufügen
          </button>
        </div>

        <div class="fd-live" role="status" aria-live="polite">
          ${this._usersLoading ? "Benutzer werden geladen …" : ""}
        </div>
        ${this._usersError
          ? html`<div class="fd-banner fd-banner--error" role="alert">
              <span>${this._usersError}</span>
              <button type="button" class="fd-button fd-button--small" @click=${() => this._loadUsers()} ?disabled=${this._usersLoading}>
                Neu laden
              </button>
            </div>`
          : ""}

        ${this._renderUsersList()}
      </section>
    `;
  }

  _renderUsersList() {
    if (!this._usersLoading && !this._usersError && this._data.users.length === 0) {
      return html`
        <div class="fd-empty-state">
          <h3>Noch keine Benutzer freigegeben</h3>
          <p class="fd-muted fd-small">
            Fügen Sie über „Benutzer hinzufügen“ einen Home-Assistant-Benutzer hinzu.
          </p>
        </div>
      `;
    }

    return html`
      <ul class="fd-user-list" aria-label="Freigegebene Benutzer">
        ${this._data.users.map((user) => this._renderUserRow(user))}
      </ul>
    `;
  }

  _renderUserRow(user) {
    const displayName = user.name || user.userId;
    const labels = this._userRoleLabels(user);
    return html`
      <li class="fd-user-row">
        <div class="fd-user-identity">
          <span class="fd-user-name">${displayName}</span>
          ${user.name && user.name !== user.userId
            ? html`<span class="fd-user-id">${user.userId}</span>`
            : ""}
        </div>
        <div class="fd-user-roles">
          ${labels.length > 0
            ? labels.map((label) => html`<span class="fd-chip fd-chip--static">${label}</span>`)
            : html`<span class="fd-muted fd-small">Keine Rollen</span>`}
        </div>
        <div class="fd-user-actions">
          <button
            type="button"
            class="fd-icon-button"
            aria-label="Rollen bearbeiten"
            title="Rollen bearbeiten"
            @click=${() => this._openEditDialog(user)}
            ?disabled=${this._usersBusy}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
              <path
                d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z"
                fill="none"
                stroke="currentColor"
                stroke-width="1.8"
              />
              <path
                d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1 1.55V21a2 2 0 1 1-4 0v-.09a1.7 1.7 0 0 0-1.11-1.55 1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.55-1H3a2 2 0 1 1 0-4h.09A1.7 1.7 0 0 0 4.64 8.9a1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.7 1.7 0 0 0 1.87.34H9a1.7 1.7 0 0 0 1-1.55V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1 1.55 1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.87V9a1.7 1.7 0 0 0 1.55 1H21a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.55 1Z"
                fill="none"
                stroke="currentColor"
                stroke-width="1.4"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
          </button>
          <button
            type="button"
            class="fd-icon-button fd-icon-button--danger"
            aria-label="Benutzer entfernen"
            title="Benutzer entfernen"
            @click=${() => this._openDeleteDialog(user)}
            ?disabled=${this._usersBusy}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
              <path
                d="M4 7h16M9 7V4.5A1.5 1.5 0 0 1 10.5 3h3A1.5 1.5 0 0 1 15 4.5V7m-8 0 1 12.5A1.5 1.5 0 0 0 9.5 21h5a1.5 1.5 0 0 0 1.5-1.5L17 7M10 11v6m4-6v6"
                fill="none"
                stroke="currentColor"
                stroke-width="1.8"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
          </button>
        </div>
      </li>
    `;
  }

  /* --------------------------------------------------------------------- */
  /* Render: Rollen-Dialog                                                  */
  /* --------------------------------------------------------------------- */

  _renderRoleDialog() {
    const dialog = this._roleDialog;
    if (!dialog) {
      return "";
    }
    const isAdd = dialog.mode === "add";
    const availableUsers = isAdd ? this._filteredAvailableUsers() : [];
    const hasAvailable = this._data.availableUsers.some(
      (user) => !this._data.users.some((granted) => granted.userId === user.userId),
    );
    const title = isAdd ? "Benutzer hinzufügen" : "Rollen bearbeiten";
    const saveDisabled =
      dialog.busy || (isAdd && (availableUsers.length === 0 || !this._addUserSelection));

    return html`
      <dialog
        id="fd-role-dialog"
        class="fd-dialog${dialog.shake ? " fd-dialog--shake" : ""}"
        aria-labelledby="fd-role-dialog-title"
        @cancel=${(event) => this._handleDialogCancel(event, () => this._closeRoleDialog())}
        @click=${(event) => {
          if (event.target === event.currentTarget && !dialog.busy) {
            this._closeRoleDialog();
          }
        }}
      >
        <div class="fd-dialog-head">
          <button
            type="button"
            class="fd-icon-button fd-dialog-close"
            @click=${() => this._closeRoleDialog()}
            ?disabled=${dialog.busy}
            aria-label="Dialog schließen"
            title="Dialog schließen"
          >
            ✕
          </button>
          <div class="fd-dialog-title">
            <h2 id="fd-role-dialog-title">${title}</h2>
            <p class="fd-muted fd-small">
              ${isAdd
                ? "Home-Assistant-Benutzer freigeben und Rollen zuweisen."
                : "Rollen des freigegebenen Benutzers anpassen."}
            </p>
          </div>
        </div>

        <div class="fd-dialog-body">
          ${dialog.error
            ? html`<div class="fd-banner fd-banner--error" role="alert">${dialog.error}</div>`
            : ""}
          ${dialog.shake
            ? html`<p class="fd-hint" role="status" aria-live="polite">
                Nicht gespeicherte Änderungen. Mit „Abbrechen“ verwerfen.
              </p>`
            : ""}

          ${isAdd ? this._renderAddUserPicker(dialog, availableUsers, hasAvailable) : this._renderFixedUser(dialog)}

          <fieldset class="fd-fieldset fd-transfer" ?disabled=${dialog.busy}>
            <legend>Rollen</legend>
            <div class="fd-transfer-grid">
              <div class="fd-transfer-column">
                <label class="fd-label" for="fd-roles-available">Verfügbare Rollen</label>
                <select
                  id="fd-roles-available"
                  class="fd-input fd-multi-select"
                  multiple
                  size="6"
                  data-autofocus
                  aria-label="Verfügbare Rollen"
                  ?disabled=${dialog.busy}
                  @change=${(event) => this._onTransferSelection("left", event)}
                >
                  ${this._availableRoles().map(
                    (role) => html`<option value=${role.id}>${role.label || role.id}</option>`,
                  )}
                </select>
              </div>

              <div class="fd-transfer-actions">
                <button
                  type="button"
                  class="fd-button fd-button--small"
                  @click=${() => this._moveToAssigned()}
                  ?disabled=${dialog.busy || dialog.leftSelection.length === 0}
                >
                  Hinzufügen →
                </button>
                <button
                  type="button"
                  class="fd-button fd-button--small"
                  @click=${() => this._moveToAvailable()}
                  ?disabled=${dialog.busy || dialog.rightSelection.length === 0}
                >
                  ← Entfernen
                </button>
              </div>

              <div class="fd-transfer-column">
                <label class="fd-label" for="fd-roles-assigned">Zugewiesene Rollen</label>
                <select
                  id="fd-roles-assigned"
                  class="fd-input fd-multi-select"
                  multiple
                  size="6"
                  aria-label="Zugewiesene Rollen"
                  ?disabled=${dialog.busy}
                  @change=${(event) => this._onTransferSelection("right", event)}
                >
                  ${this._assignedRoles().map(
                    (role) => html`<option value=${role.id}>${role.label}</option>`,
                  )}
                </select>
              </div>
            </div>
            <p class="fd-hint">
              Rollen links auswählen und mit „Hinzufügen →“ zuweisen; rechts auswählen und mit
              „← Entfernen“ zurücknehmen.
            </p>
          </fieldset>
        </div>

        <div class="fd-dialog-foot">
          <button
            type="button"
            class="fd-button fd-button--plain"
            @click=${() => this._discardRoleDialog()}
            ?disabled=${dialog.busy}
          >
            Abbrechen
          </button>
          <button
            type="button"
            class="fd-button fd-button--primary"
            @click=${() => this._saveRoleDialog()}
            ?disabled=${saveDisabled}
            aria-busy=${dialog.busy ? "true" : "false"}
          >
            ${dialog.busy ? "Speichern …" : "Speichern"}
          </button>
        </div>
      </dialog>
    `;
  }

  _renderAddUserPicker(dialog, availableUsers, hasAvailable) {
    if (!hasAvailable) {
      return html`
        <div class="fd-banner fd-banner--warning" role="status">
          Alle Home-Assistant-Benutzer sind bereits für den Frickeldave Store freigegeben.
        </div>
      `;
    }
    return html`
      <div class="fd-field">
        <label class="fd-label" for="fd-add-user-filter">Benutzer suchen</label>
        <input
          id="fd-add-user-filter"
          class="fd-input"
          type="search"
          placeholder="Name oder Benutzer-ID"
          .value=${this._addUserFilter}
          @input=${(event) => this._onAddFilterInput(event.target.value)}
          ?disabled=${dialog.busy}
        />
      </div>
      <div class="fd-field">
        <label class="fd-label" for="fd-add-user-select">
          Home-Assistant-Benutzer <span class="fd-required" aria-hidden="true">*</span>
        </label>
        <select
          id="fd-add-user-select"
          class="fd-input fd-select"
          size="6"
          data-autofocus
          required
          aria-required="true"
          ?disabled=${dialog.busy}
          @change=${(event) => this._onAddUserChange(event)}
        >
          ${availableUsers.length === 0
            ? html`<option value="" disabled>Keine passenden Benutzer gefunden</option>`
            : availableUsers.map(
                (user) => html`<option
                  value=${user.userId}
                  ?selected=${user.userId === this._addUserSelection}
                >
                  ${user.name || user.userId}${user.name ? ` (${user.userId})` : ""}
                </option>`,
              )}
        </select>
        ${availableUsers.length === 0
          ? html`<p class="fd-hint">Kein Benutzer passt zum Suchbegriff.</p>`
          : ""}
      </div>
    `;
  }

  _renderFixedUser(dialog) {
    return html`
      <div class="fd-field">
        <span class="fd-label">Benutzer</span>
        <div class="fd-fixed-user">
          <span class="fd-user-name">${dialog.name}</span>
          <span class="fd-user-id">${dialog.userId}</span>
        </div>
      </div>
    `;
  }

  /* --------------------------------------------------------------------- */
  /* Render: Entfernen-Dialog                                               */
  /* --------------------------------------------------------------------- */

  _renderDeleteDialog() {
    const dialog = this._deleteDialog;
    if (!dialog) {
      return "";
    }
    return html`
      <dialog
        id="fd-delete-dialog"
        class="fd-dialog fd-dialog--narrow"
        aria-labelledby="fd-delete-dialog-title"
        aria-describedby="fd-delete-dialog-description"
        @cancel=${(event) => this._handleDialogCancel(event, () => this._closeDeleteDialog())}
        @click=${(event) => {
          if (event.target === event.currentTarget && !dialog.busy) {
            this._closeDeleteDialog();
          }
        }}
      >
        <div class="fd-dialog-head">
          <div class="fd-dialog-title">
            <h2 id="fd-delete-dialog-title">Benutzer entfernen</h2>
            <p class="fd-muted fd-small">Die Freigabe kann jederzeit wieder hinzugefügt werden.</p>
          </div>
        </div>
        <div class="fd-dialog-body">
          ${dialog.error
            ? html`<div class="fd-banner fd-banner--error" role="alert">${dialog.error}</div>`
            : ""}
          <p id="fd-delete-dialog-description">
            Soll die Freigabe für <strong>${dialog.name}</strong> wirklich entfernt werden? Der
            Benutzer verliert damit den Zugriff auf den Frickeldave Store.
          </p>
        </div>
        <div class="fd-dialog-foot">
          <button
            type="button"
            class="fd-button fd-button--plain"
            data-autofocus
            @click=${() => this._closeDeleteDialog()}
            ?disabled=${dialog.busy}
          >
            Abbrechen
          </button>
          <button
            type="button"
            class="fd-button fd-button--danger"
            @click=${() => this._confirmDelete()}
            ?disabled=${dialog.busy}
            aria-busy=${dialog.busy ? "true" : "false"}
          >
            ${dialog.busy ? "Entfernen …" : "Entfernen"}
          </button>
        </div>
      </dialog>
    `;
  }

  /* --------------------------------------------------------------------- */
  /* Render: KI-Einstellungen                                               */
  /* --------------------------------------------------------------------- */

  _renderLocationsTab() {
    const state = this._locations;
    const busy = state.loading || state.saving || !state.loaded;

    return html`
      <section
        id="fd-config-panel-locations"
        role="tabpanel"
        aria-labelledby="fd-config-tab-locations"
        class="fd-card fd-config-section"
      >
        <div class="fd-config-toolbar">
          <div>
            <h2>Lagerstätten</h2>
            <p class="fd-muted fd-small">
              Namen und Reihenfolge der Bestände. Produktkacheln, Listenansicht, Filter und der
              Bestand-Tab im Produktdialog richten sich danach.
            </p>
          </div>
          <button type="button" class="fd-button" @click=${() => this._loadSettings()} ?disabled=${busy}>
            Neu laden
          </button>
        </div>

        <div class="fd-live" role="status" aria-live="polite">
          ${state.loading ? "Einstellungen werden geladen …" : ""}
        </div>
        ${state.error ? html`<div class="fd-banner fd-banner--error" role="alert">${state.error}</div>` : ""}
        ${state.notice ? html`<div class="fd-banner fd-banner--success" role="status">${state.notice}</div>` : ""}

        <form class="fd-locations-form" @submit=${(event) => this._saveLocations(event)} novalidate>
          <p class="fd-hint">
            Mindestens eine Lagerstätte, Namen eindeutig und nicht leer. Löschen wird beim Speichern
            geprüft; eine Lagerstätte mit Bestand wird mit deutscher Meldung abgelehnt, buche den
            Bestand zuerst um.
          </p>

          <ul class="fd-location-list" role="list" aria-label="Konfigurierte Lagerstätten">
            ${state.items.map((item, index) => html`
              <li class="fd-location-row">
                <div class="fd-field">
                  <label class="fd-label" for=${`fd-location-name-${index}`}>
                    Name der Lagerstätte ${index + 1}
                  </label>
                  <input
                    id=${`fd-location-name-${index}`}
                    class="fd-input"
                    type="text"
                    maxlength="60"
                    required
                    aria-required="true"
                    .value=${item.name}
                    ?disabled=${busy}
                    @input=${(event) => this._setLocationName(index, event.target.value)}
                  />
                  <p class="fd-hint">
                    ${!state.loaded
                      ? "Standardwert – wird nach dem Laden mit der gespeicherten Konfiguration abgeglichen."
                      : item.id
                        ? `${formatNumber(item.stockTotal)} Stück in ${formatNumber(item.productsWithStock)} Produkten`
                        : "Neue Lagerstätte – wird beim Speichern angelegt."}
                  </p>
                </div>
                <button
                  type="button"
                  class="fd-button fd-button--small fd-button--danger"
                  @click=${() => this._removeLocation(index)}
                  ?disabled=${busy || state.items.length <= 1}
                  aria-label=${`Lagerstätte ${item.name || index + 1} löschen`}
                  title=${state.items.length <= 1 ? "Mindestens eine Lagerstätte ist erforderlich." : "Löschen"}
                >
                  Löschen
                </button>
              </li>
            `)}
          </ul>

          <div class="fd-inline-actions">
            <button type="button" class="fd-button" @click=${() => this._addLocation()} ?disabled=${busy}>
              Lagerstätte hinzufügen
            </button>
            <button
              type="submit"
              class="fd-button fd-button--primary"
              ?disabled=${busy}
              aria-busy=${state.saving ? "true" : "false"}
            >
              ${state.saving ? "Speichern …" : "Lagerstätten speichern"}
            </button>
            ${state.loaded
              ? html`<span class="fd-status ${state.error ? "fd-status--warn" : "fd-status--ok"}">
                  ${state.error ? "Nicht gespeichert" : "Konfiguration geladen"}
                </span>`
              : ""}
          </div>
        </form>
      </section>
    `;
  }

  _renderAiTab() {
    const ai = this._ai;
    const keySet = ai.apiKeySet;
    return html`
      <section
        id="fd-config-panel-ai"
        role="tabpanel"
        aria-labelledby="fd-config-tab-ai"
        class="fd-card fd-config-section"
      >
        <div class="fd-config-toolbar">
          <div>
            <h2>KI-Einstellungen</h2>
            <p class="fd-muted fd-small">
              Für KI-Beschreibungsvorschläge im Produkteditor. Der API-Key wird nie angezeigt.
            </p>
          </div>
          <button type="button" class="fd-button" @click=${() => this._loadSettings()} ?disabled=${ai.loading || ai.saving}>
            Neu laden
          </button>
        </div>

        <div class="fd-live" role="status" aria-live="polite">
          ${ai.loading ? "Einstellungen werden geladen …" : ""}
        </div>
        ${ai.error ? html`<div class="fd-banner fd-banner--error" role="alert">${ai.error}</div>` : ""}
        ${ai.notice ? html`<div class="fd-banner fd-banner--success" role="status">${ai.notice}</div>` : ""}

        <form class="fd-form-grid" @submit=${(event) => this._saveSettings(event)} novalidate>
          <div class="fd-field fd-field--full">
            <label class="fd-label" for="fd-ai-endpoint">KI-Endpunkt</label>
            <input
              id="fd-ai-endpoint"
              class="fd-input"
              type="url"
              placeholder="https://api.example.com/v1/chat/completions"
              .value=${ai.form.endpoint}
              @input=${(event) => this._setAiField("endpoint", event.target.value)}
              ?disabled=${ai.loading || ai.saving}
            />
            <p class="fd-hint">Vollständige URL des Chat-Completion-Endpunkts.</p>
          </div>

          <div class="fd-field">
            <label class="fd-label" for="fd-ai-model">Modell</label>
            <input
              id="fd-ai-model"
              class="fd-input"
              type="text"
              placeholder="deepseek-flash"
              .value=${ai.form.model}
              @input=${(event) => this._setAiField("model", event.target.value)}
              ?disabled=${ai.loading || ai.saving}
            />
          </div>

          <div class="fd-field">
            <label class="fd-label" for="fd-ai-key">API-Key</label>
            <input
              id="fd-ai-key"
              class="fd-input"
              type="password"
              autocomplete="new-password"
              placeholder=${keySet ? "gespeichert" : "Noch kein Schlüssel gespeichert"}
              .value=${ai.form.api_key}
              @input=${(event) => this._setAiField("api_key", event.target.value)}
              ?disabled=${ai.loading || ai.saving}
            />
            <p class="fd-hint">
              ${keySet ? "Ein API-Key ist gespeichert." : "Noch kein API-Key gespeichert."}
              Leer lassen, um den gespeicherten Schlüssel beizubehalten.
            </p>
          </div>

          <div class="fd-field fd-field--full">
            <label class="fd-label" for="fd-ai-prompt">Prompt</label>
            <textarea
              id="fd-ai-prompt"
              class="fd-input fd-textarea"
              rows="6"
              .value=${ai.form.prompt}
              @input=${(event) => this._setAiField("prompt", event.target.value)}
              ?disabled=${ai.loading || ai.saving}
            ></textarea>
          </div>

          <div class="fd-field fd-field--full">
            <div class="fd-inline-actions">
              <button
                type="submit"
                class="fd-button fd-button--primary"
                ?disabled=${ai.saving || ai.loading}
                aria-busy=${ai.saving ? "true" : "false"}
              >
                ${ai.saving ? "Speichern …" : "Speichern"}
              </button>
              <span class="fd-status ${keySet ? "fd-status--ok" : "fd-status--warn"}">
                ${keySet ? "API-Key gespeichert" : "API-Key fehlt"}
              </span>
            </div>
          </div>
        </form>
      </section>
    `;
  }

  _renderToast() {
    return html`
      <div class="fd-toast-slot" role="status" aria-live="polite">
        ${this._toast ? html`<div class="fd-toast">${this._toast.message}</div>` : ""}
      </div>
    `;
  }

  /* --------------------------------------------------------------------- */
  /* Styles: HA-Design-Tokens plus eigene --fd-* Schicht                    */
  /* --------------------------------------------------------------------- */

  static styles = css`
    :host {
      display: block;
      min-height: 100%;
      color: var(--ha-color-text-primary, var(--primary-text-color, #212121));
      background-color: var(--ha-color-surface-lower, var(--primary-background-color, transparent));
      font-family: var(
        --ha-font-family-body,
        var(--paper-font-body1_-_font-family, Roboto, "Helvetica Neue", Arial, sans-serif)
      );
      font-size: var(--ha-font-size-m, 14px);
      line-height: var(--fd-line-height, 1.6);

      --fd-space-1: var(--ha-space-1, 4px);
      --fd-space-2: var(--ha-space-2, 8px);
      --fd-space-3: var(--ha-space-3, 12px);
      --fd-space-4: var(--ha-space-4, 16px);
      --fd-space-6: var(--ha-space-6, 24px);
      --fd-space-8: var(--ha-space-8, 32px);

      --fd-radius-lg: var(--ha-border-radius-lg, var(--ha-card-border-radius, 12px));
      --fd-radius: var(--ha-border-radius-md, 8px);
      --fd-radius-sm: var(--ha-border-radius-md, 8px);
      --fd-radius-pill: var(--ha-border-radius-pill, 999px);
      --fd-radius-dialog: var(--ha-border-radius-xl, 16px);

      --fd-surface: var(--ha-color-surface-default, var(--card-background-color, #ffffff));
      --fd-surface-alt: var(--ha-color-surface-low, var(--secondary-background-color, rgba(127, 127, 127, 0.08)));
      --fd-text: var(--ha-color-text-primary, var(--primary-text-color, #212121));
      --fd-text-muted: var(--ha-color-text-secondary, var(--secondary-text-color, #6b6b6b));
      --fd-divider: var(--ha-color-border-neutral-quiet, var(--divider-color, rgba(0, 0, 0, 0.12)));
      --fd-border-strong: var(--ha-color-border-neutral-normal, var(--fd-divider));
      --fd-accent: var(--ha-color-fill-primary-loud-resting, var(--primary-color, #03a9f4));
      --fd-accent-hover: var(--ha-color-fill-primary-loud-hover, var(--fd-accent));
      --fd-accent-active: var(--ha-color-fill-primary-loud-active, var(--fd-accent));
      --fd-on-accent: var(--ha-color-on-primary-loud, var(--text-primary-color, #ffffff));
      --fd-accent-soft: var(--ha-color-fill-primary-normal-resting, var(--fd-surface-alt));
      --fd-on-accent-soft: var(--ha-color-on-primary-normal, var(--fd-text));
      --fd-neutral-soft: var(--ha-color-fill-neutral-normal-resting, var(--fd-surface-alt));
      --fd-error: var(--ha-color-on-danger-quiet, var(--error-color, #c62828));
      --fd-error-fill: var(--ha-color-fill-danger-quiet-resting, var(--fd-surface-alt));
      --fd-error-border: var(--ha-color-border-danger-normal, var(--fd-error));
      --fd-error-strong: var(--ha-color-fill-danger-loud-resting, var(--fd-error));
      --fd-on-error-strong: var(--ha-color-on-danger-loud, #ffffff);
      --fd-warning: var(--ha-color-on-warning-quiet, var(--warning-color, #ff9800));
      --fd-warning-fill: var(--ha-color-fill-warning-quiet-resting, var(--fd-surface-alt));
      --fd-warning-border: var(--ha-color-border-warning-normal, var(--fd-warning));
      --fd-success: var(--ha-color-on-success-quiet, var(--success-color, #2e7d32));
      --fd-success-fill: var(--ha-color-fill-success-quiet-resting, var(--fd-surface-alt));
      --fd-success-border: var(--ha-color-border-success-normal, var(--fd-success));
      --fd-shadow: var(--ha-card-box-shadow, none);
      --fd-shadow-dialog: 0 12px 40px rgba(0, 0, 0, 0.35);

      --fd-font-small: var(--ha-font-size-s, 12px);
      --fd-focus: var(--ha-color-focus, var(--primary-color, #03a9f4));
      --fd-focus-ring: 2px solid var(--fd-focus);
      --fd-line-height: 1.6;
      --fd-line-height-heading: 1.2;
      --fd-duration-fast: var(--ha-animation-duration-fast, 150ms);
      --fd-duration-normal: var(--ha-animation-duration-normal, 250ms);
    }

    *,
    *::before,
    *::after {
      box-sizing: border-box;
    }

    h1,
    h2,
    h3 {
      line-height: var(--fd-line-height-heading, 1.2);
    }

    :where(button, input, select, textarea, [tabindex]):focus-visible {
      outline: var(--fd-focus-ring);
      outline-offset: 2px;
    }

    /* Layout ------------------------------------------------------------- */

    .fd-app {
      display: flex;
      flex-direction: column;
      min-height: 100%;
    }

    .fd-shell {
      display: flex;
      flex: 1;
      flex-direction: column;
      gap: var(--fd-space-4);
      width: 100%;
      max-width: 1100px;
      margin: 0 auto;
      padding: var(--fd-space-4);
    }

    .fd-config-footer {
      margin-top: auto;
      color: var(--fd-text-muted);
      font-size: var(--fd-font-small);
      text-align: end;
    }

    /* App-Leiste: volle Panel-Breite, HA-Metriken ------------------------- */

    .fd-app-bar {
      position: sticky;
      top: 0;
      z-index: 20;
      display: flex;
      flex: none;
      flex-wrap: nowrap;
      align-items: center;
      gap: 0;
      height: var(--header-height, 56px);
      padding: 0 var(--ha-space-3, 12px);
      background: var(--app-header-background-color, var(--primary-background-color));
      color: var(--app-header-text-color, var(--primary-text-color));
      border-bottom: var(--app-header-border-bottom, 1px solid var(--divider-color));
    }

    .fd-bar-title {
      flex: 1 1 auto;
      min-width: 0;
      margin: 0;
      padding-inline-start: var(--ha-space-6, 24px);
      overflow: hidden;
      font-size: var(--ha-font-size-xl, 1.5rem);
      font-weight: 400;
      line-height: var(--header-height, 56px);
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .fd-bar-icon {
      display: inline-flex;
      flex: none;
      align-items: center;
      justify-content: center;
      width: var(--ha-icon-button-size, 48px);
      height: var(--ha-icon-button-size, 48px);
      padding: 0;
      border: none;
      border-radius: 50%;
      background: transparent;
      color: inherit;
      cursor: pointer;
    }

    .fd-bar-icon:hover:not(:disabled) {
      background: var(--ha-color-fill-neutral-quiet-hover, var(--secondary-background-color, rgba(127, 127, 127, 0.16)));
    }

    .fd-bar-icon:disabled {
      opacity: 0.38;
      cursor: default;
    }

    .fd-bar-icon svg {
      display: block;
      width: 24px;
      height: 24px;
    }

    .fd-bar-icon svg path {
      fill: currentColor;
      stroke: none;
    }

    /* Tabs --------------------------------------------------------------- */

    .fd-tabs {
      display: flex;
      flex-wrap: wrap;
      gap: var(--fd-space-1);
      border-bottom: 1px solid var(--fd-divider);
    }

    .fd-tab {
      flex: 0 1 auto;
      min-height: 48px;
      padding: 8px 16px;
      border: none;
      border-bottom: 3px solid transparent;
      border-radius: 0;
      background: transparent;
      color: var(--fd-text-muted);
      font: inherit;
      font-weight: 500;
      cursor: pointer;
      transition:
        background-color var(--fd-duration-fast),
        border-color var(--fd-duration-fast),
        color var(--fd-duration-fast);
    }

    .fd-tab:hover {
      background: var(--fd-surface-alt);
      color: var(--fd-text);
    }

    .fd-tab[aria-selected="true"] {
      border-bottom-color: var(--fd-accent);
      color: var(--fd-accent);
    }

    /* Karten und Zustände ------------------------------------------------ */

    .fd-card {
      background: var(--fd-surface);
      border: var(--ha-card-border-width, 1px) solid var(--ha-card-border-color, var(--fd-divider));
      border-radius: var(--fd-radius-lg);
      box-shadow: var(--fd-shadow);
    }

    .fd-state {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: var(--fd-space-2);
      padding: var(--fd-space-8) var(--fd-space-4);
      text-align: center;
    }

    .fd-state h1 {
      margin: 0;
      font-weight: 500;
    }

    .fd-config-section {
      display: flex;
      flex-direction: column;
      gap: var(--fd-space-4);
      padding: var(--fd-space-4);
    }

    .fd-config-toolbar {
      display: flex;
      flex-wrap: wrap;
      align-items: flex-start;
      justify-content: space-between;
      gap: var(--fd-space-3);
    }

    .fd-config-toolbar h2 {
      margin: 0 0 4px;
      font-size: 1.1rem;
      font-weight: 500;
    }

    .fd-config-toolbar p {
      max-width: 60ch;
    }

    .fd-live {
      min-height: 0;
      color: var(--fd-text-muted);
      font-size: var(--fd-font-small);
    }

    /* Hinweis, wenn eine Fähigkeit von einer Erweiterung statt der Basis kommt. */
    .fd-config-feature-hint {
      margin-bottom: var(--fd-space-3);
    }

    .fd-config-feature-hint p {
      margin-top: var(--fd-space-1);
    }

    .fd-empty-state {
      padding: var(--fd-space-6);
      border: 1px dashed var(--fd-divider);
      border-radius: var(--fd-radius);
      text-align: center;
    }

    .fd-empty-state h3 {
      margin: 0 0 var(--fd-space-1);
      font-size: var(--ha-font-size-l, 16px);
      font-weight: 500;
    }

    .fd-empty-state p {
      margin: 0 0 var(--fd-space-1);
    }

    .fd-spinner {
      flex: none;
      width: 24px;
      height: 24px;
      border-radius: 50%;
      border: 3px solid var(--fd-divider);
      border-top-color: var(--fd-accent);
      animation: fd-spin 0.9s linear infinite;
    }

    @keyframes fd-spin {
      to {
        transform: rotate(360deg);
      }
    }

    /* Benutzerliste ------------------------------------------------------ */

    .fd-user-list {
      display: flex;
      flex-direction: column;
      gap: var(--fd-space-2);
      list-style: none;
      margin: 0;
      padding: var(--fd-space-2) 0;
    }

    .fd-user-row {
      display: grid;
      grid-template-columns: minmax(160px, 1.2fr) minmax(180px, 2fr) auto;
      align-items: center;
      gap: var(--fd-space-3);
      min-height: 64px;
      padding: var(--fd-space-2) var(--fd-space-3);
      border: 1px solid var(--fd-divider);
      border-radius: var(--fd-radius);
    }

    .fd-user-identity {
      display: flex;
      flex-direction: column;
      min-width: 0;
    }

    .fd-user-name {
      overflow: hidden;
      font-weight: 500;
      text-overflow: ellipsis;
    }

    .fd-user-id {
      color: var(--fd-text-muted);
      font-family: var(--ha-font-family-code, monospace);
      font-size: var(--fd-font-small);
    }

    .fd-user-roles {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      min-width: 0;
    }

    .fd-user-actions {
      display: flex;
      justify-content: flex-end;
      gap: 2px;
    }

    .fd-chip {
      display: inline-flex;
      align-items: center;
      min-height: 26px;
      padding: 2px 10px;
      border: 1px solid var(--fd-divider);
      border-radius: var(--fd-radius-pill);
      background: var(--fd-surface-alt);
      color: var(--fd-text);
      font-size: var(--fd-font-small);
      white-space: nowrap;
    }

    /* Formularelemente ---------------------------------------------------- */

    .fd-label {
      color: var(--fd-text-muted);
      font-size: var(--fd-font-small);
      font-weight: 500;
    }

    .fd-required {
      color: var(--fd-error);
    }

    .fd-hint {
      margin: 0;
      color: var(--fd-text-muted);
      font-size: var(--fd-font-small);
    }

    .fd-small {
      font-size: var(--fd-font-small);
    }

    .fd-muted {
      margin: 0;
      color: var(--fd-text-muted);
    }

    .fd-input {
      width: 100%;
      min-height: 44px;
      padding: 10px 12px;
      border: 1px solid var(--fd-border-strong);
      border-radius: var(--fd-radius-sm);
      background: var(--fd-surface);
      color: var(--fd-text);
      font: inherit;
      transition: border-color var(--fd-duration-fast);
    }

    .fd-input:focus-visible {
      border-color: var(--fd-accent);
    }

    .fd-input:disabled {
      background: var(--fd-surface-alt);
      color: var(--fd-text-muted);
      cursor: not-allowed;
    }

    .fd-select,
    .fd-multi-select {
      padding: 6px;
    }

    .fd-multi-select {
      min-height: 150px;
    }

    .fd-textarea {
      min-height: 120px;
      resize: vertical;
    }

    .fd-field {
      display: flex;
      flex-direction: column;
      gap: 6px;
      min-width: 0;
    }

    .fd-field--full {
      grid-column: 1 / -1;
    }

    .fd-form-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: var(--fd-space-4);
    }

    .fd-storage-form {
      display: flex;
      flex-direction: column;
      gap: var(--fd-space-4);
    }

    .fd-storage-form .fd-fieldset {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }

    .fd-switch {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-height: 48px;
      cursor: pointer;
    }

    .fd-switch input {
      width: 20px;
      height: 20px;
      accent-color: var(--fd-accent);
      cursor: pointer;
    }

    .fd-switch:has(input:disabled) {
      color: var(--fd-text-muted);
      cursor: not-allowed;
    }

    .fd-storage-meta {
      display: flex;
      flex-direction: column;
      gap: var(--fd-space-2);
      margin: 0;
      padding: var(--fd-space-3);
      border: 1px solid var(--fd-divider);
      border-radius: var(--fd-radius);
      background: var(--fd-surface-alt);
    }

    .fd-storage-meta div {
      display: flex;
      flex-wrap: wrap;
      gap: 4px 12px;
    }

    .fd-storage-meta dt {
      flex: 0 0 120px;
      color: var(--fd-text-muted);
      font-size: var(--fd-font-small);
    }

    .fd-storage-meta dd {
      min-width: 0;
      margin: 0;
    }

    .fd-storage-meta code {
      overflow-wrap: anywhere;
      font-family: var(--ha-font-family-code, ui-monospace, SFMono-Regular, monospace);
      font-size: var(--fd-font-small);
    }

    .fd-storage-usage {
      display: flex;
      flex-wrap: wrap;
      margin: 0;
      color: var(--fd-text);
      font-size: var(--fd-font-small);
    }

    .fd-storage-usage > span + span::before {
      content: "·";
      margin: 0 8px;
      color: var(--fd-text-muted);
    }

    .fd-image-form {
      display: flex;
      flex-direction: column;
      gap: var(--fd-space-3);
    }

    .fd-image-form .fd-fieldset {
      display: flex;
      flex-direction: column;
      gap: var(--fd-space-2);
    }

    .fd-input--number {
      max-width: 180px;
    }

    .fd-fieldset {
      min-width: 0;
      margin: 0;
      padding: var(--fd-space-3);
      border: 1px solid var(--fd-divider);
      border-radius: var(--fd-radius);
    }

    .fd-fieldset legend {
      padding: 0 6px;
      font-weight: 500;
    }

    .fd-fixed-user {
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      gap: var(--fd-space-2);
      padding: 10px 12px;
      border: 1px solid var(--fd-divider);
      border-radius: var(--fd-radius-sm);
      background: var(--fd-surface-alt);
    }

    .fd-transfer-grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
      align-items: end;
      gap: var(--fd-space-3);
    }

    .fd-transfer-column {
      display: flex;
      flex-direction: column;
      gap: 6px;
      min-width: 0;
    }

    .fd-transfer-actions {
      display: flex;
      flex-direction: column;
      gap: var(--fd-space-2);
      padding-bottom: 8px;
    }

    /* Buttons ------------------------------------------------------------- */

    .fd-button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: var(--fd-space-2);
      min-height: 48px;
      padding: 8px 16px;
      border: 1px solid var(--fd-border-strong);
      border-radius: var(--fd-radius-pill);
      background: transparent;
      color: var(--fd-text);
      font: inherit;
      font-size: var(--ha-font-size-m, 14px);
      cursor: pointer;
      transition:
        background-color var(--fd-duration-fast),
        border-color var(--fd-duration-fast),
        color var(--fd-duration-fast);
    }

    .fd-button:hover:not(:disabled) {
      background: var(--ha-color-fill-neutral-quiet-hover, var(--fd-surface-alt));
    }

    .fd-button:active:not(:disabled) {
      background: var(--ha-color-fill-neutral-quiet-active, var(--fd-surface-alt));
    }

    .fd-button:disabled {
      cursor: default;
      opacity: 0.5;
    }

    .fd-button[aria-busy="true"] {
      position: relative;
      color: transparent;
    }

    .fd-button[aria-busy="true"]::after {
      content: "";
      position: absolute;
      top: 50%;
      left: 50%;
      width: 18px;
      height: 18px;
      margin: -9px 0 0 -9px;
      border: 2px solid var(--fd-text);
      border-top-color: transparent;
      border-radius: 50%;
      animation: fd-spin 0.9s linear infinite;
    }

    .fd-button--primary {
      border-color: transparent;
      background: var(--fd-accent);
      color: var(--fd-on-accent);
    }

    .fd-button--primary:hover:not(:disabled) {
      background: var(--fd-accent-hover);
    }

    .fd-button--primary:active:not(:disabled) {
      background: var(--fd-accent-active);
    }

    .fd-button--filled {
      border-color: transparent;
      background: var(--fd-neutral-soft);
      color: var(--fd-text);
    }

    .fd-button--plain {
      border-color: transparent;
      background: transparent;
      color: var(--fd-text-muted);
    }

    .fd-button--plain:hover:not(:disabled) {
      background: var(--fd-surface-alt);
      color: var(--fd-text);
    }

    .fd-button--danger {
      border-color: var(--fd-error-border);
      background: transparent;
      color: var(--fd-error);
    }

    .fd-button--danger:hover:not(:disabled) {
      background: var(--fd-error-fill);
    }

    .fd-button--danger-filled {
      border-color: transparent;
      background: var(--fd-error-strong);
      color: var(--fd-on-error-strong);
    }

    .fd-button--primary[aria-busy="true"]::after,
    .fd-button--danger-filled[aria-busy="true"]::after {
      border-color: var(--fd-on-accent);
      border-top-color: transparent;
    }

    .fd-button--danger[aria-busy="true"]::after {
      border-color: var(--fd-error);
      border-top-color: transparent;
    }

    .fd-button--success {
      border-color: var(--fd-success-border);
      background: transparent;
      color: var(--fd-success);
    }

    .fd-button--success:hover:not(:disabled) {
      background: var(--fd-success-fill);
    }

    .fd-button--small {
      min-height: 48px;
      padding: 4px 12px;
      font-size: var(--fd-font-small);
    }

    .fd-button--xs {
      min-height: 24px;
      padding: 2px 8px;
      font-size: var(--fd-font-small);
    }

    .fd-button--large {
      min-height: 48px;
      padding: 8px 16px;
      font-size: var(--ha-font-size-l, 16px);
    }

    .fd-icon-button {
      display: inline-flex;
      flex: none;
      align-items: center;
      justify-content: center;
      width: 44px;
      height: 44px;
      padding: 0;
      border: none;
      border-radius: 50%;
      background: transparent;
      color: var(--fd-text-muted);
      font: inherit;
      cursor: pointer;
    }

    .fd-icon-button:hover:not(:disabled) {
      background: var(--fd-surface-alt);
      color: var(--fd-text);
    }

    .fd-icon-button--danger:hover:not(:disabled) {
      color: var(--fd-error);
    }

    .fd-icon-button:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }

    .fd-icon-button svg {
      display: block;
      width: 22px;
      height: 22px;
    }

    /* Banner und Status --------------------------------------------------- */

    .fd-banner {
      position: relative;
      display: flex;
      flex-wrap: wrap;
      align-items: flex-start;
      justify-content: space-between;
      gap: var(--fd-space-2);
      padding: var(--fd-space-3) var(--fd-space-3) var(--fd-space-3) var(--fd-space-8);
      border: 1px solid var(--fd-divider);
      border-left: 4px solid var(--fd-divider);
      border-radius: var(--fd-radius);
      background: var(--fd-surface-alt);
    }

    .fd-banner::before {
      content: "";
      position: absolute;
      inset-inline-start: var(--fd-space-3);
      top: var(--fd-space-3);
      width: 18px;
      height: 18px;
      background-color: var(--fd-text);
      mask: var(--fd-banner-icon) center / contain no-repeat;
      -webkit-mask: var(--fd-banner-icon) center / contain no-repeat;
    }

    .fd-banner--error {
      border-color: var(--fd-error-border);
      background: var(--fd-error-fill);
      --fd-banner-icon: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath d='M12 2 1 21h22L12 2zm1 15h-2v-2h2v2zm0-4h-2V9h2v4z'/%3E%3C/svg%3E");
    }

    .fd-banner--error::before {
      background-color: var(--fd-error);
    }

    .fd-banner--warning {
      border-color: var(--fd-warning-border);
      background: var(--fd-warning-fill);
      --fd-banner-icon: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath d='M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z'/%3E%3C/svg%3E");
    }

    .fd-banner--warning::before {
      background-color: var(--fd-warning);
    }

    .fd-banner--success {
      border-color: var(--fd-success-border);
      background: var(--fd-success-fill);
      --fd-banner-icon: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath d='M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm-2 15-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z'/%3E%3C/svg%3E");
    }

    .fd-banner--success::before {
      background-color: var(--fd-success);
    }

    .fd-inline-actions {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: var(--fd-space-2);
    }

    .fd-status {
      padding: 4px 10px;
      border: 1px solid var(--fd-divider);
      border-radius: var(--fd-radius-pill);
      font-size: var(--fd-font-small);
    }

    .fd-status--ok {
      border-color: var(--fd-success);
    }

    .fd-status--warn {
      border-color: var(--fd-warning);
    }

    /* Lagerstätten und Veröffentlichung ----------------------------------- */

    .fd-locations-form {
      display: flex;
      flex-direction: column;
      gap: var(--fd-space-3);
    }

    .fd-location-list {
      display: flex;
      flex-direction: column;
      gap: var(--fd-space-2);
      margin: 0;
      padding: 0;
      list-style: none;
    }

    .fd-location-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: center;
      gap: var(--fd-space-3);
      padding: var(--fd-space-3);
      border: 1px solid var(--fd-divider);
      border-radius: var(--fd-radius);
      background: var(--fd-surface-alt);
    }

    .fd-location-row .fd-button {
      min-height: 48px;
    }

    @media (max-width: 640px) {
      .fd-location-row {
        grid-template-columns: minmax(0, 1fr);
      }
    }

    /* Dialoge ------------------------------------------------------------- */

    .fd-dialog {
      width: min(820px, calc(100vw - 32px));
      max-height: calc(100vh - 48px);
      padding: 0;
      border: none;
      border-radius: var(--fd-radius-dialog);
      background: var(--fd-surface);
      color: var(--fd-text);
      box-shadow: var(--fd-shadow-dialog);
    }

    .fd-dialog--narrow {
      width: min(520px, calc(100vw - 32px));
    }

    .fd-dialog[open] {
      display: flex;
      flex-direction: column;
    }

    .fd-dialog::backdrop {
      background: rgba(0, 0, 0, 0.55);
    }

    .fd-dialog-head {
      display: flex;
      flex: none;
      align-items: flex-start;
      gap: var(--fd-space-3);
      padding: var(--fd-space-4) var(--fd-space-6) var(--fd-space-3);
      border-bottom: 1px solid var(--fd-divider);
    }

    .fd-dialog-title {
      flex: 1 1 auto;
      min-width: 0;
    }

    .fd-dialog-head h2 {
      margin: 0;
      font-size: var(--ha-font-size-xl, 1.5rem);
      font-weight: 500;
      line-height: var(--fd-line-height-heading, 1.2);
    }

    .fd-dialog-close {
      margin-inline-start: calc(-1 * var(--fd-space-2));
    }

    .fd-dialog-body {
      display: flex;
      flex: 1;
      flex-direction: column;
      gap: var(--fd-space-3);
      padding: 0 var(--fd-space-6) var(--fd-space-6);
      overflow-y: auto;
    }

    .fd-dialog-foot {
      display: flex;
      flex: none;
      flex-wrap: wrap;
      justify-content: flex-end;
      gap: var(--fd-space-3);
      padding: var(--fd-space-4);
      border-top: 1px solid var(--fd-divider);
    }

    .fd-dialog--shake {
      animation: fd-shake var(--fd-duration-normal) ease-in-out;
    }

    @keyframes fd-shake {
      0%,
      100% {
        transform: translateX(0);
      }

      20% {
        transform: translateX(-6px);
      }

      40% {
        transform: translateX(6px);
      }

      60% {
        transform: translateX(-4px);
      }

      80% {
        transform: translateX(4px);
      }
    }

    /* Toast --------------------------------------------------------------- */

    .fd-toast-slot {
      position: fixed;
      inset-inline: 0;
      bottom: var(--fd-space-6);
      display: flex;
      justify-content: center;
      pointer-events: none;
      z-index: 20;
    }

    .fd-toast {
      max-width: min(560px, calc(100vw - 32px));
      padding: 10px 18px;
      border-radius: var(--fd-radius-pill);
      background: var(--fd-accent);
      color: var(--fd-on-accent);
      box-shadow: 0 6px 20px rgba(0, 0, 0, 0.25);
      font-size: var(--fd-font-small);
    }

    /* Responsive ---------------------------------------------------------- */

    @media (max-width: 760px) {
      .fd-user-row {
        grid-template-columns: 1fr auto;
      }

      .fd-user-roles {
        grid-column: 1 / -1;
        grid-row: 2;
      }

      .fd-user-actions {
        grid-column: 2;
        grid-row: 1;
      }

      .fd-transfer-grid {
        grid-template-columns: 1fr;
        align-items: stretch;
      }

      .fd-transfer-actions {
        flex-direction: row;
        flex-wrap: wrap;
        padding-bottom: 0;
      }
    }

    @media (max-width: 640px) {
      .fd-shell {
        gap: var(--fd-space-3);
        padding: var(--fd-space-2);
      }

      .fd-bar-title {
        padding-inline-start: var(--ha-space-3, 12px);
        font-size: var(--ha-font-size-l, 1.25rem);
      }

      .fd-dialog,
      .fd-dialog--narrow {
        width: calc(100vw - 16px);
        max-height: calc(100vh - 16px);
      }

      .fd-dialog-body {
        padding: var(--fd-space-3);
      }

      .fd-multi-select {
        min-height: 120px;
      }
    }

    @media (prefers-reduced-motion: reduce) {
      *,
      *::before,
      *::after {
        animation: none !important;
        transition: none !important;
      }
    }
  `;
}

if (!customElements.get("frickeldave-store-config-panel")) {
  customElements.define("frickeldave-store-config-panel", FrickeldaveStoreConfigPanel);
}
