/**
 * Frickeldave Store - Verwaltungspanel für Home Assistant.
 *
 * Einzelne Lit-Web-Component für `panel_custom` mit `embed_iframe=False`.
 * Einzige Laufzeitabhängigkeit ist der von Home Assistant bereitgestellte
 * Lit-Import-Map-Eintrag. Alle Daten kommen über die HA-HTTP-API mit
 * Bearer-Token; die Autorisierung erfolgt serverseitig pro Anfrage.
 *
 * Aufbau:
 *   1. Konstanten und Hilfsfunktionen
 *   2. API-Schicht und Zustandsverwaltung
 *   3. Editor-, Bild-, Bestands-, Archiv-, Tag- und Einstellungslogik
 *   4. Render-Methoden (Produktliste, Dialoge, Einstellungen)
 *   5. Styles ausschliesslich ueber HA-Design-Tokens und eigene --fd-* Schicht
 */

import { LitElement, html, css } from "lit";

const API_ROOT = "/api/frickeldave_store";
const MAX_IMAGES = 10;
const PAGE_SIZE = 24;
const SEARCH_DEBOUNCE_MS = 300;
const TOAST_TIMEOUT_MS = 6000;
const REQUEST_TIMEOUT_MS = 30000;

const CATEGORY_LABELS = Object.freeze({
  "3D": "3D-Druck",
  EP: "Epoxidharz",
  LA: "Laser",
  SB: "Steinmasse",
  WO: "Holz",
});

const CATEGORY_KEYS = Object.freeze(Object.keys(CATEGORY_LABELS));

const VISIBILITY_OPTIONS = Object.freeze([
  { value: "all", label: "Alle" },
  { value: "visible", label: "Sichtbar" },
  { value: "invisible", label: "Unsichtbar" },
]);

const STOCK_TYPES = Object.freeze([
  {
    value: "in",
    label: "Zugang",
    hint: "Ware kommt ins Lager. Der Bestand im Verkaufsschrank bleibt unverändert.",
  },
  {
    value: "out",
    label: "Abgang",
    hint: "Ware verlässt das Lager, zum Beispiel durch Verkauf oder Schwund.",
  },
  {
    value: "transfer",
    label: "Umlagerung",
    hint: "Ware wird vom Lager in den Verkaufsschrank umgelagert.",
  },
  {
    value: "correction",
    label: "Korrektur",
    hint: "Der Lagerbestand wird auf den gezählten Wert gesetzt.",
  },
]);

const ROLE_LABELS = Object.freeze({
  product: "Produktpflege",
  finance: "Finanzen",
  app_admin: "Anwendungsverwaltung",
});

const currencyFormatter = new Intl.NumberFormat("de-DE", {
  style: "currency",
  currency: "EUR",
});

const numberFormatter = new Intl.NumberFormat("de-DE");

const dateFormatter = new Intl.DateTimeFormat("de-DE", {
  dateStyle: "medium",
  timeStyle: "short",
});

class ApiError extends Error {
  constructor(message, status = 0, code = "") {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

const formatPrice = (value) => {
  const number = Number(value);
  return Number.isFinite(number) ? currencyFormatter.format(number) : "—";
};

const formatNumber = (value) => {
  const number = Number(value);
  return Number.isFinite(number) ? numberFormatter.format(number) : "0";
};

const formatDateTime = (value) => {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : dateFormatter.format(date);
};

const categoryLabel = (key) => CATEGORY_LABELS[key] || key || "—";

const productPath = (articleNumber) =>
  `${API_ROOT}/products/${encodeURIComponent(String(articleNumber ?? ""))}`;

const toggleInList = (list, value) =>
  list.includes(value) ? list.filter((entry) => entry !== value) : [...list, value];

const normalizeTagNames = (data) => {
  const raw = Array.isArray(data)
    ? data
    : Array.isArray(data?.tags)
      ? data.tags
      : [];
  const names = raw
    .map((entry) => (typeof entry === "string" ? entry : entry?.name))
    .filter((name) => typeof name === "string" && name.trim().length > 0)
    .map((name) => name.trim());
  return [...new Set(names)].sort((a, b) => a.localeCompare(b, "de"));
};

const normalizeImage = (image) => {
  if (typeof image === "string") {
    return { name: image, url: image, is_main: false };
  }
  if (!image || typeof image !== "object") {
    return null;
  }
  return {
    name: typeof image.name === "string" ? image.name : "",
    url: typeof image.url === "string" ? image.url : "",
    is_main: Boolean(image.is_main),
  };
};

const isAbortError = (error) => error?.name === "AbortError";

export class FrickeldaveStorePanel extends LitElement {
  static properties = {
    hass: { attribute: false, noAccessor: true },
    panel: { attribute: false },
    narrow: { attribute: false, type: Boolean },

    _accessState: { state: true },
    _accessError: { state: true },
    _me: { state: true },
    _view: { state: true },

    _products: { state: true },
    _total: { state: true },
    _productsLoading: { state: true },
    _productsError: { state: true },

    _search: { state: true },
    _categoryFilter: { state: true },
    _tagFilter: { state: true },
    _customizableOnly: { state: true },
    _inStockOnly: { state: true },
    _inCabinetOnly: { state: true },
    _visibility: { state: true },
    _showArchived: { state: true },
    _sort: { state: true },
    _order: { state: true },
    _offset: { state: true },
    _allTags: { state: true },
    _imageVersion: { state: true },

    _editor: { state: true },
    _imagesDialog: { state: true },
    _stockDialog: { state: true },
    _archiveDialog: { state: true },
    _tagsDialog: { state: true },

    _settingsLoading: { state: true },
    _settingsSaving: { state: true },
    _settingsError: { state: true },
    _settingsNotice: { state: true },
    _settingsForm: { state: true },
    _settingsMeta: { state: true },

    _toast: { state: true },
  };

  constructor() {
    super();

    this._hass = undefined;
    this._controllers = new Set();
    this._searchTimer = undefined;
    this._toastTimer = undefined;
    this._focusReturn = null;
    this._accessUserId = null;
    this._productsRequestId = 0;
    this._imagesRequestId = 0;
    this._imageCache = new Map();
    this._imagePending = new Set();

    this._accessState = "idle";
    this._accessError = "";
    this._me = null;
    this._view = "products";

    this._products = [];
    this._total = 0;
    this._productsLoading = false;
    this._productsError = "";

    this._search = "";
    this._categoryFilter = [];
    this._tagFilter = [];
    this._customizableOnly = false;
    this._inStockOnly = false;
    this._inCabinetOnly = false;
    this._visibility = "all";
    this._showArchived = false;
    this._sort = "name";
    this._order = "asc";
    this._offset = 0;
    this._allTags = [];

    this._editor = null;
    this._imagesDialog = null;
    this._stockDialog = null;
    this._archiveDialog = null;
    this._tagsDialog = null;

    this._settingsLoading = false;
    this._settingsSaving = false;
    this._settingsError = "";
    this._settingsNotice = "";
    this._settingsForm = {
      ai_endpoint: "",
      ai_model: "",
      ai_prompt: "",
      api_key: "",
    };
    this._settingsMeta = { api_key_set: false };

    this._toast = null;
    this._imageVersion = 0;

    this._onVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        this._revalidateAccess();
      }
    };
  }

  get hass() {
    return this._hass;
  }

  set hass(value) {
    const previous = this._hass;
    if (previous === value) {
      return;
    }
    this._hass = value;
    const update = this.requestUpdate("hass", previous);
    if (update && typeof update.catch === "function") {
      update.catch(() => {});
    }

    const userId = value?.user?.id ?? null;
    if (this._accessUserId !== null && userId !== null && userId !== this._accessUserId) {
      this._accessUserId = userId;
      this._resetForUser();
    }

    this._bootstrap();
  }

  connectedCallback() {
    super.connectedCallback();
    document.addEventListener("visibilitychange", this._onVisibilityChange);
    this._bootstrap();
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    document.removeEventListener("visibilitychange", this._onVisibilityChange);
    clearTimeout(this._searchTimer);
    clearTimeout(this._toastTimer);
    for (const controller of this._controllers) {
      controller.abort();
    }
    this._controllers.clear();
    this._releaseImages();
  }

  firstUpdated() {
    this._bootstrap();
  }

  updated() {
    this._syncDialogs();
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

  _assetUrl(url) {
    if (!url) {
      return "";
    }
    try {
      return new URL(url, `${this._baseUrl()}/`).href;
    } catch {
      return url;
    }
  }

  _can(permission) {
    return Boolean(this._me?.can?.[permission]);
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
    if (status === 413) {
      return "Die Datei ist zu groß.";
    }
    if (status === 429) {
      return "Nutzungslimit erreicht. Bitte später erneut versuchen.";
    }
    if (status >= 500) {
      return "Der Server hat einen Fehler gemeldet. Bitte erneut versuchen.";
    }
    return "Die Anfrage ist fehlgeschlagen.";
  }

  _errorText(error) {
    if (error instanceof ApiError) {
      if (error.status === 429) {
        return error.message || "Nutzungslimit erreicht. Bitte später erneut versuchen.";
      }
      return error.message || this._httpFallbackMessage(error.status);
    }
    return "Unerwarteter Fehler. Bitte erneut versuchen.";
  }

  _suggestionError(error) {
    if (error instanceof ApiError) {
      if (error.status === 429) {
        return "Das Nutzungslimit des KI-Anbieters ist erreicht. Bitte später erneut versuchen.";
      }
      if (error.status === 408 || error.status === 504) {
        return "Der KI-Anbieter hat nicht rechtzeitig geantwortet. Bitte erneut versuchen.";
      }
      return error.message || "Der Beschreibungsvorschlag konnte nicht erstellt werden.";
    }
    return "Der Beschreibungsvorschlag konnte nicht erstellt werden.";
  }

  /**
   * Zentrale HTTP-Schicht. Setzt immer den Bearer-Token, parst das
   * Fehlerformat {"error":{"code","message"}} und bricht nach Timeout ab.
   */
  async _api(path, options = {}) {
    const { method = "GET", body, formData } = options;

    const token = this._token();
    if (!this.hass) {
      throw new ApiError("Home Assistant ist noch nicht verbunden. Bitte kurz warten.", 0, "no_hass");
    }
    if (!token) {
      throw new ApiError("Keine gültige Sitzung gefunden. Bitte die Seite neu laden.", 401, "no_token");
    }

    const controller = new AbortController();
    this._controllers.add(controller);
    let timedOut = false;
    const timeoutId = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, REQUEST_TIMEOUT_MS);

    const headers = {
      Authorization: `Bearer ${token}`,
      Accept: "application/json",
    };

    let payload;
    if (formData instanceof FormData) {
      payload = formData;
    } else if (body !== undefined) {
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

  /* --------------------------------------------------------------------- */
  /* Zugriff und Lebenszyklus der Daten                                     */
  /* --------------------------------------------------------------------- */

  _bootstrap() {
    if (!this.hass) {
      return;
    }
    if (!this._token()) {
      // Home Assistant liefert die Sitzung kurz nach dem ersten Render nach.
      return;
    }
    if (this._accessState !== "idle") {
      return;
    }
    this._loadMe();
  }

  _resetForUser() {
    for (const controller of this._controllers) {
      controller.abort();
    }
    this._controllers.clear();
    this._accessState = "idle";
    this._accessError = "";
    this._me = null;
    this._products = [];
    this._total = 0;
    this._allTags = [];
    this._editor = null;
    this._imagesDialog = null;
    this._stockDialog = null;
    this._archiveDialog = null;
    this._tagsDialog = null;
    this._settingsForm = { ai_endpoint: "", ai_model: "", ai_prompt: "", api_key: "" };
    this._settingsMeta = { api_key_set: false };
    this._settingsError = "";
    this._settingsNotice = "";
    this._releaseImages();
    this._imageVersion += 1;
    this._bootstrap();
  }

  async _loadMe() {
    this._accessState = "loading";
    this._accessError = "";
    try {
      const me = await this._api(`${API_ROOT}/me`);
      const can = {
        product: Boolean(me?.can?.product),
        finance: Boolean(me?.can?.finance),
        app_admin: Boolean(me?.can?.app_admin),
      };
      this._me = {
        userId: me?.user_id || "",
        name: typeof me?.name === "string" ? me.name : "",
        roles: Array.isArray(me?.roles) ? me.roles.filter((role) => typeof role === "string") : [],
        can,
      };
      this._accessUserId = this.hass?.user?.id ?? null;

      if (can.product || can.app_admin) {
        this._accessState = "ready";
        this._view = can.product ? "products" : "settings";
        if (can.product) {
          this._loadTags();
          this._loadProducts();
        }
        if (can.app_admin) {
          this._loadSettings();
        }
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
      this._accessError = this._errorText(error);
      this._accessState =
        error instanceof ApiError && (error.status === 401 || error.status === 403) ? "denied" : "error";
    }
  }

  async _revalidateAccess() {
    if (this._accessState !== "ready" || !this.hass || !this._token()) {
      return;
    }
    try {
      const me = await this._api(`${API_ROOT}/me`);
      const can = {
        product: Boolean(me?.can?.product),
        finance: Boolean(me?.can?.finance),
        app_admin: Boolean(me?.can?.app_admin),
      };
      this._me = {
        userId: me?.user_id || "",
        name: typeof me?.name === "string" ? me.name : this._me?.name || "",
        roles: Array.isArray(me?.roles) ? me.roles.filter((role) => typeof role === "string") : [],
        can,
      };
      if (!can.product && !can.app_admin) {
        this._accessState = "denied";
        this._accessError = "";
        this._products = [];
        this._total = 0;
        this._allTags = [];
        this._editor = null;
        this._imagesDialog = null;
        this._stockDialog = null;
        this._archiveDialog = null;
        this._tagsDialog = null;
        return;
      }
      if (!can.product) {
        this._products = [];
        this._total = 0;
        this._allTags = [];
        this._view = "settings";
        this._editor = null;
        this._imagesDialog = null;
        this._stockDialog = null;
        this._archiveDialog = null;
        this._tagsDialog = null;
      } else if (this._view === "products") {
        this._loadProducts();
        this._loadTags();
      }
    } catch (error) {
      if (isAbortError(error)) {
        return;
      }
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        this._accessState = "denied";
        this._accessError = this._errorText(error);
        this._products = [];
        this._total = 0;
        this._allTags = [];
      }
    }
  }

  _retryAccess() {
    this._accessState = "idle";
    this._accessError = "";
    this._bootstrap();
  }

  _setView(view) {
    if (view === "settings" && !this._can("app_admin")) {
      return;
    }
    if (view === "products" && !this._can("product")) {
      return;
    }
    this._view = view;
    if (view === "settings" && !this._settingsLoading && !this._settingsForm.ai_endpoint) {
      this._loadSettings();
    }
  }

  _reloadAll() {
    if (!this._can("product")) {
      return;
    }
    this._releaseImages();
    this._imageVersion += 1;
    this._loadTags();
    this._loadProducts();
  }

  /* --------------------------------------------------------------------- */
  /* Produktliste und Filter                                                */
  /* --------------------------------------------------------------------- */

  async _loadProducts() {
    if (!this._can("product")) {
      return;
    }
    const requestId = ++this._productsRequestId;
    this._productsLoading = true;
    this._productsError = "";

    const params = new URLSearchParams();
    const search = this._search.trim();
    if (search) {
      params.set("search", search);
    }
    if (this._categoryFilter.length > 0) {
      params.set("category", this._categoryFilter.join(","));
    }
    if (this._tagFilter.length > 0) {
      params.set("tags", this._tagFilter.join(","));
    }
    if (this._customizableOnly) {
      params.set("customizable", "true");
    }
    if (this._inStockOnly) {
      params.set("in_stock", "true");
    }
    if (this._inCabinetOnly) {
      params.set("in_cabinet", "true");
    }
    if (this._visibility !== "all") {
      params.set("visibility", this._visibility);
    }
    params.set("archived", this._showArchived ? "true" : "false");
    params.set("sort", this._sort);
    params.set("order", this._order);
    params.set("limit", String(PAGE_SIZE));
    params.set("offset", String(this._offset));

    try {
      const data = await this._api(`${API_ROOT}/products?${params.toString()}`);
      if (requestId !== this._productsRequestId) {
        return;
      }
      const products = Array.isArray(data?.products) ? data.products : [];
      this._products = products;
      this._total = Number.isFinite(Number(data?.total)) ? Number(data.total) : products.length;
    } catch (error) {
      if (isAbortError(error) || requestId !== this._productsRequestId) {
        return;
      }
      this._products = [];
      this._total = 0;
      this._productsError = this._errorText(error);
    } finally {
      if (requestId === this._productsRequestId) {
        this._productsLoading = false;
      }
    }
  }

  async _loadTags() {
    if (!this._can("product")) {
      return;
    }
    try {
      const data = await this._api(`${API_ROOT}/tags`);
      this._allTags = normalizeTagNames(data);
    } catch (error) {
      if (isAbortError(error)) {
        return;
      }
      // Die Tag-Liste ist nur Komfort. Fehler erscheinen im Tag-Dialog bzw. beim Speichern.
      this._allTags = [];
    }
  }

  _onSearchInput(value) {
    this._search = value;
    clearTimeout(this._searchTimer);
    this._searchTimer = setTimeout(() => {
      this._offset = 0;
      this._loadProducts();
    }, SEARCH_DEBOUNCE_MS);
  }

  _toggleFilterValue(field, value) {
    this[field] = toggleInList(this[field], value);
    this._offset = 0;
    this._loadProducts();
  }

  _setVisibility(value) {
    this._visibility = value;
    this._offset = 0;
    this._loadProducts();
  }

  _setFlag(field, value) {
    this[field] = Boolean(value);
    this._offset = 0;
    this._loadProducts();
  }

  _setSortField(value) {
    this._sort = value === "price" ? "price" : "name";
    this._offset = 0;
    this._loadProducts();
  }

  _toggleSortOrder() {
    this._order = this._order === "asc" ? "desc" : "asc";
    this._offset = 0;
    this._loadProducts();
  }

  _changePage(delta) {
    const pages = Math.max(1, Math.ceil(this._total / PAGE_SIZE));
    const page = Math.floor(this._offset / PAGE_SIZE) + 1;
    const next = Math.min(Math.max(page + delta, 1), pages);
    if (next === page) {
      return;
    }
    this._offset = (next - 1) * PAGE_SIZE;
    this._loadProducts();
  }

  _hasActiveFilters() {
    return Boolean(
      this._search.trim() ||
        this._categoryFilter.length > 0 ||
        this._tagFilter.length > 0 ||
        this._customizableOnly ||
        this._inStockOnly ||
        this._inCabinetOnly ||
        this._visibility !== "all" ||
        this._showArchived,
    );
  }

  _resetFilters() {
    this._search = "";
    this._categoryFilter = [];
    this._tagFilter = [];
    this._customizableOnly = false;
    this._inStockOnly = false;
    this._inCabinetOnly = false;
    this._visibility = "all";
    this._showArchived = false;
    this._offset = 0;
    this._loadProducts();
  }

  _mainImage(product) {
    const images = Array.isArray(product?.images) ? product.images.map(normalizeImage).filter(Boolean) : [];
    return images.find((image) => image.is_main) || images[0] || null;
  }

  _imageUrl(product, image) {
    if (!image) {
      return "";
    }
    if (image.url) {
      return image.url;
    }
    if (image.name && product?.articleNumber) {
      return `${productPath(product.articleNumber)}/images/${encodeURIComponent(image.name)}`;
    }
    return "";
  }

  /**
   * Bilder liegen hinter einer bearer-geschuetzten Route. Ein reines
   * <img src> kann keinen Authorization-Header senden, deshalb werden die
   * Dateien einmalig mit Token geladen und als Object-URL zwischengespeichert.
   */
  _imageState(url) {
    const resolved = this._assetUrl(url);
    if (!resolved) {
      return { status: "empty", src: "" };
    }
    const entry = this._imageCache.get(resolved);
    if (entry) {
      return entry.status === "ready"
        ? { status: "ready", src: entry.objectUrl }
        : { status: "error", src: "" };
    }
    if (!this._imagePending.has(resolved) && this._token()) {
      queueMicrotask(() => this._loadImage(resolved));
    }
    return { status: "loading", src: "" };
  }

  _imageSrc(url) {
    return this._imageState(url).src;
  }

  async _loadImage(url) {
    if (!this.isConnected || this._imagePending.has(url) || this._imageCache.has(url)) {
      return;
    }
    const token = this._token();
    if (!token) {
      return;
    }
    this._imagePending.add(url);
    const controller = new AbortController();
    this._controllers.add(controller);
    try {
      const response = await fetch(url, {
        headers: { Authorization: `Bearer ${token}` },
        credentials: "same-origin",
        signal: controller.signal,
      });
      if (!response.ok) {
        throw new Error(`Bild konnte nicht geladen werden (${response.status}).`);
      }
      const blob = await response.blob();
      this._imageCache.set(url, { status: "ready", objectUrl: URL.createObjectURL(blob) });
      this._imageVersion += 1;
    } catch (error) {
      if (!isAbortError(error)) {
        this._imageCache.set(url, { status: "error", objectUrl: "" });
        this._imageVersion += 1;
      }
    } finally {
      this._imagePending.delete(url);
      this._controllers.delete(controller);
    }
  }

  _releaseImages() {
    for (const entry of this._imageCache.values()) {
      if (entry.objectUrl) {
        URL.revokeObjectURL(entry.objectUrl);
      }
    }
    this._imageCache.clear();
    this._imagePending.clear();
  }

  _roleLabels() {
    const roles = this._me?.roles ?? [];
    const labels = roles.map((role) => ROLE_LABELS[role]).filter(Boolean);
    if (labels.length > 0) {
      return labels;
    }
    const derived = [];
    if (this._can("product")) {
      derived.push(ROLE_LABELS.product);
    }
    if (this._can("finance")) {
      derived.push(ROLE_LABELS.finance);
    }
    if (this._can("app_admin")) {
      derived.push(ROLE_LABELS.app_admin);
    }
    return derived;
  }

  _showToast(message) {
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
    const active = this.renderRoot?.activeElement;
    this._focusReturn = active && typeof active.focus === "function" ? active : null;
  }

  _restoreFocus() {
    const target = this._focusReturn;
    this._focusReturn = null;
    this.updateComplete
      .then(() => {
        const element = target && target.isConnected
          ? target
          : this.renderRoot?.querySelector("#fd-main-heading");
        if (element && typeof element.focus === "function") {
          element.focus();
        }
      })
      .catch(() => {});
  }

  _syncDialogs() {
    const entries = [
      ["#fd-editor-dialog", Boolean(this._editor)],
      ["#fd-images-dialog", Boolean(this._imagesDialog)],
      ["#fd-stock-dialog", Boolean(this._stockDialog)],
      ["#fd-archive-dialog", Boolean(this._archiveDialog)],
      ["#fd-tags-dialog", Boolean(this._tagsDialog)],
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

  _focusFirstInvalid(errors) {
    const firstKey = Object.keys(errors)[0];
    if (!firstKey) {
      return;
    }
    this.updateComplete
      .then(() => {
        const field = this.renderRoot?.querySelector(`[data-field="${firstKey}"]`);
        if (field && typeof field.focus === "function") {
          field.focus();
        }
      })
      .catch(() => {});
  }

  /* --------------------------------------------------------------------- */
  /* Produkt-Editor                                                         */
  /* --------------------------------------------------------------------- */

  _openCreate() {
    if (!this._can("product")) {
      return;
    }
    this._captureFocus();
    this._editor = {
      mode: "create",
      articleNumber: "",
      name: "",
      description: "",
      category: CATEGORY_KEYS[0],
      price: "",
      size: "",
      tags: [],
      tagDraft: "",
      customizable: true,
      visible: true,
      archived: false,
      saving: false,
      error: "",
      fieldErrors: {},
      suggestion: null,
    };
  }

  _openEdit(product) {
    if (!this._can("product") || !product) {
      return;
    }
    this._captureFocus();
    this._editor = {
      mode: "edit",
      articleNumber: product.articleNumber || "",
      name: typeof product.name === "string" ? product.name : "",
      description: typeof product.description === "string" ? product.description : "",
      category: product.category || "",
      price: this._formatPriceInput(product.price),
      size: typeof product.size === "string" ? product.size : "",
      tags: Array.isArray(product.tags) ? [...product.tags] : [],
      tagDraft: "",
      customizable: Boolean(product.customizable),
      visible: product.visible !== false,
      archived: Boolean(product.archived),
      saving: false,
      error: "",
      fieldErrors: {},
      suggestion: null,
    };
  }

  _closeEditor() {
    if (this._editor?.saving) {
      return;
    }
    this._editor = null;
    this._restoreFocus();
  }

  _patchEditor(patch) {
    if (!this._editor) {
      return;
    }
    this._editor = { ...this._editor, ...patch };
  }

  _setEditorField(field, value) {
    const editor = this._editor;
    if (!editor) {
      return;
    }
    const fieldErrors = { ...editor.fieldErrors };
    delete fieldErrors[field];
    this._editor = { ...editor, [field]: value, fieldErrors, error: "" };
  }

  _formatPriceInput(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) {
      return "";
    }
    return number.toFixed(2).replace(".", ",");
  }

  _parsePrice(raw) {
    const text = String(raw ?? "")
      .trim()
      .replace(/\s/g, "")
      .replace("€", "");
    if (!text) {
      return null;
    }
    let normalized = text;
    if (text.includes(",") && text.includes(".")) {
      normalized = text.replace(/\./g, "").replace(",", ".");
    } else {
      normalized = text.replace(",", ".");
    }
    if (!/^\d+(\.\d{1,2})?$/.test(normalized)) {
      return null;
    }
    const value = Number(normalized);
    return Number.isFinite(value) ? Math.round(value * 100) / 100 : null;
  }

  _validateEditor(editor) {
    const errors = {};
    const name = editor.name.trim();
    if (!name) {
      errors.name = "Bitte einen Namen angeben.";
    } else if (name.length > 200) {
      errors.name = "Der Name darf höchstens 200 Zeichen enthalten.";
    }

    if (editor.mode === "create" && !CATEGORY_LABELS[editor.category]) {
      errors.category = "Bitte eine Kategorie auswählen.";
    }

    if (!String(editor.price).trim()) {
      errors.price = "Bitte einen Preis angeben.";
    } else if (this._parsePrice(editor.price) === null) {
      errors.price = "Bitte einen gültigen Preis angeben, zum Beispiel 12,50.";
    }

    const price = this._parsePrice(editor.price);
    if (price !== null && price > 1000000) {
      errors.price = "Der Preis ist unrealistisch hoch.";
    }
    if (editor.size.trim().length > 200) {
      errors.size = "Die Größe darf höchstens 200 Zeichen enthalten.";
    }
    if (editor.description.length > 20000) {
      errors.description = "Die Beschreibung darf höchstens 20000 Zeichen enthalten.";
    }
    return errors;
  }

  _addEditorTag(rawName) {
    const editor = this._editor;
    if (!editor) {
      return;
    }
    const name = String(rawName ?? "").trim();
    if (!name) {
      return;
    }
    if (editor.tags.some((tag) => tag.toLowerCase() === name.toLowerCase())) {
      this._patchEditor({ error: `Der Tag „${name}“ ist bereits gesetzt.`, tagDraft: "" });
      return;
    }
    this._patchEditor({ tags: [...editor.tags, name], tagDraft: "", error: "" });
  }

  _removeEditorTag(name) {
    const editor = this._editor;
    if (!editor) {
      return;
    }
    this._patchEditor({ tags: editor.tags.filter((tag) => tag !== name), error: "" });
  }

  async _saveEditor(event) {
    event?.preventDefault();
    const editor = this._editor;
    if (!editor || editor.saving) {
      return;
    }

    const fieldErrors = this._validateEditor(editor);
    if (Object.keys(fieldErrors).length > 0) {
      this._patchEditor({
        fieldErrors,
        error: "Bitte die markierten Felder prüfen.",
      });
      this._focusFirstInvalid(fieldErrors);
      return;
    }

    const payload = {
      name: editor.name.trim(),
      description: editor.description.trim(),
      price: this._parsePrice(editor.price),
      size: editor.size.trim(),
      tags: [...editor.tags],
      customizable: editor.customizable,
      visible: editor.visible,
    };

    this._patchEditor({ saving: true, error: "", fieldErrors: {} });

    try {
      if (editor.mode === "create") {
        await this._api(`${API_ROOT}/products`, {
          method: "POST",
          body: { ...payload, category: editor.category },
        });
      } else {
        await this._api(productPath(editor.articleNumber), {
          method: "PATCH",
          body: payload,
        });
      }

      this._editor = null;
      this._showToast(editor.mode === "create" ? "Produkt wurde angelegt." : "Produkt wurde gespeichert.");
      this._restoreFocus();
      this._loadProducts();
      this._loadTags();
    } catch (error) {
      if (isAbortError(error)) {
        this._patchEditor({ saving: false });
        return;
      }
      this._patchEditor({ saving: false, error: this._errorText(error) });
    }
  }

  async _suggestDescription() {
    const editor = this._editor;
    if (!editor || editor.mode !== "edit" || editor.suggestion?.loading) {
      return;
    }
    this._patchEditor({ suggestion: { loading: true, error: "", text: "" } });
    try {
      const data = await this._api(`${productPath(editor.articleNumber)}/description/suggest`, {
        method: "POST",
      });
      const text = typeof data?.suggestion === "string" ? data.suggestion.trim() : "";
      if (!text) {
        throw new ApiError("Es wurde kein Vorschlag geliefert. Bitte erneut versuchen.", 0, "empty_suggestion");
      }
      this._patchEditor({ suggestion: { loading: false, error: "", text } });
    } catch (error) {
      if (isAbortError(error)) {
        this._patchEditor({ suggestion: null });
        return;
      }
      this._patchEditor({ suggestion: { loading: false, error: this._suggestionError(error), text: "" } });
    }
  }

  _patchSuggestion(text) {
    const editor = this._editor;
    if (!editor?.suggestion) {
      return;
    }
    this._editor = { ...editor, suggestion: { ...editor.suggestion, text } };
  }

  _applySuggestion() {
    const editor = this._editor;
    if (!editor?.suggestion) {
      return;
    }
    this._patchEditor({ description: editor.suggestion.text, suggestion: null });
    this._showToast("Vorschlag übernommen. Bitte noch speichern.");
  }

  _discardSuggestion() {
    if (!this._editor?.suggestion) {
      return;
    }
    this._patchEditor({ suggestion: null });
  }

  /* --------------------------------------------------------------------- */
  /* Bilder                                                                 */
  /* --------------------------------------------------------------------- */

  _openImages(product) {
    if (!this._can("product") || !product) {
      return;
    }
    this._captureFocus();
    this._imagesDialog = {
      product,
      images: [],
      loading: true,
      busy: false,
      error: "",
      notice: "",
      pendingDelete: null,
    };
    this._loadImages(product.articleNumber);
  }

  _closeImages() {
    if (this._imagesDialog?.busy) {
      return;
    }
    this._imagesDialog = null;
    this._imagesRequestId += 1;
    this._restoreFocus();
  }

  async _loadImages(articleNumber) {
    if (!this._imagesDialog) {
      return;
    }
    const requestId = ++this._imagesRequestId;
    this._imagesDialog = { ...this._imagesDialog, loading: true, error: "" };
    try {
      const data = await this._api(`${productPath(articleNumber)}/images`);
      if (requestId !== this._imagesRequestId) {
        return;
      }
      const current = this._imagesDialog;
      if (!current || current.product.articleNumber !== articleNumber) {
        return;
      }
      const images = Array.isArray(data?.images) ? data.images.map(normalizeImage).filter(Boolean) : [];
      this._imagesDialog = { ...current, images, loading: false };
    } catch (error) {
      if (isAbortError(error) || requestId !== this._imagesRequestId) {
        return;
      }
      const current = this._imagesDialog;
      if (!current) {
        return;
      }
      this._imagesDialog = { ...current, loading: false, error: this._errorText(error) };
    }
  }

  async _onImagesSelected(event) {
    const input = event.target;
    const files = Array.from(input?.files ?? []);
    if (input) {
      input.value = "";
    }
    const dialog = this._imagesDialog;
    if (!dialog || dialog.busy || files.length === 0) {
      return;
    }

    if (dialog.images.length + files.length > MAX_IMAGES) {
      const free = Math.max(MAX_IMAGES - dialog.images.length, 0);
      this._imagesDialog = {
        ...dialog,
        error:
          `Maximal ${MAX_IMAGES} Bilder pro Produkt. Es sind bereits ${dialog.images.length} Bilder hinterlegt ` +
          `und nur noch ${free} ${free === 1 ? "Platz" : "Plätze"} frei. Es wurde nichts hochgeladen.`,
        notice: "",
        pendingDelete: null,
      };
      return;
    }

    const articleNumber = dialog.product.articleNumber;
    this._imagesDialog = { ...dialog, busy: true, error: "", notice: "", pendingDelete: null };

    try {
      for (const file of files) {
        const formData = new FormData();
        formData.append("file", file, file.name);
        await this._api(`${productPath(articleNumber)}/images`, { method: "POST", formData });
      }
      await this._loadImages(articleNumber);
      if (this._imagesDialog) {
        this._imagesDialog = {
          ...this._imagesDialog,
          busy: false,
          notice: files.length === 1 ? "Bild wurde hochgeladen." : `${files.length} Bilder wurden hochgeladen.`,
        };
      }
      this._loadProducts();
    } catch (error) {
      if (isAbortError(error)) {
        if (this._imagesDialog) {
          this._imagesDialog = { ...this._imagesDialog, busy: false };
        }
        return;
      }
      await this._loadImages(articleNumber);
      if (this._imagesDialog) {
        this._imagesDialog = {
          ...this._imagesDialog,
          busy: false,
          error: `${this._errorText(error)} Bereits übertragene Bilder bleiben erhalten.`,
        };
      }
    }
  }

  async _moveImage(index, delta) {
    const dialog = this._imagesDialog;
    if (!dialog || dialog.busy || dialog.loading) {
      return;
    }
    const target = index + delta;
    if (target < 0 || target >= dialog.images.length) {
      return;
    }
    const images = [...dialog.images];
    const [moved] = images.splice(index, 1);
    if (!moved) {
      return;
    }
    images.splice(target, 0, moved);

    const articleNumber = dialog.product.articleNumber;
    this._imagesDialog = { ...dialog, images, busy: true, error: "", notice: "", pendingDelete: null };

    try {
      await this._api(`${productPath(articleNumber)}/images/order`, {
        method: "POST",
        body: { order: images.map((image) => image.name) },
      });
      await this._loadImages(articleNumber);
      if (this._imagesDialog) {
        this._imagesDialog = { ...this._imagesDialog, busy: false, notice: "Reihenfolge gespeichert." };
      }
      this._loadProducts();
    } catch (error) {
      if (isAbortError(error)) {
        return;
      }
      await this._loadImages(articleNumber);
      if (this._imagesDialog) {
        this._imagesDialog = { ...this._imagesDialog, busy: false, error: this._errorText(error) };
      }
    }
  }

  async _setMainImage(name) {
    const dialog = this._imagesDialog;
    if (!dialog || dialog.busy || !name) {
      return;
    }
    const articleNumber = dialog.product.articleNumber;
    this._imagesDialog = { ...dialog, busy: true, error: "", notice: "", pendingDelete: null };

    try {
      await this._api(`${productPath(articleNumber)}/images/main`, {
        method: "POST",
        body: { name },
      });
      await this._loadImages(articleNumber);
      if (this._imagesDialog) {
        this._imagesDialog = { ...this._imagesDialog, busy: false, notice: "Hauptbild gespeichert." };
      }
      this._loadProducts();
    } catch (error) {
      if (isAbortError(error)) {
        return;
      }
      await this._loadImages(articleNumber);
      if (this._imagesDialog) {
        this._imagesDialog = { ...this._imagesDialog, busy: false, error: this._errorText(error) };
      }
    }
  }

  _requestDeleteImage(name) {
    const dialog = this._imagesDialog;
    if (!dialog || dialog.busy) {
      return;
    }
    this._imagesDialog = {
      ...dialog,
      pendingDelete: dialog.pendingDelete === name ? null : name,
      error: "",
      notice: "",
    };
  }

  async _deleteImage(name) {
    const dialog = this._imagesDialog;
    if (!dialog || dialog.busy || !name) {
      return;
    }
    const articleNumber = dialog.product.articleNumber;
    this._imagesDialog = { ...dialog, busy: true, error: "", pendingDelete: null };

    try {
      await this._api(`${productPath(articleNumber)}/images/${encodeURIComponent(name)}`, {
        method: "DELETE",
      });
      await this._loadImages(articleNumber);
      if (this._imagesDialog) {
        this._imagesDialog = { ...this._imagesDialog, busy: false, notice: "Bild wurde gelöscht." };
      }
      this._loadProducts();
    } catch (error) {
      if (isAbortError(error)) {
        return;
      }
      await this._loadImages(articleNumber);
      if (this._imagesDialog) {
        this._imagesDialog = { ...this._imagesDialog, busy: false, error: this._errorText(error) };
      }
    }
  }

  /* --------------------------------------------------------------------- */
  /* Bestandsbuchung                                                        */
  /* --------------------------------------------------------------------- */

  _openStock(product) {
    if (!this._can("product") || !product) {
      return;
    }
    this._captureFocus();
    this._stockDialog = {
      product,
      type: "in",
      quantity: "1",
      note: "",
      saving: false,
      error: "",
    };
  }

  _closeStock() {
    if (this._stockDialog?.saving) {
      return;
    }
    this._stockDialog = null;
    this._restoreFocus();
  }

  async _saveStock(event) {
    event?.preventDefault();
    const dialog = this._stockDialog;
    if (!dialog || dialog.saving) {
      return;
    }

    const type = STOCK_TYPES.some((entry) => entry.value === dialog.type) ? dialog.type : "in";
    const quantityText = String(dialog.quantity).trim();
    const quantity = Number(quantityText);

    if (!/^\d+$/.test(quantityText) || !Number.isInteger(quantity) || quantity < 1) {
      this._stockDialog = {
        ...dialog,
        error: "Bitte eine ganze Zahl größer 0 angeben.",
      };
      return;
    }

    this._stockDialog = { ...dialog, saving: true, error: "" };

    try {
      await this._api(`${productPath(dialog.product.articleNumber)}/stock`, {
        method: "POST",
        body: { type, quantity, note: dialog.note.trim() },
      });
      this._stockDialog = null;
      this._showToast("Bestandsbuchung gespeichert.");
      this._restoreFocus();
      this._loadProducts();
    } catch (error) {
      if (isAbortError(error)) {
        return;
      }
      if (!this._stockDialog) {
        return;
      }
      this._stockDialog = { ...this._stockDialog, saving: false, error: this._errorText(error) };
    }
  }

  /* --------------------------------------------------------------------- */
  /* Archivierung (nur Soft-Delete)                                         */
  /* --------------------------------------------------------------------- */

  _requestArchive(product) {
    if (!this._can("product") || !product || product.archived) {
      return;
    }
    this._captureFocus();
    this._archiveDialog = { product, saving: false, error: "" };
  }

  _closeArchive() {
    if (this._archiveDialog?.saving) {
      return;
    }
    this._archiveDialog = null;
    this._restoreFocus();
  }

  async _confirmArchive() {
    const dialog = this._archiveDialog;
    if (!dialog || dialog.saving) {
      return;
    }
    this._archiveDialog = { ...dialog, saving: true, error: "" };

    try {
      await this._api(`${productPath(dialog.product.articleNumber)}/archive`, { method: "POST" });
      this._archiveDialog = null;
      this._showToast("Produkt wurde archiviert. Die Daten bleiben erhalten.");
      this._restoreFocus();
      this._loadProducts();
    } catch (error) {
      if (isAbortError(error)) {
        return;
      }
      if (!this._archiveDialog) {
        return;
      }
      this._archiveDialog = { ...this._archiveDialog, saving: false, error: this._errorText(error) };
    }
  }

  /* --------------------------------------------------------------------- */
  /* Tag-Verwaltung                                                         */
  /* --------------------------------------------------------------------- */

  _openTags() {
    if (!this._can("product")) {
      return;
    }
    this._captureFocus();
    this._tagsDialog = {
      tags: [...this._allTags],
      draft: "",
      loading: true,
      busy: false,
      error: "",
      notice: "",
      pendingDelete: null,
    };
    this._refreshTagDialog();
  }

  _closeTags() {
    if (this._tagsDialog?.busy) {
      return;
    }
    this._tagsDialog = null;
    this._restoreFocus();
  }

  async _refreshTagDialog() {
    if (!this._tagsDialog) {
      return;
    }
    this._tagsDialog = { ...this._tagsDialog, loading: true, error: "" };
    try {
      const data = await this._api(`${API_ROOT}/tags`);
      const tags = normalizeTagNames(data);
      this._allTags = tags;
      if (!this._tagsDialog) {
        return;
      }
      this._tagsDialog = { ...this._tagsDialog, tags, loading: false };
    } catch (error) {
      if (isAbortError(error) || !this._tagsDialog) {
        return;
      }
      this._tagsDialog = { ...this._tagsDialog, loading: false, error: this._errorText(error) };
    }
  }

  async _addTag(event) {
    event?.preventDefault();
    const dialog = this._tagsDialog;
    if (!dialog || dialog.busy) {
      return;
    }
    const name = dialog.draft.trim();
    if (!name) {
      this._tagsDialog = { ...dialog, error: "Bitte einen Tag-Namen angeben." };
      return;
    }
    if (name.length > 40) {
      this._tagsDialog = { ...dialog, error: "Der Tag-Name darf höchstens 40 Zeichen enthalten." };
      return;
    }

    this._tagsDialog = { ...dialog, busy: true, error: "", notice: "", pendingDelete: null };

    try {
      await this._api(`${API_ROOT}/tags`, { method: "POST", body: { name } });
      await this._refreshTagDialog();
      if (this._tagsDialog) {
        this._tagsDialog = {
          ...this._tagsDialog,
          busy: false,
          draft: "",
          notice: `Tag „${name}“ wurde angelegt.`,
        };
      }
    } catch (error) {
      if (isAbortError(error)) {
        if (this._tagsDialog) {
          this._tagsDialog = { ...this._tagsDialog, busy: false };
        }
        return;
      }
      if (!this._tagsDialog) {
        return;
      }
      this._tagsDialog = { ...this._tagsDialog, busy: false, error: this._errorText(error) };
    }
  }

  _requestDeleteTag(name) {
    const dialog = this._tagsDialog;
    if (!dialog || dialog.busy) {
      return;
    }
    this._tagsDialog = {
      ...dialog,
      pendingDelete: dialog.pendingDelete === name ? null : name,
      error: "",
      notice: "",
    };
  }

  async _deleteTag(name) {
    const dialog = this._tagsDialog;
    if (!dialog || dialog.busy || !name) {
      return;
    }
    this._tagsDialog = { ...dialog, busy: true, error: "", pendingDelete: null };

    try {
      await this._api(`${API_ROOT}/tags/${encodeURIComponent(name)}`, { method: "DELETE" });
      if (this._tagFilter.includes(name)) {
        this._tagFilter = this._tagFilter.filter((tag) => tag !== name);
        this._offset = 0;
      }
      await this._refreshTagDialog();
      if (this._tagsDialog) {
        this._tagsDialog = { ...this._tagsDialog, busy: false, notice: `Tag „${name}“ wurde gelöscht.` };
      }
      this._loadProducts();
    } catch (error) {
      if (isAbortError(error)) {
        if (this._tagsDialog) {
          this._tagsDialog = { ...this._tagsDialog, busy: false };
        }
        return;
      }
      if (!this._tagsDialog) {
        return;
      }
      this._tagsDialog = { ...this._tagsDialog, busy: false, error: this._errorText(error) };
    }
  }

  /* --------------------------------------------------------------------- */
  /* Einstellungen (nur Anwendungsverwaltung)                               */
  /* --------------------------------------------------------------------- */

  async _loadSettings() {
    if (!this._can("app_admin")) {
      return;
    }
    this._settingsLoading = true;
    this._settingsError = "";
    try {
      const data = await this._api(`${API_ROOT}/settings`);
      const payload = data && typeof data === "object" ? data : {};
      const ai = payload.ai && typeof payload.ai === "object" ? payload.ai : payload;
      this._settingsForm = {
        ai_endpoint: typeof ai.endpoint === "string" ? ai.endpoint : "",
        ai_model: typeof ai.model === "string" ? ai.model : "",
        ai_prompt: typeof ai.prompt === "string" ? ai.prompt : "",
        api_key: "",
      };
      this._settingsMeta = { api_key_set: Boolean(ai.api_key_set) };
    } catch (error) {
      if (isAbortError(error)) {
        return;
      }
      this._settingsError = this._errorText(error);
    } finally {
      this._settingsLoading = false;
    }
  }

  _setSettingsField(field, value) {
    this._settingsForm = { ...this._settingsForm, [field]: value };
  }

  async _saveSettings(event) {
    event?.preventDefault();
    if (!this._can("app_admin") || this._settingsSaving) {
      return;
    }
    const form = this._settingsForm;
    if (!form.ai_endpoint.trim()) {
      this._settingsError = "Bitte einen KI-Endpunkt angeben.";
      return;
    }

    const ai = {
      endpoint: form.ai_endpoint.trim(),
      model: form.ai_model.trim(),
      prompt: form.ai_prompt,
    };
    const key = form.api_key.trim();
    if (key) {
      ai.api_key = key;
    }
    const body = { ai };

    this._settingsSaving = true;
    this._settingsError = "";
    this._settingsNotice = "";

    try {
      await this._api(`${API_ROOT}/settings`, { method: "PATCH", body });
      this._settingsForm = { ...this._settingsForm, api_key: "" };
      this._settingsSaving = false;
      this._settingsNotice = "Einstellungen gespeichert.";
      // Der Schlüssel wird nie angezeigt; der Status kommt ausschließlich vom Server.
      await this._loadSettings();
    } catch (error) {
      this._settingsSaving = false;
      if (isAbortError(error)) {
        return;
      }
      this._settingsError = this._errorText(error);
    }
  }

  /* --------------------------------------------------------------------- */
  /* Render: Rahmen, Kopfzeile, Navigation                                  */
  /* --------------------------------------------------------------------- */

  render() {
    return html`
      ${this._renderAccess()}
      ${this._renderEditorDialog()}
      ${this._renderImagesDialog()}
      ${this._renderStockDialog()}
      ${this._renderArchiveDialog()}
      ${this._renderTagsDialog()}
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
          <h1 id="fd-main-heading" tabindex="-1">Frickeldave Store</h1>
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
        <section class="fd-card fd-state">
          <h1 id="fd-main-heading" tabindex="-1">Kein Zugriff</h1>
          <p>Ihr Home-Assistant-Benutzer ist für diese Verwaltung nicht freigegeben.</p>
          <p class="fd-muted">${this._accessError || "Bitte wenden Sie sich an die Anwendungsverwaltung."}</p>
        </section>
      </div>
    `;
  }

  _renderAccessError() {
    return html`
      <div class="fd-shell">
        <section class="fd-card fd-state" role="alert">
          <h1 id="fd-main-heading" tabindex="-1">Verbindung fehlgeschlagen</h1>
          <p>${this._accessError}</p>
          <button type="button" class="fd-button fd-button--primary" @click=${() => this._retryAccess()}>
            Erneut versuchen
          </button>
        </section>
      </div>
    `;
  }

  _renderMain() {
    const canProduct = this._can("product");
    const canAdmin = this._can("app_admin");
    const view = this._view === "settings" && canAdmin ? "settings" : canProduct ? "products" : "settings";

    return html`
      <div class="fd-shell">
        <header class="fd-header">
          <div class="fd-header-text">
            <h1 id="fd-main-heading" tabindex="-1">Frickeldave Store</h1>
            <p class="fd-muted">
              Angemeldet als ${this._me?.name || this.hass?.user?.name || "Unbekannt"}
            </p>
            ${this._roleLabels().length > 0
              ? html`
                  <ul class="fd-role-list" role="list">
                    ${this._roleLabels().map((label) => html`<li class="fd-role">${label}</li>`)}
                  </ul>
                `
              : ""}
          </div>
          <div class="fd-header-actions">
            ${canProduct
              ? html`
                  <button type="button" class="fd-button" @click=${() => this._reloadAll()} ?disabled=${this._productsLoading}>
                    Aktualisieren
                  </button>
                  <button type="button" class="fd-button fd-button--primary" @click=${() => this._openCreate()}>
                    Produkt anlegen
                  </button>
                `
              : ""}
          </div>
        </header>

        ${canProduct && canAdmin ? this._renderTabs(view) : ""}
        ${view === "settings" ? this._renderSettings() : this._renderProducts()}
      </div>
    `;
  }

  _renderTabs(view) {
    return html`
      <nav class="fd-tabs" aria-label="Bereiche">
        ${this._can("product")
          ? html`
              <button
                type="button"
                class="fd-tab"
                aria-current=${view === "products" ? "page" : "false"}
                @click=${() => this._setView("products")}
              >
                Produkte
              </button>
            `
          : ""}
        ${this._can("app_admin")
          ? html`
              <button
                type="button"
                class="fd-tab"
                aria-current=${view === "settings" ? "page" : "false"}
                @click=${() => this._setView("settings")}
              >
                Einstellungen
              </button>
            `
          : ""}
      </nav>
    `;
  }

  /* --------------------------------------------------------------------- */
  /* Render: Produktliste                                                   */
  /* --------------------------------------------------------------------- */

  _renderProducts() {
    return html`
      <section class="fd-card fd-filters" aria-label="Suche, Filter und Sortierung">
        <div class="fd-filter-head">
          <div class="fd-field fd-field--search">
            <label class="fd-label" for="fd-search">Suche</label>
            <input
              id="fd-search"
              class="fd-input"
              type="search"
              placeholder="Name, Beschreibung, Artikelnummer oder Tag"
              .value=${this._search}
              @input=${(event) => this._onSearchInput(event.target.value)}
            />
          </div>
          <div class="fd-filter-actions">
            <button type="button" class="fd-button" @click=${() => this._openTags()}>Tags verwalten</button>
            <button
              type="button"
              class="fd-button"
              @click=${() => this._resetFilters()}
              ?disabled=${!this._hasActiveFilters()}
            >
              Filter zurücksetzen
            </button>
          </div>
        </div>

        <fieldset class="fd-fieldset">
          <legend class="fd-label">Kategorie</legend>
          <div class="fd-chip-row">
            ${CATEGORY_KEYS.map(
              (key) => html`
                <button
                  type="button"
                  class="fd-chip"
                  aria-pressed=${this._categoryFilter.includes(key) ? "true" : "false"}
                  @click=${() => this._toggleFilterValue("_categoryFilter", key)}
                >
                  ${CATEGORY_LABELS[key]}
                </button>
              `,
            )}
          </div>
          <p class="fd-hint">Mehrere Kategorien werden mit „oder“ verknüpft.</p>
        </fieldset>

        <fieldset class="fd-fieldset">
          <legend class="fd-label">Tags</legend>
          ${this._allTags.length === 0
            ? html`<p class="fd-hint">Noch keine Tags angelegt.</p>`
            : html`
                <div class="fd-chip-row fd-chip-row--scroll">
                  ${this._allTags.map(
                    (tag) => html`
                      <button
                        type="button"
                        class="fd-chip"
                        aria-pressed=${this._tagFilter.includes(tag) ? "true" : "false"}
                        @click=${() => this._toggleFilterValue("_tagFilter", tag)}
                      >
                        ${tag}
                      </button>
                    `,
                  )}
                </div>
              `}
          <p class="fd-hint">Mehrere Tags werden mit „und“ verknüpft.</p>
        </fieldset>

        <div class="fd-filter-grid">
          <div class="fd-field">
            <span class="fd-label" id="fd-flags-label">Bestand und Status</span>
            <div class="fd-switch-row" role="group" aria-labelledby="fd-flags-label">
              <label class="fd-switch">
                <input
                  type="checkbox"
                  .checked=${this._customizableOnly}
                  @change=${(event) => this._setFlag("_customizableOnly", event.target.checked)}
                />
                <span>Personalisierbar</span>
              </label>
              <label class="fd-switch">
                <input
                  type="checkbox"
                  .checked=${this._inStockOnly}
                  @change=${(event) => this._setFlag("_inStockOnly", event.target.checked)}
                />
                <span>Auf Lager</span>
              </label>
              <label class="fd-switch">
                <input
                  type="checkbox"
                  .checked=${this._inCabinetOnly}
                  @change=${(event) => this._setFlag("_inCabinetOnly", event.target.checked)}
                />
                <span>Im Schrank</span>
              </label>
              <label class="fd-switch">
                <input
                  type="checkbox"
                  .checked=${this._showArchived}
                  @change=${(event) => this._setFlag("_showArchived", event.target.checked)}
                />
                <span>Nur archivierte anzeigen</span>
              </label>
            </div>
          </div>

          <fieldset class="fd-fieldset fd-fieldset--inline">
            <legend class="fd-label">Sichtbarkeit</legend>
            <div class="fd-chip-row">
              ${VISIBILITY_OPTIONS.map(
                (option) => html`
                  <button
                    type="button"
                    class="fd-chip"
                    aria-pressed=${this._visibility === option.value ? "true" : "false"}
                    @click=${() => this._setVisibility(option.value)}
                  >
                    ${option.label}
                  </button>
                `,
              )}
            </div>
          </fieldset>

          <div class="fd-field">
            <label class="fd-label" for="fd-sort">Sortierung</label>
            <div class="fd-sort-row">
              <select
                id="fd-sort"
                class="fd-input"
                .value=${this._sort}
                @change=${(event) => this._setSortField(event.target.value)}
              >
                <option value="name">Name</option>
                <option value="price">Preis</option>
              </select>
              <button
                type="button"
                class="fd-button"
                @click=${() => this._toggleSortOrder()}
                aria-label=${`Sortierrichtung: ${this._order === "asc" ? "aufsteigend" : "absteigend"}. Richtung wechseln.`}
              >
                ${this._order === "asc" ? "↑ Aufsteigend" : "↓ Absteigend"}
              </button>
            </div>
          </div>
        </div>
      </section>

      ${this._productsError ? this._renderErrorBanner(this._productsError, () => this._loadProducts()) : ""}

      <p class="fd-list-meta" aria-live="polite">
        ${this._productsLoading ? "Produkte werden geladen …" : this._resultSummary()}
      </p>

      ${this._renderProductGrid()}
      ${this._renderPagination()}
    `;
  }

  _resultSummary() {
    const total = this._total;
    const loaded = this._products.length;
    if (total === 0) {
      return "Keine Produkte gefunden.";
    }
    if (loaded < total) {
      return `${formatNumber(loaded)} von ${formatNumber(total)} Produkten angezeigt.`;
    }
    return total === 1 ? "1 Produkt gefunden." : `${formatNumber(total)} Produkte gefunden.`;
  }

  _renderErrorBanner(message, retry) {
    return html`
      <div class="fd-banner fd-banner--error" role="alert">
        <p>${message}</p>
        ${retry
          ? html`<button type="button" class="fd-button fd-button--small" @click=${() => retry()}>
              Erneut versuchen
            </button>`
          : ""}
      </div>
    `;
  }

  _renderProductGrid() {
    if (this._productsLoading && this._products.length === 0) {
      return html`
        <div class="fd-state fd-state--inline" role="status" aria-live="polite">
          <span class="fd-spinner" aria-hidden="true"></span>
          <p>Produkte werden geladen …</p>
        </div>
      `;
    }
    if (this._products.length === 0) {
      return html`
        <div class="fd-state fd-state--inline">
          <h2>Keine Produkte gefunden</h2>
          <p class="fd-muted">
            ${this._hasActiveFilters()
              ? "Bitte Suche oder Filter anpassen."
              : "Es sind noch keine Produkte angelegt."}
          </p>
          ${this._hasActiveFilters()
            ? html`<button type="button" class="fd-button" @click=${() => this._resetFilters()}>
                Filter zurücksetzen
              </button>`
            : ""}
        </div>
      `;
    }
    return html`
      <ul class="fd-grid" role="list">
        ${this._products.map((product) => this._renderProductCard(product))}
      </ul>
    `;
  }

  _renderProductCard(product) {
    const mainImage = this._mainImage(product);
    const mainSrc = this._imageSrc(this._imageUrl(product, mainImage));
    const archived = Boolean(product.archived);
    const productLabel = product.name || product.articleNumber || "Produkt";

    return html`
      <li class="fd-card fd-product${archived ? " fd-product--archived" : ""}">
        <div class="fd-product-media">
          ${mainSrc
            ? html`<img src=${mainSrc} alt=${`Produktbild von ${productLabel}`} loading="lazy" />`
            : html`<div class="fd-product-placeholder" aria-hidden="true">
                ${String(productLabel).slice(0, 1).toUpperCase()}
              </div>`}
        </div>
        <div class="fd-product-body">
          <div class="fd-product-title-row">
            <h2 class="fd-product-title">${product.name || "(ohne Namen)"}</h2>
            <span class="fd-price">${formatPrice(product.price)}</span>
          </div>
          <p class="fd-muted fd-small">
            ${categoryLabel(product.category)} · ${product.articleNumber}${product.size ? ` · ${product.size}` : ""}
          </p>
          ${product.description ? html`<p class="fd-product-description">${product.description}</p>` : ""}

          <div class="fd-badges">
            ${product.customizable ? html`<span class="fd-badge">Personalisierbar</span>` : ""}
            ${product.visible === false ? html`<span class="fd-badge fd-badge--warning">Nicht sichtbar</span>` : ""}
            ${archived ? html`<span class="fd-badge fd-badge--muted">Archiviert</span>` : ""}
          </div>

          <p class="fd-stock-line">
            Lager: <strong>${formatNumber(product.stock)}</strong> ·
            Schrank: <strong>${formatNumber(product.salesCabinet)}</strong>
          </p>
          ${product.updatedAt
            ? html`<p class="fd-hint">Zuletzt geändert: ${formatDateTime(product.updatedAt)}</p>`
            : ""}

          ${Array.isArray(product.tags) && product.tags.length > 0
            ? html`
                <ul class="fd-tag-list" role="list" aria-label="Tags">
                  ${product.tags.map((tag) => html`<li class="fd-tag">${tag}</li>`)}
                </ul>
              `
            : ""}

          <div class="fd-card-actions">
            <button
              type="button"
              class="fd-button"
              @click=${() => this._openEdit(product)}
              aria-label=${`${productLabel} bearbeiten`}
            >
              Bearbeiten
            </button>
            <button
              type="button"
              class="fd-button"
              @click=${() => this._openImages(product)}
              aria-label=${`Bilder von ${productLabel} verwalten`}
            >
              Bilder
            </button>
            <button
              type="button"
              class="fd-button"
              @click=${() => this._openStock(product)}
              aria-label=${`Bestand von ${productLabel} buchen`}
            >
              Bestand
            </button>
            ${archived
              ? ""
              : html`
                  <button
                    type="button"
                    class="fd-button fd-button--danger"
                    @click=${() => this._requestArchive(product)}
                    aria-label=${`${productLabel} archivieren`}
                  >
                    Archivieren
                  </button>
                `}
          </div>
        </div>
      </li>
    `;
  }

  _renderPagination() {
    if (this._total <= PAGE_SIZE) {
      return "";
    }
    const page = Math.floor(this._offset / PAGE_SIZE) + 1;
    const pages = Math.max(1, Math.ceil(this._total / PAGE_SIZE));
    const from = this._offset + 1;
    const to = Math.min(this._offset + PAGE_SIZE, this._total);

    return html`
      <div class="fd-pagination">
        <p class="fd-muted fd-small">
          ${formatNumber(from)}–${formatNumber(to)} von ${formatNumber(this._total)}
        </p>
        <div class="fd-pagination-actions">
          <button
            type="button"
            class="fd-button"
            @click=${() => this._changePage(-1)}
            ?disabled=${page <= 1 || this._productsLoading}
          >
            Zurück
          </button>
          <span aria-live="polite">Seite ${page} von ${pages}</span>
          <button
            type="button"
            class="fd-button"
            @click=${() => this._changePage(1)}
            ?disabled=${page >= pages || this._productsLoading}
          >
            Weiter
          </button>
        </div>
      </div>
    `;
  }

  /* --------------------------------------------------------------------- */
  /* Render: Editor-Dialog                                                  */
  /* --------------------------------------------------------------------- */

  _renderEditorDialog() {
    const editor = this._editor;
    if (!editor) {
      return "";
    }
    const errors = editor.fieldErrors || {};
    const isCreate = editor.mode === "create";

    return html`
      <dialog
        id="fd-editor-dialog"
        class="fd-dialog"
        aria-labelledby="fd-editor-title"
        @cancel=${(event) => this._handleDialogCancel(event, () => this._closeEditor())}
        @click=${(event) => {
          if (event.target === event.currentTarget) {
            this._closeEditor();
          }
        }}
      >
        <div class="fd-dialog-head">
          <div>
            <h2 id="fd-editor-title">${isCreate ? "Neues Produkt" : "Produkt bearbeiten"}</h2>
            ${isCreate
              ? html`<p class="fd-muted fd-small">Die Artikelnummer wird automatisch vergeben.</p>`
              : html`<p class="fd-muted fd-small">Artikelnummer ${editor.articleNumber}</p>`}
          </div>
          <button
            type="button"
            class="fd-icon-button"
            @click=${() => this._closeEditor()}
            aria-label="Dialog schließen"
          >
            ✕
          </button>
        </div>

        <form class="fd-dialog-body" @submit=${(event) => this._saveEditor(event)} novalidate>
          ${editor.error ? html`<div class="fd-banner fd-banner--error" role="alert">${editor.error}</div>` : ""}

          <div class="fd-form-grid">
            <div class="fd-field">
              <label class="fd-label" for="fd-editor-name">Name *</label>
              <input
                id="fd-editor-name"
                data-field="name"
                data-autofocus
                class="fd-input"
                type="text"
                maxlength="200"
                .value=${editor.name}
                aria-invalid=${errors.name ? "true" : "false"}
                @input=${(event) => this._setEditorField("name", event.target.value)}
              />
              ${errors.name
                ? html`<p class="fd-error-text">${errors.name}</p>`
                : html`<p class="fd-hint">Pflichtfeld.</p>`}
            </div>

            <div class="fd-field">
              <label class="fd-label" for="fd-editor-category">Kategorie *</label>
              ${isCreate
                ? html`
                    <select
                      id="fd-editor-category"
                      data-field="category"
                      class="fd-input"
                      .value=${editor.category}
                      @change=${(event) => this._setEditorField("category", event.target.value)}
                    >
                      ${CATEGORY_KEYS.map(
                        (key) => html`<option value=${key}>${CATEGORY_LABELS[key]}</option>`,
                      )}
                    </select>
                  `
                : html`
                    <input
                      id="fd-editor-category"
                      class="fd-input"
                      type="text"
                      .value=${categoryLabel(editor.category)}
                      disabled
                    />
                  `}
              <p class="fd-hint">
                ${isCreate
                  ? "Die Kategorie kann nach dem Anlegen nicht geändert werden."
                  : "Die Kategorie ist unveränderlich. Für einen Wechsel bitte ein neues Produkt anlegen."}
              </p>
              ${errors.category ? html`<p class="fd-error-text">${errors.category}</p>` : ""}
            </div>

            <div class="fd-field">
              <label class="fd-label" for="fd-editor-price">Preis (EUR) *</label>
              <input
                id="fd-editor-price"
                data-field="price"
                class="fd-input"
                type="text"
                inputmode="decimal"
                placeholder="z. B. 12,50"
                .value=${editor.price}
                aria-invalid=${errors.price ? "true" : "false"}
                @input=${(event) => this._setEditorField("price", event.target.value)}
              />
              ${errors.price
                ? html`<p class="fd-error-text">${errors.price}</p>`
                : html`<p class="fd-hint">Dezimal mit Komma oder Punkt.</p>`}
            </div>

            <div class="fd-field">
              <label class="fd-label" for="fd-editor-size">Größe</label>
              <input
                id="fd-editor-size"
                data-field="size"
                class="fd-input"
                type="text"
                maxlength="200"
                .value=${editor.size}
                @input=${(event) => this._setEditorField("size", event.target.value)}
              />
              ${errors.size ? html`<p class="fd-error-text">${errors.size}</p>` : ""}
            </div>

            <div class="fd-field fd-field--full">
              <label class="fd-label" for="fd-editor-description">Beschreibung</label>
              <textarea
                id="fd-editor-description"
                data-field="description"
                class="fd-input fd-textarea"
                rows="4"
                .value=${editor.description}
                @input=${(event) => this._setEditorField("description", event.target.value)}
              ></textarea>
              ${errors.description ? html`<p class="fd-error-text">${errors.description}</p>` : ""}

              <div class="fd-inline-actions">
                ${editor.mode === "edit"
                  ? html`
                      <button
                        type="button"
                        class="fd-button"
                        @click=${() => this._suggestDescription()}
                        ?disabled=${editor.saving || Boolean(editor.suggestion?.loading)}
                      >
                        ${editor.suggestion?.loading ? "Vorschlag wird erstellt …" : "Beschreibung vorschlagen"}
                      </button>
                      <span class="fd-hint">
                        Der Vorschlag wird erst nach „Übernehmen“ und Speichern wirksam.
                      </span>
                    `
                  : html`<span class="fd-hint">
                      Ein KI-Vorschlag ist verfügbar, sobald das Produkt angelegt ist.
                    </span>`}
              </div>

              ${editor.suggestion?.error
                ? html`<p class="fd-error-text" role="alert">${editor.suggestion.error}</p>`
                : ""}

              ${editor.suggestion && !editor.suggestion.error && !editor.suggestion.loading
                ? html`
                    <div class="fd-suggestion">
                      <label class="fd-label" for="fd-editor-suggestion">Vorschlag (bearbeitbar)</label>
                      <textarea
                        id="fd-editor-suggestion"
                        class="fd-input fd-textarea"
                        rows="4"
                        .value=${editor.suggestion.text}
                        @input=${(event) => this._patchSuggestion(event.target.value)}
                      ></textarea>
                      <div class="fd-card-actions">
                        <button
                          type="button"
                          class="fd-button fd-button--primary"
                          @click=${() => this._applySuggestion()}
                        >
                          Übernehmen
                        </button>
                        <button type="button" class="fd-button" @click=${() => this._discardSuggestion()}>
                          Verwerfen
                        </button>
                      </div>
                    </div>
                  `
                : ""}
            </div>

            <div class="fd-field fd-field--full">
              <span class="fd-label" id="fd-editor-tags-label">Tags</span>
              <ul class="fd-chip-row fd-chip-row--selected" role="list" aria-labelledby="fd-editor-tags-label">
                ${editor.tags.length === 0
                  ? html`<li class="fd-hint">Noch keine Tags gewählt.</li>`
                  : editor.tags.map(
                      (tag) => html`
                        <li class="fd-selected-tag">
                          ${tag}
                          <button
                            type="button"
                            class="fd-icon-button fd-icon-button--small"
                            @click=${() => this._removeEditorTag(tag)}
                            aria-label=${`Tag ${tag} entfernen`}
                          >
                            ✕
                          </button>
                        </li>
                      `,
                    )}
              </ul>

              <div class="fd-tag-input-row">
                <label class="fd-sr-only" for="fd-editor-tag-input">Tag hinzufügen</label>
                <input
                  id="fd-editor-tag-input"
                  class="fd-input"
                  type="text"
                  list="fd-tag-options"
                  placeholder="Tag eingeben"
                  .value=${editor.tagDraft}
                  @input=${(event) => this._setEditorField("tagDraft", event.target.value)}
                  @keydown=${(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      this._addEditorTag(event.target.value);
                    }
                  }}
                />
                <datalist id="fd-tag-options">
                  ${this._allTags.map((tag) => html`<option value=${tag}></option>`)}
                </datalist>
                <button type="button" class="fd-button" @click=${() => this._addEditorTag(editor.tagDraft)}>
                  Hinzufügen
                </button>
              </div>
              ${errors.tags ? html`<p class="fd-error-text">${errors.tags}</p>` : ""}
              ${this._allTags.length > 0
                ? html`
                    <div class="fd-chip-row fd-chip-row--scroll">
                      ${this._allTags
                        .filter((tag) => !editor.tags.includes(tag))
                        .map(
                          (tag) => html`
                            <button type="button" class="fd-chip" @click=${() => this._addEditorTag(tag)}>
                              + ${tag}
                            </button>
                          `,
                        )}
                    </div>
                  `
                : ""}
            </div>

            <div class="fd-field fd-field--full">
              <div class="fd-switch-row">
                <label class="fd-switch">
                  <input
                    type="checkbox"
                    .checked=${editor.visible}
                    @change=${(event) => this._setEditorField("visible", event.target.checked)}
                  />
                  <span>Sichtbar auf der Website</span>
                </label>
                <label class="fd-switch">
                  <input
                    type="checkbox"
                    .checked=${editor.customizable}
                    @change=${(event) => this._setEditorField("customizable", event.target.checked)}
                  />
                  <span>Personalisierbar</span>
                </label>
              </div>
            </div>
          </div>

          <footer class="fd-dialog-foot">
            <button type="button" class="fd-button" @click=${() => this._closeEditor()} ?disabled=${editor.saving}>
              Abbrechen
            </button>
            <button type="submit" class="fd-button fd-button--primary" ?disabled=${editor.saving}>
              ${editor.saving ? "Speichern …" : "Speichern"}
            </button>
          </footer>
        </form>
      </dialog>
    `;
  }

  /* --------------------------------------------------------------------- */
  /* Render: Bildverwaltung                                                 */
  /* --------------------------------------------------------------------- */

  _onDeleteImageClick(name) {
    const dialog = this._imagesDialog;
    if (!dialog || dialog.busy) {
      return;
    }
    if (dialog.pendingDelete === name) {
      this._deleteImage(name);
      return;
    }
    this._requestDeleteImage(name);
  }

  _renderImagesDialog() {
    const dialog = this._imagesDialog;
    if (!dialog) {
      return "";
    }
    const product = dialog.product;
    const productLabel = product.name || product.articleNumber || "Produkt";
    const count = dialog.images.length;
    const busy = dialog.busy || dialog.loading;

    return html`
      <dialog
        id="fd-images-dialog"
        class="fd-dialog fd-dialog--wide"
        aria-labelledby="fd-images-title"
        @cancel=${(event) => this._handleDialogCancel(event, () => this._closeImages())}
        @click=${(event) => {
          if (event.target === event.currentTarget) {
            this._closeImages();
          }
        }}
      >
        <div class="fd-dialog-head">
          <div>
            <h2 id="fd-images-title">Bilder – ${productLabel}</h2>
            <p class="fd-muted fd-small">
              ${count} von ${MAX_IMAGES} Bildern · Artikelnummer ${product.articleNumber}
            </p>
          </div>
          <button
            type="button"
            class="fd-icon-button"
            @click=${() => this._closeImages()}
            ?disabled=${dialog.busy}
            aria-label="Dialog schließen"
          >
            ✕
          </button>
        </div>

        <div class="fd-dialog-body">
          ${dialog.error ? html`<div class="fd-banner fd-banner--error" role="alert">${dialog.error}</div>` : ""}
          ${dialog.notice ? html`<div class="fd-banner fd-banner--success" role="status">${dialog.notice}</div>` : ""}

          <div class="fd-upload">
            <label class="fd-label" for="fd-image-upload">Bilder hochladen (maximal ${MAX_IMAGES})</label>
            <input
              id="fd-image-upload"
              class="fd-input fd-file"
              type="file"
              accept="image/jpeg,image/png,image/webp,image/gif"
              multiple
              ?disabled=${busy}
              @change=${(event) => this._onImagesSelected(event)}
            />
            <p class="fd-hint">
              ${count >= MAX_IMAGES
                ? "Die maximale Anzahl ist erreicht. Bitte zuerst ein Bild löschen."
                : "Mehrere Dateien möglich. Ein elfter Upload wird ohne Teilübernahme abgelehnt."}
            </p>
            ${dialog.busy
              ? html`<p class="fd-hint" role="status">
                  <span class="fd-spinner" aria-hidden="true"></span> Bilder werden übertragen …
                </p>`
              : ""}
          </div>

          ${dialog.loading && count === 0
            ? html`<p class="fd-muted" role="status">
                <span class="fd-spinner" aria-hidden="true"></span> Bilder werden geladen …
              </p>`
            : ""}

          ${!dialog.loading && count === 0
            ? html`<p class="fd-state fd-state--inline">Noch keine Bilder hinterlegt.</p>`
            : ""}

          <ul class="fd-image-grid" role="list" aria-label="Bilder in Reihenfolge">
            ${dialog.images.map((image, index) => {
              const state = this._imageState(this._imageUrl(product, image));
              const src = state.src;
              return html`
                <li class="fd-image-card">
                  <div class="fd-image-preview">
                    ${src
                      ? html`<img
                          src=${src}
                          alt=${image.is_main
                            ? `Hauptbild von ${productLabel}`
                            : `Bild ${index + 1} von ${productLabel}`}
                          loading="lazy"
                        />`
                      : html`<div class="fd-image-loading">
                          ${state.status === "error"
                            ? html`<span class="fd-image-error">Bild nicht verfügbar</span>`
                            : html`<span class="fd-spinner" aria-hidden="true"></span>
                                <span class="fd-sr-only">Bild wird geladen …</span>`}
                        </div>`}
                    ${image.is_main ? html`<span class="fd-badge fd-badge--main">Hauptbild</span>` : ""}
                  </div>
                  <p class="fd-image-name" title=${image.name}>${image.name}</p>
                  <div class="fd-image-actions">
                    <button
                      type="button"
                      class="fd-button fd-button--small"
                      @click=${() => this._moveImage(index, -1)}
                      ?disabled=${busy || index === 0}
                      aria-label=${`Bild ${index + 1} nach links verschieben`}
                    >
                      ←
                    </button>
                    <button
                      type="button"
                      class="fd-button fd-button--small"
                      @click=${() => this._moveImage(index, 1)}
                      ?disabled=${busy || index === dialog.images.length - 1}
                      aria-label=${`Bild ${index + 1} nach rechts verschieben`}
                    >
                      →
                    </button>
                    <button
                      type="button"
                      class="fd-button fd-button--small"
                      @click=${() => this._setMainImage(image.name)}
                      ?disabled=${busy || image.is_main}
                      aria-label=${`Bild ${index + 1} als Hauptbild setzen`}
                    >
                      Als Hauptbild
                    </button>
                    <button
                      type="button"
                      class="fd-button fd-button--small fd-button--danger"
                      @click=${() => this._onDeleteImageClick(image.name)}
                      ?disabled=${busy}
                      aria-label=${`Bild ${index + 1} löschen`}
                    >
                      ${dialog.pendingDelete === image.name ? "Wirklich löschen" : "Löschen"}
                    </button>
                  </div>
                  ${dialog.pendingDelete === image.name
                    ? html`<p class="fd-hint">Erneut „Wirklich löschen“ wählen, um das Bild zu entfernen.</p>`
                    : ""}
                </li>
              `;
            })}
          </ul>
        </div>

        <div class="fd-dialog-foot">
          <button type="button" class="fd-button" @click=${() => this._closeImages()} ?disabled=${dialog.busy}>
            Schließen
          </button>
        </div>
      </dialog>
    `;
  }

  /* --------------------------------------------------------------------- */
  /* Render: Bestandsbuchung                                                */
  /* --------------------------------------------------------------------- */

  _patchStock(patch) {
    if (!this._stockDialog) {
      return;
    }
    this._stockDialog = { ...this._stockDialog, ...patch, error: "" };
  }

  _renderStockDialog() {
    const dialog = this._stockDialog;
    if (!dialog) {
      return "";
    }
    const product = dialog.product;
    const productLabel = product.name || product.articleNumber || "Produkt";
    const typeEntry = STOCK_TYPES.find((entry) => entry.value === dialog.type) || STOCK_TYPES[0];

    return html`
      <dialog
        id="fd-stock-dialog"
        class="fd-dialog"
        aria-labelledby="fd-stock-title"
        @cancel=${(event) => this._handleDialogCancel(event, () => this._closeStock())}
        @click=${(event) => {
          if (event.target === event.currentTarget) {
            this._closeStock();
          }
        }}
      >
        <div class="fd-dialog-head">
          <div>
            <h2 id="fd-stock-title">Bestandsbuchung</h2>
            <p class="fd-muted fd-small">${productLabel} · Artikelnummer ${product.articleNumber}</p>
          </div>
          <button
            type="button"
            class="fd-icon-button"
            @click=${() => this._closeStock()}
            ?disabled=${dialog.saving}
            aria-label="Dialog schließen"
          >
            ✕
          </button>
        </div>

        <form class="fd-dialog-body" @submit=${(event) => this._saveStock(event)} novalidate>
          ${dialog.error ? html`<div class="fd-banner fd-banner--error" role="alert">${dialog.error}</div>` : ""}

          <dl class="fd-stock-summary">
            <div>
              <dt>Lager</dt>
              <dd>${formatNumber(product.stock)}</dd>
            </div>
            <div>
              <dt>Verkaufsschrank</dt>
              <dd>${formatNumber(product.salesCabinet)}</dd>
            </div>
          </dl>

          <div class="fd-form-grid">
            <div class="fd-field">
              <label class="fd-label" for="fd-stock-type">Buchungsart</label>
              <select
                id="fd-stock-type"
                class="fd-input"
                .value=${dialog.type}
                @change=${(event) => this._patchStock({ type: event.target.value })}
              >
                ${STOCK_TYPES.map((entry) => html`<option value=${entry.value}>${entry.label}</option>`)}
              </select>
              <p class="fd-hint">${typeEntry.hint}</p>
            </div>

            <div class="fd-field">
              <label class="fd-label" for="fd-stock-quantity">Menge *</label>
              <input
                id="fd-stock-quantity"
                data-autofocus
                class="fd-input"
                type="number"
                min="0"
                step="1"
                inputmode="numeric"
                .value=${dialog.quantity}
                @input=${(event) => this._patchStock({ quantity: event.target.value })}
              />
              <p class="fd-hint">Ganze Zahl, mindestens 1.</p>
            </div>

            <div class="fd-field fd-field--full">
              <label class="fd-label" for="fd-stock-note">Notiz</label>
              <input
                id="fd-stock-note"
                class="fd-input"
                type="text"
                maxlength="200"
                placeholder="Optional, zum Beispiel Belegnummer"
                .value=${dialog.note}
                @input=${(event) => this._patchStock({ note: event.target.value })}
              />
            </div>
          </div>

          <p class="fd-hint">
            Buchungen werden mit Zeitstempel und auslösendem Home-Assistant-Benutzer erfasst.
            Personalisierbare Produkte werden durch Verkäufe nicht automatisch abgebucht.
          </p>

          <div class="fd-dialog-foot">
            <button type="button" class="fd-button" @click=${() => this._closeStock()} ?disabled=${dialog.saving}>
              Abbrechen
            </button>
            <button type="submit" class="fd-button fd-button--primary" ?disabled=${dialog.saving}>
              ${dialog.saving ? "Buchen …" : "Buchen"}
            </button>
          </div>
        </form>
      </dialog>
    `;
  }

  /* --------------------------------------------------------------------- */
  /* Render: Archivierung                                                   */
  /* --------------------------------------------------------------------- */

  _renderArchiveDialog() {
    const dialog = this._archiveDialog;
    if (!dialog) {
      return "";
    }
    const product = dialog.product;
    const productLabel = product.name || product.articleNumber || "Produkt";

    return html`
      <dialog
        id="fd-archive-dialog"
        class="fd-dialog"
        aria-labelledby="fd-archive-title"
        @cancel=${(event) => this._handleDialogCancel(event, () => this._closeArchive())}
        @click=${(event) => {
          if (event.target === event.currentTarget) {
            this._closeArchive();
          }
        }}
      >
        <div class="fd-dialog-head">
          <div>
            <h2 id="fd-archive-title">Produkt archivieren</h2>
            <p class="fd-muted fd-small">${productLabel} · Artikelnummer ${product.articleNumber}</p>
          </div>
          <button
            type="button"
            class="fd-icon-button"
            @click=${() => this._closeArchive()}
            ?disabled=${dialog.saving}
            aria-label="Dialog schließen"
          >
            ✕
          </button>
        </div>

        <div class="fd-dialog-body">
          ${dialog.error ? html`<div class="fd-banner fd-banner--error" role="alert">${dialog.error}</div>` : ""}
          <div class="fd-banner fd-banner--warning" role="note">
            <p>
              Das Produkt verschwindet aus der Website und aus dem Export. Die Daten und Bilder bleiben
              erhalten und können über „Archivierte anzeigen“ weiterhin eingesehen werden.
            </p>
          </div>
          <p class="fd-hint">Ein endgültiges Löschen ist nicht vorgesehen.</p>
          <div class="fd-dialog-foot">
            <button type="button" class="fd-button" @click=${() => this._closeArchive()} ?disabled=${dialog.saving}>
              Abbrechen
            </button>
            <button
              type="button"
              class="fd-button fd-button--danger"
              @click=${() => this._confirmArchive()}
              ?disabled=${dialog.saving}
            >
              ${dialog.saving ? "Archivieren …" : "Archivieren"}
            </button>
          </div>
        </div>
      </dialog>
    `;
  }

  /* --------------------------------------------------------------------- */
  /* Render: Tag-Verwaltung                                                 */
  /* --------------------------------------------------------------------- */

  _setTagDraft(value) {
    if (!this._tagsDialog) {
      return;
    }
    this._tagsDialog = { ...this._tagsDialog, draft: value, error: "", notice: "" };
  }

  _onDeleteTagClick(name) {
    const dialog = this._tagsDialog;
    if (!dialog || dialog.busy) {
      return;
    }
    if (dialog.pendingDelete === name) {
      this._deleteTag(name);
      return;
    }
    this._requestDeleteTag(name);
  }

  _renderTagsDialog() {
    const dialog = this._tagsDialog;
    if (!dialog) {
      return "";
    }

    return html`
      <dialog
        id="fd-tags-dialog"
        class="fd-dialog"
        aria-labelledby="fd-tags-title"
        @cancel=${(event) => this._handleDialogCancel(event, () => this._closeTags())}
        @click=${(event) => {
          if (event.target === event.currentTarget) {
            this._closeTags();
          }
        }}
      >
        <div class="fd-dialog-head">
          <div>
            <h2 id="fd-tags-title">Tags verwalten</h2>
            <p class="fd-muted fd-small">Tags sind öffentliche Filter der Website.</p>
          </div>
          <button
            type="button"
            class="fd-icon-button"
            @click=${() => this._closeTags()}
            ?disabled=${dialog.busy}
            aria-label="Dialog schließen"
          >
            ✕
          </button>
        </div>

        <div class="fd-dialog-body">
          ${dialog.error ? html`<div class="fd-banner fd-banner--error" role="alert">${dialog.error}</div>` : ""}
          ${dialog.notice ? html`<div class="fd-banner fd-banner--success" role="status">${dialog.notice}</div>` : ""}

          <form class="fd-tag-input-row" @submit=${(event) => this._addTag(event)}>
            <label class="fd-sr-only" for="fd-tag-draft">Neuer Tag</label>
            <input
              id="fd-tag-draft"
              data-autofocus
              class="fd-input"
              type="text"
              maxlength="40"
              placeholder="Neuer Tag"
              .value=${dialog.draft}
              @input=${(event) => this._setTagDraft(event.target.value)}
            />
            <button type="submit" class="fd-button fd-button--primary" ?disabled=${dialog.busy}>Anlegen</button>
          </form>

          ${dialog.loading ? html`<p class="fd-muted" role="status">Tags werden geladen …</p>` : ""}
          ${!dialog.loading && dialog.tags.length === 0
            ? html`<p class="fd-state fd-state--inline">Noch keine Tags angelegt.</p>`
            : ""}

          <ul class="fd-tag-manage-list" role="list">
            ${dialog.tags.map(
              (tag) => html`
                <li class="fd-tag-manage">
                  <span>${tag}</span>
                  <button
                    type="button"
                    class="fd-button fd-button--small${dialog.pendingDelete === tag ? " fd-button--danger" : ""}"
                    @click=${() => this._onDeleteTagClick(tag)}
                    ?disabled=${dialog.busy}
                    aria-label=${`Tag ${tag} löschen`}
                  >
                    ${dialog.pendingDelete === tag ? "Wirklich löschen" : "Löschen"}
                  </button>
                </li>
              `,
            )}
          </ul>
        </div>

        <div class="fd-dialog-foot">
          <button type="button" class="fd-button" @click=${() => this._closeTags()} ?disabled=${dialog.busy}>
            Schließen
          </button>
        </div>
      </dialog>
    `;
  }

  /* --------------------------------------------------------------------- */
  /* Render: Einstellungen                                                  */
  /* --------------------------------------------------------------------- */

  _renderSettings() {
    if (!this._can("app_admin")) {
      return html`
        <section class="fd-card fd-state">
          <h2>Kein Zugriff</h2>
          <p class="fd-muted">Die Einstellungen sind der Anwendungsverwaltung vorbehalten.</p>
        </section>
      `;
    }

    const form = this._settingsForm;
    const keySet = Boolean(this._settingsMeta.api_key_set);

    return html`
      <section class="fd-card fd-settings" aria-labelledby="fd-settings-title">
        <h2 id="fd-settings-title">KI-Beschreibungsvorschläge</h2>
        <p class="fd-muted fd-small">
          Zugangsdaten bleiben serverseitig gespeichert und werden nie an die Oberfläche zurückgegeben.
        </p>

        ${this._settingsLoading ? html`<p class="fd-muted" role="status">Einstellungen werden geladen …</p>` : ""}
        ${this._settingsError ? html`<div class="fd-banner fd-banner--error" role="alert">${this._settingsError}</div>` : ""}
        ${this._settingsNotice
          ? html`<div class="fd-banner fd-banner--success" role="status">${this._settingsNotice}</div>`
          : ""}

        <form class="fd-form-grid" @submit=${(event) => this._saveSettings(event)} novalidate>
          <div class="fd-field fd-field--full">
            <label class="fd-label" for="fd-settings-endpoint">KI-Endpunkt</label>
            <input
              id="fd-settings-endpoint"
              class="fd-input"
              type="url"
              placeholder="https://api.example.com/v1/chat/completions"
              .value=${form.ai_endpoint}
              @input=${(event) => this._setSettingsField("ai_endpoint", event.target.value)}
            />
            <p class="fd-hint">Vollständige URL des Chat-Completion-Endpunkts.</p>
          </div>

          <div class="fd-field">
            <label class="fd-label" for="fd-settings-model">Modell</label>
            <input
              id="fd-settings-model"
              class="fd-input"
              type="text"
              placeholder="deepseek-flash"
              .value=${form.ai_model}
              @input=${(event) => this._setSettingsField("ai_model", event.target.value)}
            />
          </div>

          <div class="fd-field">
            <label class="fd-label" for="fd-settings-key">API-Key</label>
            <input
              id="fd-settings-key"
              class="fd-input"
              type="password"
              autocomplete="new-password"
              placeholder=${keySet ? "•••••••• (gesetzt)" : "Noch kein Schlüssel gesetzt"}
              .value=${form.api_key}
              @input=${(event) => this._setSettingsField("api_key", event.target.value)}
            />
            <p class="fd-hint">
              ${keySet ? "API-Key gesetzt." : "Noch kein API-Key gesetzt."}
              Der Schlüssel wird ausschließlich gespeichert und nie angezeigt.
            </p>
          </div>

          <div class="fd-field fd-field--full">
            <label class="fd-label" for="fd-settings-prompt">Prompt</label>
            <textarea
              id="fd-settings-prompt"
              class="fd-input fd-textarea"
              rows="5"
              .value=${form.ai_prompt}
              @input=${(event) => this._setSettingsField("ai_prompt", event.target.value)}
            ></textarea>
          </div>

          <div class="fd-field fd-field--full">
            <div class="fd-inline-actions">
              <button
                type="submit"
                class="fd-button fd-button--primary"
                ?disabled=${this._settingsSaving || this._settingsLoading}
              >
                ${this._settingsSaving ? "Speichern …" : "Einstellungen speichern"}
              </button>
              <button
                type="button"
                class="fd-button"
                @click=${() => this._loadSettings()}
                ?disabled=${this._settingsLoading}
              >
                Neu laden
              </button>
              <span class="fd-status ${keySet ? "fd-status--ok" : "fd-status--warn"}">
                ${keySet ? "API-Key gesetzt" : "API-Key fehlt"}
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
      color: var(--primary-text-color, #212121);
      background-color: var(--primary-background-color, transparent);
      font-family: var(
        --paper-font-body1_-_font-family,
        var(--ha-font-family-body, Roboto, "Helvetica Neue", Arial, sans-serif)
      );
      font-size: var(--ha-font-size-m, 14px);
      line-height: 1.5;

      --fd-space-1: 4px;
      --fd-space-2: 8px;
      --fd-space-3: 12px;
      --fd-space-4: 16px;
      --fd-space-5: 20px;
      --fd-space-6: 24px;
      --fd-space-8: 32px;

      --fd-radius-lg: var(--ha-card-border-radius, 12px);
      --fd-radius: 10px;
      --fd-radius-sm: 8px;

      --fd-surface: var(--card-background-color, #ffffff);
      --fd-surface-alt: var(--secondary-background-color, rgba(127, 127, 127, 0.08));
      --fd-text: var(--primary-text-color, #212121);
      --fd-text-muted: var(--secondary-text-color, #6b6b6b);
      --fd-accent: var(--primary-color, #03a9f4);
      --fd-on-accent: var(--text-primary-color, #ffffff);
      --fd-error: var(--error-color, #c62828);
      --fd-warning: var(--warning-color, #ff9800);
      --fd-success: var(--success-color, #2e7d32);
      --fd-divider: var(--divider-color, rgba(0, 0, 0, 0.12));
      --fd-shadow: var(--ha-card-box-shadow, 0 1px 3px rgba(0, 0, 0, 0.12));
      --fd-shadow-dialog: 0 12px 40px rgba(0, 0, 0, 0.35);

      --fd-font-small: var(--ha-font-size-s, 13px);
      --fd-focus-ring: 2px solid var(--fd-accent);
    }

    *,
    *::before,
    *::after {
      box-sizing: border-box;
    }

    :where(button, input, select, textarea, [tabindex]):focus-visible {
      outline: var(--fd-focus-ring);
      outline-offset: 2px;
    }

    /* Layout ------------------------------------------------------------- */

    .fd-shell {
      display: flex;
      flex-direction: column;
      gap: var(--fd-space-4);
      max-width: 1440px;
      margin: 0 auto;
      padding: var(--fd-space-4);
    }

    .fd-header {
      display: flex;
      flex-wrap: wrap;
      align-items: flex-start;
      justify-content: space-between;
      gap: var(--fd-space-3);
    }

    .fd-header-text h1 {
      margin: 0;
      font-size: 1.6rem;
      font-weight: 500;
      line-height: 1.25;
    }

    .fd-header-text p {
      margin: 4px 0 0;
    }

    .fd-header-actions {
      display: flex;
      flex-wrap: wrap;
      gap: var(--fd-space-2);
    }

    .fd-role-list {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      list-style: none;
      margin: var(--fd-space-2) 0 0;
      padding: 0;
    }

    .fd-role {
      padding: 2px 10px;
      border: 1px solid var(--fd-divider);
      border-radius: 999px;
      background: var(--fd-surface-alt);
      color: var(--fd-text-muted);
      font-size: var(--fd-font-small);
    }

    .fd-tabs {
      display: flex;
      gap: var(--fd-space-1);
      border-bottom: 1px solid var(--fd-divider);
    }

    .fd-tab {
      appearance: none;
      padding: 10px 14px;
      border: none;
      border-bottom: 3px solid transparent;
      background: none;
      color: var(--fd-text-muted);
      font: inherit;
      cursor: pointer;
    }

    .fd-tab:hover {
      color: var(--fd-text);
    }

    .fd-tab[aria-current="page"] {
      border-bottom-color: var(--fd-accent);
      color: var(--fd-accent);
      font-weight: 500;
    }

    /* Karten ------------------------------------------------------------- */

    .fd-card {
      background: var(--fd-surface);
      border: 1px solid var(--fd-divider);
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

    .fd-state h1,
    .fd-state h2 {
      margin: 0;
      font-weight: 500;
    }

    .fd-state--inline {
      border: 1px dashed var(--fd-divider);
      border-radius: var(--fd-radius-lg);
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

    /* Formularelemente ---------------------------------------------------- */

    .fd-label {
      color: var(--fd-text-muted);
      font-size: var(--fd-font-small);
      font-weight: 500;
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

    .fd-error-text {
      margin: 0;
      color: var(--fd-error);
      font-size: var(--fd-font-small);
      font-weight: 500;
    }

    .fd-sr-only {
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0 0 0 0);
      white-space: nowrap;
      border: 0;
    }

    .fd-input {
      width: 100%;
      min-height: 40px;
      padding: 10px 12px;
      border: 1px solid var(--fd-divider);
      border-radius: var(--fd-radius-sm);
      background: var(--fd-surface);
      color: var(--fd-text);
      font: inherit;
    }

    .fd-input:focus-visible {
      border-color: var(--fd-accent);
    }

    .fd-input:disabled {
      background: var(--fd-surface-alt);
      color: var(--fd-text-muted);
      cursor: not-allowed;
    }

    .fd-textarea {
      min-height: 96px;
      resize: vertical;
    }

    .fd-file {
      padding: 8px;
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

    .fd-fieldset {
      min-width: 0;
      margin: 0;
      padding: var(--fd-space-3);
      border: 1px solid var(--fd-divider);
      border-radius: var(--fd-radius);
    }

    .fd-fieldset legend {
      padding: 0 6px;
    }

    .fd-fieldset--inline {
      padding: 0;
      border: none;
    }

    .fd-switch-row {
      display: flex;
      flex-wrap: wrap;
      gap: var(--fd-space-2) var(--fd-space-4);
    }

    .fd-switch {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-height: 36px;
      cursor: pointer;
    }

    .fd-switch input {
      width: 18px;
      height: 18px;
      accent-color: var(--fd-accent);
      cursor: pointer;
    }

    /* Buttons ------------------------------------------------------------- */

    .fd-button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      min-height: 38px;
      padding: 8px 14px;
      border: 1px solid var(--fd-divider);
      border-radius: var(--fd-radius-sm);
      background: var(--fd-surface);
      color: var(--fd-text);
      font: inherit;
      cursor: pointer;
    }

    .fd-button:hover:not(:disabled) {
      background: var(--fd-surface-alt);
    }

    .fd-button:disabled {
      opacity: 0.55;
      cursor: not-allowed;
    }

    .fd-button--primary {
      border-color: transparent;
      background: var(--fd-accent);
      color: var(--fd-on-accent);
    }

    .fd-button--primary:hover:not(:disabled) {
      background: var(--fd-accent);
      filter: brightness(1.08);
    }

    .fd-button--danger {
      border-color: var(--fd-error);
      color: var(--fd-error);
      background: transparent;
    }

    .fd-button--small {
      min-height: 30px;
      padding: 4px 8px;
      font-size: var(--fd-font-small);
    }

    .fd-icon-button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      flex: none;
      width: 36px;
      height: 36px;
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

    .fd-icon-button:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }

    .fd-icon-button--small {
      width: 24px;
      height: 24px;
      font-size: 11px;
    }

    /* Chips und Tags ------------------------------------------------------ */

    .fd-chip-row {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: var(--fd-space-2);
      list-style: none;
      margin: var(--fd-space-2) 0 0;
      padding: 0;
    }

    .fd-chip-row--scroll {
      max-height: 124px;
      overflow-y: auto;
      padding-right: 4px;
    }

    .fd-chip-row--selected {
      align-items: center;
      min-height: 34px;
    }

    .fd-chip {
      appearance: none;
      min-height: 34px;
      padding: 6px 12px;
      border: 1px solid var(--fd-divider);
      border-radius: 999px;
      background: var(--fd-surface);
      color: var(--fd-text);
      font: inherit;
      font-size: var(--fd-font-small);
      cursor: pointer;
    }

    .fd-chip:hover {
      background: var(--fd-surface-alt);
    }

    .fd-chip[aria-pressed="true"] {
      border-color: var(--fd-accent);
      background: var(--fd-accent);
      color: var(--fd-on-accent);
    }

    .fd-selected-tag {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 4px 4px 4px 12px;
      border: 1px solid var(--fd-divider);
      border-radius: 999px;
      background: var(--fd-surface-alt);
      font-size: var(--fd-font-small);
    }

    .fd-tag-input-row {
      display: flex;
      flex-wrap: wrap;
      gap: var(--fd-space-2);
      align-items: center;
      margin-top: var(--fd-space-2);
    }

    .fd-tag-input-row .fd-input {
      flex: 1 1 200px;
      width: auto;
    }

    .fd-tag-list {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
      list-style: none;
      margin: 0;
      padding: 0;
    }

    .fd-tag {
      padding: 2px 8px;
      border-radius: 4px;
      background: var(--fd-surface-alt);
      color: var(--fd-text-muted);
      font-size: 11px;
    }

    /* Banner und Status --------------------------------------------------- */

    .fd-banner {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: var(--fd-space-2);
      padding: var(--fd-space-3);
      border-left: 4px solid var(--fd-divider);
      border-radius: var(--fd-radius);
      background: var(--fd-surface-alt);
    }

    .fd-banner p {
      margin: 0;
    }

    .fd-banner--error {
      border-left-color: var(--fd-error);
    }

    .fd-banner--warning {
      border-left-color: var(--fd-warning);
    }

    .fd-banner--success {
      border-left-color: var(--fd-success);
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
      border-radius: 999px;
      font-size: var(--fd-font-small);
    }

    .fd-status--ok {
      border-color: var(--fd-success);
      color: var(--fd-text);
    }

    .fd-status--warn {
      border-color: var(--fd-warning);
      color: var(--fd-text);
    }

    /* Filter -------------------------------------------------------------- */

    .fd-filters {
      display: flex;
      flex-direction: column;
      gap: var(--fd-space-3);
      padding: var(--fd-space-4);
    }

    .fd-filter-head {
      display: flex;
      flex-wrap: wrap;
      align-items: flex-end;
      gap: var(--fd-space-3);
    }

    .fd-field--search {
      flex: 1 1 280px;
    }

    .fd-filter-actions {
      display: flex;
      flex-wrap: wrap;
      gap: var(--fd-space-2);
    }

    .fd-filter-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: var(--fd-space-3);
      align-items: start;
    }

    .fd-sort-row {
      display: flex;
      flex-wrap: wrap;
      gap: var(--fd-space-2);
    }

    .fd-sort-row .fd-input {
      flex: 1 1 120px;
      width: auto;
    }

    .fd-list-meta {
      margin: 0;
      color: var(--fd-text-muted);
      font-size: var(--fd-font-small);
    }

    /* Produktliste -------------------------------------------------------- */

    .fd-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
      gap: var(--fd-space-3);
      list-style: none;
      margin: 0;
      padding: 0;
    }

    .fd-product {
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }

    .fd-product--archived {
      opacity: 0.72;
    }

    .fd-product-media {
      aspect-ratio: 4 / 3;
      overflow: hidden;
      background: var(--fd-surface-alt);
    }

    .fd-product-media img {
      display: block;
      width: 100%;
      height: 100%;
      object-fit: cover;
    }

    .fd-product-placeholder {
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100%;
      color: var(--fd-text-muted);
      font-size: 2.5rem;
    }

    .fd-product-body {
      display: flex;
      flex: 1;
      flex-direction: column;
      gap: var(--fd-space-2);
      padding: var(--fd-space-3);
    }

    .fd-product-title-row {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: var(--fd-space-2);
    }

    .fd-product-title {
      margin: 0;
      font-size: 1rem;
      font-weight: 600;
      line-height: 1.3;
    }

    .fd-price {
      color: var(--fd-text);
      font-weight: 600;
      white-space: nowrap;
    }

    .fd-product-description {
      display: -webkit-box;
      margin: 0;
      overflow: hidden;
      color: var(--fd-text-muted);
      font-size: var(--fd-font-small);
      -webkit-line-clamp: 3;
      -webkit-box-orient: vertical;
    }

    .fd-badges {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }

    .fd-badge {
      padding: 2px 8px;
      border: 1px solid var(--fd-divider);
      border-radius: 999px;
      background: var(--fd-surface-alt);
      color: var(--fd-text-muted);
      font-size: 11px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }

    .fd-badge--warning {
      border-color: var(--fd-warning);
      color: var(--fd-text);
    }

    .fd-badge--muted {
      border-color: var(--fd-divider);
      color: var(--fd-text-muted);
    }

    .fd-badge--main {
      position: absolute;
      top: 6px;
      left: 6px;
      border-color: transparent;
      background: var(--fd-accent);
      color: var(--fd-on-accent);
    }

    .fd-stock-line {
      margin: 0;
      font-size: var(--fd-font-small);
    }

    .fd-card-actions {
      display: flex;
      flex-wrap: wrap;
      gap: var(--fd-space-2);
      margin-top: auto;
    }

    .fd-pagination {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: var(--fd-space-3);
      padding: var(--fd-space-2) 0;
    }

    .fd-pagination-actions {
      display: flex;
      align-items: center;
      gap: var(--fd-space-2);
    }

    /* Dialoge ------------------------------------------------------------- */

    .fd-dialog {
      width: min(720px, calc(100vw - 32px));
      max-height: calc(100vh - 48px);
      padding: 0;
      border: none;
      border-radius: var(--fd-radius-lg);
      background: var(--fd-surface);
      color: var(--fd-text);
      box-shadow: var(--fd-shadow-dialog);
    }

    .fd-dialog--wide {
      width: min(980px, calc(100vw - 32px));
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
      align-items: flex-start;
      justify-content: space-between;
      gap: var(--fd-space-3);
      padding: var(--fd-space-3) var(--fd-space-4);
      border-bottom: 1px solid var(--fd-divider);
    }

    .fd-dialog-head h2 {
      margin: 0;
      font-size: 1.15rem;
      font-weight: 500;
    }

    .fd-dialog-body {
      display: flex;
      flex: 1;
      flex-direction: column;
      gap: var(--fd-space-4);
      padding: var(--fd-space-4);
      overflow-y: auto;
    }

    .fd-dialog-foot {
      display: flex;
      flex-wrap: wrap;
      justify-content: flex-end;
      gap: var(--fd-space-2);
      padding: var(--fd-space-3) var(--fd-space-4);
      border-top: 1px solid var(--fd-divider);
    }

    /* Bilder -------------------------------------------------------------- */

    .fd-upload {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }

    .fd-image-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
      gap: var(--fd-space-3);
      list-style: none;
      margin: 0;
      padding: 0;
    }

    .fd-image-card {
      display: flex;
      flex-direction: column;
      gap: 6px;
      padding: var(--fd-space-2);
      border: 1px solid var(--fd-divider);
      border-radius: var(--fd-radius);
    }

    .fd-image-preview {
      position: relative;
      aspect-ratio: 4 / 3;
      overflow: hidden;
      border-radius: var(--fd-radius-sm);
      background: var(--fd-surface-alt);
    }

    .fd-image-preview img {
      display: block;
      width: 100%;
      height: 100%;
      object-fit: cover;
    }

    .fd-image-loading {
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100%;
    }

    .fd-image-error {
      color: var(--fd-text-muted);
      font-size: 11px;
      text-align: center;
      padding: 0 8px;
    }

    .fd-image-name {
      margin: 0;
      overflow: hidden;
      color: var(--fd-text-muted);
      font-size: 11px;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .fd-image-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
    }

    /* Bestand ------------------------------------------------------------- */

    .fd-stock-summary {
      display: flex;
      flex-wrap: wrap;
      gap: var(--fd-space-6);
      margin: 0;
      padding: var(--fd-space-3);
      border-radius: var(--fd-radius);
      background: var(--fd-surface-alt);
    }

    .fd-stock-summary dt {
      color: var(--fd-text-muted);
      font-size: var(--fd-font-small);
    }

    .fd-stock-summary dd {
      margin: 0;
      font-size: 1.4rem;
      font-weight: 600;
    }

    /* Tag-Verwaltung ------------------------------------------------------ */

    .fd-tag-manage-list {
      display: flex;
      flex-direction: column;
      gap: var(--fd-space-2);
      list-style: none;
      margin: 0;
      padding: 0;
    }

    .fd-tag-manage {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--fd-space-3);
      padding: 8px 10px;
      border: 1px solid var(--fd-divider);
      border-radius: var(--fd-radius-sm);
    }

    /* Einstellungen ------------------------------------------------------- */

    .fd-settings {
      display: flex;
      flex-direction: column;
      gap: var(--fd-space-3);
      padding: var(--fd-space-4);
    }

    .fd-settings h2 {
      margin: 0;
      font-size: 1.2rem;
      font-weight: 500;
    }

    .fd-suggestion {
      display: flex;
      flex-direction: column;
      gap: var(--fd-space-2);
      margin-top: var(--fd-space-2);
      padding: var(--fd-space-3);
      border: 1px solid var(--fd-accent);
      border-radius: var(--fd-radius);
      background: var(--fd-surface-alt);
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
      border-radius: 999px;
      background: var(--fd-accent);
      color: var(--fd-on-accent);
      box-shadow: 0 6px 20px rgba(0, 0, 0, 0.25);
      font-size: var(--fd-font-small);
    }

    /* Responsive ---------------------------------------------------------- */

    @media (max-width: 900px) {
      .fd-shell {
        gap: var(--fd-space-3);
        padding: var(--fd-space-3);
      }

      .fd-grid {
        grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
      }
    }

    @media (max-width: 640px) {
      .fd-shell {
        padding: var(--fd-space-2);
      }

      .fd-header {
        flex-direction: column;
      }

      .fd-grid {
        grid-template-columns: 1fr;
      }

      .fd-product-media {
        aspect-ratio: 16 / 9;
      }

      .fd-dialog,
      .fd-dialog--wide {
        width: calc(100vw - 16px);
        max-height: calc(100vh - 16px);
      }

      .fd-dialog-body {
        padding: var(--fd-space-3);
      }

      .fd-image-grid {
        grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
      }
    }

    @media (prefers-reduced-motion: reduce) {
      .fd-spinner {
        animation: none;
      }

      * {
        transition: none !important;
      }
    }
  `;
}

if (!customElements.get("frickeldave-store-panel")) {
  customElements.define("frickeldave-store-panel", FrickeldaveStorePanel);
}
