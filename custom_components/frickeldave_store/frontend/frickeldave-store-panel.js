/**
 * Frickeldave Store - Verwaltungspanel für Home Assistant.
 *
 * Einzelne Lit-Web-Component für `panel_custom` mit `embed_iframe=False`.
 * Lit liegt als vendored ESM-Bundle unter `./lit.js` (siehe frontend/README.md),
 * weil Home Assistant keine Import-Map für den nackten Spezifizierer "lit"
 * bereitstellt. Alle Daten kommen über die HA-HTTP-API mit Bearer-Token; die
 * Autorisierung erfolgt serverseitig pro Anfrage.
 *
 * Aufbau:
 *   1. Konstanten und Hilfsfunktionen
 *   2. API-Schicht und Zustandsverwaltung
 *   3. Editor-, Bild-, Bestands-, Archiv- und Tag-Logik
 *   4. Render-Methoden (Produktliste und Dialoge)
 *   5. Styles ausschließlich über HA-Design-Tokens und eigene --fd-* Schicht
 */

import { LitElement, html, css } from "./lit.js";

const API_ROOT = "/api/frickeldave_store";
const MAX_IMAGES = 10;
const PAGE_SIZE = 24;
const SEARCH_DEBOUNCE_MS = 300;
const TOAST_TIMEOUT_MS = 6000;
const REQUEST_TIMEOUT_MS = 30000;
// Der Katalogimport lädt Produkte und Bilder von der Website und darf länger laufen.
const IMPORT_TIMEOUT_MS = 180000;

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

    _menuOpen: { state: true },

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
    _importDialog: { state: true },

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
    this._menuOpen = false;

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
    this._importDialog = null;

    this._toast = null;
    this._imageVersion = 0;
    this._editorSnapshot = "";
    this._editorShakeTimer = undefined;

    this._onVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        this._revalidateAccess();
      }
    };

    // Gebundene Listener für das Kontextmenü (document-Ebene).
    this._onMenuOutside = (event) => this._handleMenuOutside(event);
    this._onMenuKeydown = (event) => this._handleMenuKeydown(event);
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
    this._detachMenuListeners();
    clearTimeout(this._searchTimer);
    clearTimeout(this._toastTimer);
    clearTimeout(this._editorShakeTimer);
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
    const { method = "GET", body, formData, timeoutMs = REQUEST_TIMEOUT_MS } = options;

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
    }, timeoutMs);

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
    this._menuOpen = false;
    this._detachMenuListeners();
    this._editor = null;
    this._imagesDialog = null;
    this._stockDialog = null;
    this._archiveDialog = null;
    this._tagsDialog = null;
    this._importDialog = null;
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
        if (can.product) {
          this._loadTags();
          this._loadProducts();
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
        this._menuOpen = false;
        this._detachMenuListeners();
        this._editor = null;
        this._imagesDialog = null;
        this._stockDialog = null;
        this._archiveDialog = null;
        this._tagsDialog = null;
        this._importDialog = null;
        return;
      }
      if (!can.product) {
        this._products = [];
        this._total = 0;
        this._allTags = [];
        this._menuOpen = false;
        this._detachMenuListeners();
        this._editor = null;
        this._imagesDialog = null;
        this._stockDialog = null;
        this._archiveDialog = null;
        this._tagsDialog = null;
        this._importDialog = null;
      } else {
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
      ["#fd-import-dialog", Boolean(this._importDialog)],
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
      shake: false,
    };
    this._editorSnapshot = this._editorFormState(this._editor);
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
      shake: false,
    };
    this._editorSnapshot = this._editorFormState(this._editor);
  }

  _editorFormState(editor) {
    return JSON.stringify({
      name: editor.name,
      description: editor.description,
      category: editor.category,
      price: editor.price,
      size: editor.size,
      tags: editor.tags,
      customizable: editor.customizable,
      visible: editor.visible,
    });
  }

  _editorHasChanges() {
    if (!this._editor) {
      return false;
    }
    return this._editorSnapshot !== this._editorFormState(this._editor);
  }

  _shakeEditor() {
    if (!this._editor) {
      return;
    }
    clearTimeout(this._editorShakeTimer);
    this._patchEditor({ shake: true });
    this._editorShakeTimer = setTimeout(() => {
      if (this._editor) {
        this._patchEditor({ shake: false });
      }
    }, 600);
  }

  _closeEditor({ force = false } = {}) {
    if (this._editor?.saving) {
      return;
    }
    if (!force && this._editorHasChanges()) {
      this._shakeEditor();
      return;
    }
    clearTimeout(this._editorShakeTimer);
    this._editorSnapshot = "";
    this._editor = null;
    this._restoreFocus();
  }

  _discardEditor() {
    this._closeEditor({ force: true });
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
      this._showToast(editor.mode === "create" ? "Produkt wurde erstellt." : "Produkt wurde gespeichert.");
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
  /* Katalogimport von frickeldave.de                                       */
  /* --------------------------------------------------------------------- */

  _openImport() {
    if (!this._can("product")) {
      return;
    }
    this._captureFocus();
    this._importDialog = {
      phase: "confirm",
      busy: false,
      error: "",
      result: null,
    };
  }

  _closeImport() {
    if (this._importDialog?.busy) {
      return;
    }
    this._importDialog = null;
    this._restoreFocus();
  }

  _focusImport(selector) {
    this.updateComplete
      .then(() => {
        const target = this.renderRoot?.querySelector(selector);
        if (target && typeof target.focus === "function") {
          target.focus();
        }
      })
      .catch(() => {});
  }

  _importErrorText(error) {
    if (error instanceof ApiError) {
      if (error.status === 403) {
        return "Keine Berechtigung für den Katalogimport.";
      }
      if (error.status === 401) {
        return "Die Sitzung ist abgelaufen. Bitte die Seite neu laden.";
      }
      if (error.status === 408 || error.status === 504) {
        return "Der Import hat zu lange gedauert. Bitte erneut versuchen.";
      }
      if (error.status === 0) {
        return "Der Server ist nicht erreichbar. Bitte die Verbindung prüfen und erneut versuchen.";
      }
      return error.message || "Der Katalog konnte nicht importiert werden. Bitte erneut versuchen.";
    }
    return "Der Katalog konnte nicht importiert werden. Bitte erneut versuchen.";
  }

  _normalizeImportResult(data) {
    const count = (value) => {
      const number = Number(value);
      return Number.isFinite(number) ? Math.max(0, Math.trunc(number)) : 0;
    };
    const errors = Array.isArray(data?.errors)
      ? data.errors
          .filter((entry) => entry && typeof entry === "object")
          .map((entry) => ({
            articleNumber: String(entry.articleNumber ?? "").trim(),
            message: String(entry.message ?? "").trim(),
          }))
      : [];
    return {
      created: count(data?.created),
      skipped: count(data?.skipped),
      failed: count(data?.failed),
      imagesImported: count(data?.images_imported),
      productsTotal: count(data?.products_total),
      errors,
    };
  }

  async _startImport() {
    const dialog = this._importDialog;
    if (!dialog || dialog.busy || !this._can("product")) {
      return;
    }

    this._importDialog = { phase: "running", busy: true, error: "", result: null };
    this._focusImport("#fd-import-status");

    try {
      const data = await this._api(`${API_ROOT}/import/catalog`, {
        method: "POST",
        body: { import_images: true },
        timeoutMs: IMPORT_TIMEOUT_MS,
      });
      const result = this._normalizeImportResult(data);

      // Nach dem Import ohne Such- und Filtereinschränkung neu laden,
      // damit die übernommenen Produkte sichtbar sind.
      clearTimeout(this._searchTimer);
      this._resetFilters();
      this._loadTags();

      this._importDialog = { phase: "result", busy: false, error: "", result };
      this._focusImport("#fd-import-result");
    } catch (error) {
      if (!this._importDialog) {
        return;
      }
      this._importDialog = {
        phase: "error",
        busy: false,
        error: this._importErrorText(error),
        result: null,
      };
      this._focusImport("#fd-import-error");
      if (error instanceof ApiError && (error.status === 403 || error.status === 401)) {
        this._revalidateAccess();
      }
    }
  }

  /* --------------------------------------------------------------------- */
  /* Tag-Verwaltung                                                         */
  /* --------------------------------------------------------------------- */

  _openTags() {
    if (!this._can("product")) {
      return;
    }
    const trigger = this._menuTrigger();
    this._closeMenu({ restoreFocus: false });
    if (trigger) {
      this._focusReturn = trigger;
    } else {
      this._captureFocus();
    }
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
          notice: `Tag „${name}“ wurde erstellt.`,
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
      ${this._renderImportDialog()}
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

  /* --------------------------------------------------------------------- */
  /* Kopfzeile und Hauptmenü                                                */
  /* --------------------------------------------------------------------- */

  _menuItems() {
    return [...(this.renderRoot?.querySelectorAll(".fd-menu [role='menuitem']") ?? [])];
  }

  _menuTrigger() {
    const trigger = this.renderRoot?.querySelector("#fd-menu-trigger");
    return trigger && typeof trigger.focus === "function" ? trigger : null;
  }

  _toggleSidebar() {
    this._closeMenu({ restoreFocus: false });
    this.dispatchEvent(new CustomEvent("hass-toggle-menu", { bubbles: true, composed: true }));
  }

  _toggleMenu() {
    if (this._menuOpen) {
      this._closeMenu();
    } else {
      this._openMenu();
    }
  }

  _openMenu() {
    if (this._menuOpen) {
      return;
    }
    this._menuOpen = true;
    document.addEventListener("pointerdown", this._onMenuOutside, true);
    document.addEventListener("keydown", this._onMenuKeydown, true);
    this._focusMenuItem(0);
  }

  _closeMenu({ restoreFocus = true } = {}) {
    if (!this._menuOpen) {
      return;
    }
    this._menuOpen = false;
    this._detachMenuListeners();
    if (restoreFocus) {
      const trigger = this._menuTrigger();
      this.updateComplete
        .then(() => {
          trigger?.focus?.();
        })
        .catch(() => {});
    }
  }

  _detachMenuListeners() {
    document.removeEventListener("pointerdown", this._onMenuOutside, true);
    document.removeEventListener("keydown", this._onMenuKeydown, true);
  }

  _focusMenuItem(index) {
    this.updateComplete
      .then(() => {
        const items = this._menuItems();
        if (items.length === 0) {
          return;
        }
        items[(index + items.length) % items.length].focus();
      })
      .catch(() => {});
  }

  _handleMenuOutside(event) {
    if (!this._menuOpen) {
      return;
    }
    const path = typeof event.composedPath === "function" ? event.composedPath() : [];
    const anchor = this.renderRoot?.querySelector(".fd-menu-anchor");
    if (anchor && path.includes(anchor)) {
      return;
    }
    this._closeMenu({ restoreFocus: false });
  }

  _handleMenuKeydown(event) {
    if (!this._menuOpen) {
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      this._closeMenu();
      return;
    }
    if (event.key === "Tab") {
      this._closeMenu({ restoreFocus: false });
      return;
    }
    const items = this._menuItems();
    if (items.length === 0) {
      return;
    }
    const current = items.indexOf(this.renderRoot?.activeElement);
    if (event.key === "ArrowDown") {
      event.preventDefault();
      this._focusMenuItem(current < 0 ? 0 : current + 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      this._focusMenuItem(current <= 0 ? items.length - 1 : current - 1);
    } else if (event.key === "Home") {
      event.preventDefault();
      this._focusMenuItem(0);
    } else if (event.key === "End") {
      event.preventDefault();
      this._focusMenuItem(items.length - 1);
    }
  }

  _renderHeaderMenu() {
    if (!this._menuOpen) {
      return "";
    }
    if (!this._can("product")) {
      return "";
    }
    return html`
      <div id="fd-main-menu" class="fd-menu" role="menu" aria-label="Weitere Aktionen">
        <button
          type="button"
          class="fd-menu-item"
          role="menuitem"
          tabindex="-1"
          @click=${() => this._openTags()}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <path
              d="M17.63 5.84C17.27 5.33 16.67 5 16 5H5c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h11c.67 0 1.27-.33 1.63-.84L22 12l-4.37-6.16z"
            />
          </svg>
          Tags verwalten
        </button>
      </div>
    `;
  }

  _renderMain() {
    const canProduct = this._can("product");

    return html`
      <div class="fd-app">
        <header class="fd-app-bar">
          <button
            type="button"
            class="fd-bar-icon fd-bar-menu"
            @click=${() => this._toggleSidebar()}
            aria-label="Menü öffnen"
            title="Menü"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
              <path d="M3 18h18v-2H3v2zm0-5h18v-2H3v2zm0-7v2h18V6H3z" />
            </svg>
          </button>
          <h1 id="fd-main-heading" class="fd-bar-title" tabindex="-1">Frickeldave Store</h1>
          ${canProduct
            ? html`
                <div class="fd-bar-actions">
                  <button
                    type="button"
                    class="fd-bar-icon"
                    @click=${() => this._reloadAll()}
                    ?disabled=${this._productsLoading}
                    aria-label="Aktualisieren"
                    title="Aktualisieren"
                  >
                    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                      <path
                        d="M17.65 6.35A7.95 7.95 0 0 0 12 4a8 8 0 1 0 7.73 10h-2.08A6 6 0 1 1 12 6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"
                      />
                    </svg>
                  </button>
                  <button
                    type="button"
                    class="fd-bar-icon"
                    @click=${() => this._openImport()}
                    ?disabled=${Boolean(this._importDialog?.busy)}
                    aria-label="Katalog importieren"
                    title="Katalog importieren"
                  >
                    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                      <path d="M5 20h14v-2H5v2zM19 9h-4V3H9v6H5l7 7 7-7z" />
                    </svg>
                  </button>
                  <button
                    type="button"
                    class="fd-bar-icon"
                    @click=${() => this._openCreate()}
                    aria-label="Produkt erstellen"
                    title="Produkt erstellen"
                  >
                    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                      <path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z" />
                    </svg>
                  </button>
                </div>
                <div class="fd-menu-anchor">
                  <button
                    id="fd-menu-trigger"
                    type="button"
                    class="fd-bar-icon"
                    @click=${() => this._toggleMenu()}
                    aria-haspopup="menu"
                    aria-expanded=${this._menuOpen ? "true" : "false"}
                    aria-controls="fd-main-menu"
                    aria-label="Weitere Aktionen"
                    title="Weitere Aktionen"
                  >
                    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                      <circle cx="12" cy="5" r="1.7" />
                      <circle cx="12" cy="12" r="1.7" />
                      <circle cx="12" cy="19" r="1.7" />
                    </svg>
                  </button>
                  ${this._renderHeaderMenu()}
                </div>
              `
            : ""}
        </header>

        <div class="fd-shell">
          ${canProduct ? this._renderProducts() : this._renderNoProductView()}
        </div>
      </div>
    `;
  }

  _renderNoProductView() {
    return html`
      <section class="fd-card fd-state">
        <h2>Keine Produktverwaltung</h2>
        <p class="fd-muted">
          Ihr Benutzer darf keine Produkte verwalten. Benutzer und KI-Einstellungen verwalten Sie
          in den Einstellungen unter „Geräte &amp; Dienste“ → „Frickeldave Store“.
        </p>
      </section>
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

        <div class="fd-filter-options">
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
          </fieldset>

          <fieldset class="fd-fieldset">
            <legend class="fd-label">Tags</legend>
            ${this._allTags.length === 0
              ? html`<p class="fd-hint">Noch keine Tags vorhanden.</p>`
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
          </fieldset>
        </div>

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
              : "Es sind noch keine Produkte vorhanden."}
          </p>
          ${this._hasActiveFilters()
            ? html`<button type="button" class="fd-button" @click=${() => this._resetFilters()}>
                Filter zurücksetzen
              </button>`
            : html`<button type="button" class="fd-button fd-button--primary" @click=${() => this._openCreate()}>
                Produkt erstellen
              </button>`}
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
                    class="fd-button"
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
        class="fd-dialog${editor.shake ? " fd-dialog--shake" : ""}"
        aria-labelledby="fd-editor-title"
        @cancel=${(event) => this._handleDialogCancel(event, () => this._closeEditor())}
        @click=${(event) => {
          if (event.target === event.currentTarget) {
            this._closeEditor();
          }
        }}
      >
        <div class="fd-dialog-head">
          <button
            type="button"
            class="fd-icon-button fd-dialog-close"
            @click=${() => this._closeEditor()}
            ?disabled=${editor.saving}
            aria-label="Dialog schließen"
            title="Dialog schließen"
          >
            ✕
          </button>
          <div class="fd-dialog-title">
            <h2 id="fd-editor-title">${isCreate ? "Produkt erstellen" : "Produkt bearbeiten"}</h2>
            ${isCreate
              ? html`<p class="fd-muted fd-small">Die Artikelnummer wird automatisch vergeben.</p>`
              : html`<p class="fd-muted fd-small">Artikelnummer ${editor.articleNumber}</p>`}
          </div>
        </div>

        <form class="fd-dialog-form" @submit=${(event) => this._saveEditor(event)} novalidate>
          <div class="fd-dialog-body">
          ${editor.error ? html`<div class="fd-banner fd-banner--error" role="alert">${editor.error}</div>` : ""}
          ${editor.shake
            ? html`<p class="fd-hint" role="status" aria-live="polite">
                Nicht gespeicherte Änderungen. Mit „Abbrechen“ verwerfen.
              </p>`
            : ""}

          <div class="fd-form-grid">
            <div class="fd-field">
              <label class="fd-label" for="fd-editor-name">
                Name <span class="fd-required" aria-hidden="true">*</span>
              </label>
              <input
                id="fd-editor-name"
                data-field="name"
                data-autofocus
                class="fd-input"
                type="text"
                maxlength="200"
                required
                aria-required="true"
                .value=${editor.name}
                aria-invalid=${errors.name ? "true" : "false"}
                aria-describedby=${errors.name ? "fd-editor-name-error" : "fd-editor-name-hint"}
                @input=${(event) => this._setEditorField("name", event.target.value)}
              />
              ${errors.name
                ? html`<p class="fd-error-text" id="fd-editor-name-error">${errors.name}</p>`
                : html`<p class="fd-hint" id="fd-editor-name-hint">Pflichtfeld.</p>`}
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
                  ? "Die Kategorie kann nach dem Erstellen nicht geändert werden."
                  : "Die Kategorie ist unveränderlich. Für einen Wechsel bitte ein neues Produkt erstellen."}
              </p>
              ${errors.category ? html`<p class="fd-error-text">${errors.category}</p>` : ""}
            </div>

            <div class="fd-field">
              <label class="fd-label" for="fd-editor-price">
                Preis (EUR) <span class="fd-required" aria-hidden="true">*</span>
              </label>
              <input
                id="fd-editor-price"
                data-field="price"
                class="fd-input"
                type="text"
                inputmode="decimal"
                required
                aria-required="true"
                placeholder="z. B. 12,50"
                .value=${editor.price}
                aria-invalid=${errors.price ? "true" : "false"}
                aria-describedby=${errors.price ? "fd-editor-price-error" : "fd-editor-price-hint"}
                @input=${(event) => this._setEditorField("price", event.target.value)}
              />
              ${errors.price
                ? html`<p class="fd-error-text" id="fd-editor-price-error">${errors.price}</p>`
                : html`<p class="fd-hint" id="fd-editor-price-hint">Dezimal mit Komma oder Punkt.</p>`}
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
                      Ein KI-Vorschlag ist verfügbar, sobald das Produkt erstellt ist.
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
          </div>

          <footer class="fd-dialog-foot">
            <button
              type="button"
              class="fd-button fd-button--plain"
              @click=${() => this._discardEditor()}
              ?disabled=${editor.saving}
            >
              Abbrechen
            </button>
            <button
              type="submit"
              class="fd-button fd-button--primary"
              ?disabled=${editor.saving}
              aria-busy=${editor.saving ? "true" : "false"}
            >
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
          <button
            type="button"
            class="fd-icon-button fd-dialog-close"
            @click=${() => this._closeImages()}
            ?disabled=${dialog.busy}
            aria-label="Dialog schließen"
            title="Dialog schließen"
          >
            ✕
          </button>
          <div class="fd-dialog-title">
            <h2 id="fd-images-title">Bilder – ${productLabel}</h2>
            <p class="fd-muted fd-small">
              ${count} von ${MAX_IMAGES} Bildern · Artikelnummer ${product.articleNumber}
            </p>
          </div>
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
            ? html`<div class="fd-state fd-state--inline">
                <h3>Noch keine Bilder hinterlegt</h3>
                <p class="fd-hint">Laden Sie bis zu zehn Bilder hoch. Das erste Bild ist das Hauptbild.</p>
              </div>`
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
          <button
            type="button"
            class="fd-button fd-button--plain"
            @click=${() => this._closeImages()}
            ?disabled=${dialog.busy}
          >
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
          <button
            type="button"
            class="fd-icon-button fd-dialog-close"
            @click=${() => this._closeStock()}
            ?disabled=${dialog.saving}
            aria-label="Dialog schließen"
            title="Dialog schließen"
          >
            ✕
          </button>
          <div class="fd-dialog-title">
            <h2 id="fd-stock-title">Bestandsbuchung</h2>
            <p class="fd-muted fd-small">${productLabel} · Artikelnummer ${product.articleNumber}</p>
          </div>
        </div>

        <form class="fd-dialog-form" @submit=${(event) => this._saveStock(event)} novalidate>
          <div class="fd-dialog-body">
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
              <label class="fd-label" for="fd-stock-quantity">
                Menge <span class="fd-required" aria-hidden="true">*</span>
              </label>
              <input
                id="fd-stock-quantity"
                data-autofocus
                class="fd-input"
                type="number"
                min="1"
                step="1"
                inputmode="numeric"
                required
                aria-required="true"
                aria-invalid=${dialog.error ? "true" : "false"}
                aria-describedby="fd-stock-quantity-hint"
                .value=${dialog.quantity}
                @input=${(event) => this._patchStock({ quantity: event.target.value })}
              />
              <p class="fd-hint" id="fd-stock-quantity-hint">Ganze Zahl, mindestens 1.</p>
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
          </div>

          <div class="fd-dialog-foot">
            <button
              type="button"
              class="fd-button fd-button--plain"
              @click=${() => this._closeStock()}
              ?disabled=${dialog.saving}
            >
              Abbrechen
            </button>
            <button
              type="submit"
              class="fd-button fd-button--primary"
              ?disabled=${dialog.saving}
              aria-busy=${dialog.saving ? "true" : "false"}
            >
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
        aria-describedby="fd-archive-description"
        @cancel=${(event) => this._handleDialogCancel(event, () => this._closeArchive())}
        @click=${(event) => {
          if (event.target === event.currentTarget) {
            this._closeArchive();
          }
        }}
      >
        <div class="fd-dialog-head">
          <div class="fd-dialog-title">
            <h2 id="fd-archive-title">Produkt archivieren</h2>
            <p class="fd-muted fd-small">${productLabel} · Artikelnummer ${product.articleNumber}</p>
          </div>
        </div>

        <div class="fd-dialog-body">
          ${dialog.error ? html`<div class="fd-banner fd-banner--error" role="alert">${dialog.error}</div>` : ""}
          <div class="fd-banner fd-banner--warning" role="note" id="fd-archive-description">
            <p>
              Das Produkt verschwindet aus der Website und aus dem Export. Die Daten und Bilder bleiben
              erhalten und können über „Archivierte anzeigen“ weiterhin eingesehen werden.
            </p>
          </div>
          <p class="fd-hint">Ein endgültiges Löschen ist nicht vorgesehen.</p>
        </div>

        <div class="fd-dialog-foot">
          <button
            type="button"
            class="fd-button fd-button--plain"
            data-autofocus
            @click=${() => this._closeArchive()}
            ?disabled=${dialog.saving}
          >
            Abbrechen
          </button>
          <button
            type="button"
            class="fd-button fd-button--primary"
            @click=${() => this._confirmArchive()}
            ?disabled=${dialog.saving}
            aria-busy=${dialog.saving ? "true" : "false"}
          >
            ${dialog.saving ? "Archivieren …" : "Archivieren"}
          </button>
        </div>
      </dialog>
    `;
  }

  /* --------------------------------------------------------------------- */
  /* Render: Katalogimport                                                  */
  /* --------------------------------------------------------------------- */

  _renderImportDialog() {
    const dialog = this._importDialog;
    if (!dialog) {
      return "";
    }
    const running = dialog.busy;
    const result = dialog.result;
    const showIntro = dialog.phase === "confirm" || dialog.phase === "running";

    return html`
      <dialog
        id="fd-import-dialog"
        class="fd-dialog"
        aria-labelledby="fd-import-title"
        @cancel=${(event) => this._handleDialogCancel(event, () => this._closeImport())}
        @click=${(event) => {
          if (event.target === event.currentTarget) {
            this._closeImport();
          }
        }}
      >
        <div class="fd-dialog-head">
          <button
            type="button"
            class="fd-icon-button fd-dialog-close"
            @click=${() => this._closeImport()}
            ?disabled=${running}
            aria-label="Dialog schließen"
            title="Dialog schließen"
          >
            ✕
          </button>
          <div class="fd-dialog-title">
            <h2 id="fd-import-title">Katalog importieren</h2>
            <p class="fd-muted fd-small">Produkte der Live-Website übernehmen</p>
          </div>
        </div>

        <div class="fd-dialog-body">
          ${showIntro
            ? html`
                <div class="fd-import-intro">
                  <p>
                    Der aktuelle Katalog von <strong>frickeldave.de</strong> wird in diese Verwaltung geladen.
                  </p>
                  <ul class="fd-import-notes">
                    <li>Bereits vorhandene Artikelnummern werden übersprungen und niemals überschrieben.</li>
                    <li>
                      Zu jedem Produkt werden bis zu ${MAX_IMAGES} Bilder aus dem Website-Repository
                      übernommen.
                    </li>
                    <li>
                      Der Vorgang kann einen Moment dauern. Die Seite bitte bis zum Abschluss geöffnet
                      lassen.
                    </li>
                  </ul>
                </div>
              `
            : ""}

          ${dialog.error
            ? html`
                <div id="fd-import-error" class="fd-banner fd-banner--error" role="alert" tabindex="-1">
                  ${dialog.error}
                </div>
              `
            : ""}

          ${running
            ? html`
                <div
                  id="fd-import-status"
                  class="fd-state fd-state--inline"
                  role="status"
                  aria-live="polite"
                  tabindex="-1"
                >
                  <span class="fd-spinner" aria-hidden="true"></span>
                  <p>Katalog wird geladen …</p>
                </div>
              `
            : ""}

          ${result ? this._renderImportResult(result) : ""}
        </div>

        <div class="fd-dialog-foot">
            ${dialog.phase === "result"
              ? html`
                  <button type="button" class="fd-button fd-button--primary" @click=${() => this._closeImport()}>
                    Schließen
                  </button>
                `
              : html`
                  <button
                    type="button"
                    class="fd-button fd-button--plain"
                    @click=${() => this._closeImport()}
                    ?disabled=${running}
                    data-autofocus
                  >
                    ${dialog.phase === "error" ? "Schließen" : "Abbrechen"}
                  </button>
                  <button
                    type="button"
                    class="fd-button fd-button--primary"
                    @click=${() => this._startImport()}
                    ?disabled=${running}
                    aria-busy=${running ? "true" : "false"}
                  >
                    ${running ? "Import läuft …" : dialog.phase === "error" ? "Erneut versuchen" : "Import starten"}
                  </button>
                `}
        </div>
      </dialog>
    `;
  }

  _renderImportResult(result) {
    const errorCount = result.errors.length;

    return html`
      <section id="fd-import-result" class="fd-import-result" tabindex="-1" aria-live="polite">
        <h3>Import abgeschlossen</h3>

        <dl class="fd-import-summary">
          <div class="fd-import-stat">
            <dt>Neu</dt>
            <dd>${formatNumber(result.created)}</dd>
          </div>
          <div class="fd-import-stat">
            <dt>Übersprungen</dt>
            <dd>${formatNumber(result.skipped)}</dd>
          </div>
          <div class="fd-import-stat">
            <dt>Fehlgeschlagen</dt>
            <dd class=${errorCount > 0 || result.failed > 0 ? "fd-import-stat--error" : ""}>
              ${formatNumber(result.failed)}
            </dd>
          </div>
          <div class="fd-import-stat">
            <dt>Bilder</dt>
            <dd>${formatNumber(result.imagesImported)}</dd>
          </div>
        </dl>

        <p class="fd-muted fd-small">
          ${result.productsTotal === 1
            ? "1 Produkt im Katalog."
            : `${formatNumber(result.productsTotal)} Produkte im Katalog.`}
        </p>

        ${errorCount > 0
          ? html`
              <div class="fd-import-errors">
                <h4>Fehler pro Produkt</h4>
                <ul role="list">
                  ${result.errors.map(
                    (entry) => html`
                      <li>
                        <strong>${entry.articleNumber || "Ohne Artikelnummer"}</strong>
                        <span>${entry.message || "Unbekannter Fehler."}</span>
                      </li>
                    `,
                  )}
                </ul>
              </div>
            `
          : ""}
      </section>
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
          <button
            type="button"
            class="fd-icon-button fd-dialog-close"
            @click=${() => this._closeTags()}
            ?disabled=${dialog.busy}
            aria-label="Dialog schließen"
            title="Dialog schließen"
          >
            ✕
          </button>
          <div class="fd-dialog-title">
            <h2 id="fd-tags-title">Tags verwalten</h2>
            <p class="fd-muted fd-small">Tags sind öffentliche Filter der Website.</p>
          </div>
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
            <button type="submit" class="fd-button fd-button--primary" ?disabled=${dialog.busy} aria-busy=${dialog.busy ? "true" : "false"}>Erstellen</button>
          </form>

          ${dialog.loading ? html`<p class="fd-muted" role="status">Tags werden geladen …</p>` : ""}
          ${!dialog.loading && dialog.tags.length === 0
            ? html`<div class="fd-state fd-state--inline">
                <h3>Noch keine Tags vorhanden</h3>
                <p class="fd-hint">Erstellen Sie oben den ersten Tag.</p>
              </div>`
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
          <button
            type="button"
            class="fd-button fd-button--plain"
            @click=${() => this._closeTags()}
            ?disabled=${dialog.busy}
          >
            Schließen
          </button>
        </div>
      </dialog>
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
      max-width: 1440px;
      margin: 0 auto;
      padding: var(--fd-space-4);
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

    .fd-bar-actions {
      display: flex;
      flex: none;
      align-items: center;
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

    .fd-bar-icon svg path,
    .fd-bar-icon svg circle {
      fill: currentColor;
      stroke: none;
    }

    .fd-menu-anchor {
      position: relative;
      flex: none;
    }

    .fd-menu {
      position: absolute;
      top: calc(100% + 4px);
      right: 0;
      z-index: 30;
      display: flex;
      flex-direction: column;
      min-width: 200px;
      padding: 6px 0;
      border: 1px solid var(--fd-divider);
      border-radius: var(--fd-radius);
      background: var(--fd-surface);
      box-shadow: var(--fd-shadow-dialog);
    }

    .fd-menu-item {
      display: flex;
      align-items: center;
      min-height: 44px;
      padding: 8px 16px;
      border: none;
      background: none;
      color: var(--fd-text);
      font: inherit;
      text-align: left;
      cursor: pointer;
      transition: background-color var(--fd-duration-fast);
    }

    .fd-menu-item:hover,
    .fd-menu-item:focus-visible {
      background: var(--fd-surface-alt);
    }

    .fd-menu-item svg {
      flex: none;
      width: 20px;
      height: 20px;
      margin-inline-end: var(--fd-space-3);
      fill: currentColor;
    }

    /* Karten ------------------------------------------------------------- */

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

    .fd-state h1,
    .fd-state h2,
    .fd-state h3 {
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
      gap: var(--fd-space-2);
      min-height: 40px;
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
      min-height: 32px;
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
      align-items: center;
      justify-content: center;
      flex: none;
      width: 44px;
      height: 44px;
      padding: 0;
      border: none;
      border-radius: 50%;
      background: transparent;
      color: var(--fd-text-muted);
      font: inherit;
      cursor: pointer;
      transition:
        background-color var(--fd-duration-fast),
        color var(--fd-duration-fast);
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
      border-radius: var(--fd-radius-pill);
      background: var(--fd-surface);
      color: var(--fd-text);
      font: inherit;
      font-size: var(--fd-font-small);
      cursor: pointer;
      transition:
        background-color var(--fd-duration-fast),
        border-color var(--fd-duration-fast),
        color var(--fd-duration-fast);
    }

    .fd-chip:hover {
      background: var(--fd-surface-alt);
    }

    .fd-chip[aria-pressed="true"] {
      border-color: var(--fd-accent);
      background: var(--fd-accent);
      color: var(--fd-on-accent);
    }

    .fd-chip[aria-pressed="true"]::before {
      content: "✓";
      margin-inline-end: 4px;
    }

    .fd-selected-tag {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 4px 4px 4px 12px;
      border: 1px solid var(--fd-divider);
      border-radius: var(--fd-radius-pill);
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

    .fd-banner p {
      margin: 0;
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

    .fd-filter-options {
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      gap: var(--fd-space-3);
      align-items: start;
    }

    @media (min-width: 1200px) {
      .fd-filter-options {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
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
      font-size: var(--ha-font-size-l, 16px);
      font-weight: 500;
      line-height: var(--fd-line-height-heading, 1.2);
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
      border-radius: var(--fd-radius-pill);
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
      border-radius: var(--fd-radius-dialog);
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

    .fd-dialog-form {
      display: flex;
      flex: 1;
      flex-direction: column;
      min-height: 0;
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

    /* Katalogimport -------------------------------------------------------- */

    .fd-import-intro {
      display: flex;
      flex-direction: column;
      gap: var(--fd-space-2);
    }

    .fd-import-intro p {
      margin: 0;
    }

    .fd-import-notes {
      display: flex;
      flex-direction: column;
      gap: 6px;
      margin: 0;
      padding-left: 1.2em;
    }

    .fd-import-result {
      display: flex;
      flex-direction: column;
      gap: var(--fd-space-3);
    }

    .fd-import-result h3 {
      margin: 0;
      font-size: 1rem;
      font-weight: 500;
    }

    .fd-import-summary {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
      gap: var(--fd-space-2);
      margin: 0;
    }

    .fd-import-stat {
      display: flex;
      flex-direction: column;
      gap: 2px;
      padding: var(--fd-space-2) var(--fd-space-3);
      border: 1px solid var(--fd-divider);
      border-radius: var(--fd-radius);
      background: var(--fd-surface-alt);
    }

    .fd-import-stat dt {
      color: var(--fd-text-muted);
      font-size: var(--fd-font-small);
    }

    .fd-import-stat dd {
      margin: 0;
      font-size: 1.15rem;
      font-variant-numeric: tabular-nums;
    }

    .fd-import-stat--error {
      color: var(--fd-error);
    }

    .fd-import-errors {
      display: flex;
      flex-direction: column;
      gap: var(--fd-space-2);
    }

    .fd-import-errors h4 {
      margin: 0;
      font-size: var(--fd-font-small);
      font-weight: 500;
    }

    .fd-import-errors ul {
      display: flex;
      flex-direction: column;
      gap: 6px;
      max-height: 240px;
      margin: 0;
      padding: var(--fd-space-2);
      overflow-y: auto;
      border: 1px solid var(--fd-divider);
      border-radius: var(--fd-radius);
      background: var(--fd-surface-alt);
      list-style: none;
    }

    .fd-import-errors li {
      display: flex;
      flex-wrap: wrap;
      gap: 4px 8px;
      padding-bottom: 6px;
      border-bottom: 1px solid var(--fd-divider);
    }

    .fd-import-errors li:last-child {
      padding-bottom: 0;
      border-bottom: none;
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

    /* Vorschlag (KI) ------------------------------------------------------ */

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
      border-radius: var(--fd-radius-pill);
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

      .fd-bar-title {
        padding-inline-start: var(--ha-space-3, 12px);
        font-size: var(--ha-font-size-l, 1.25rem);
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
      *,
      *::before,
      *::after {
        animation: none !important;
        transition: none !important;
      }
    }
  `;
}

if (!customElements.get("frickeldave-store-panel")) {
  customElements.define("frickeldave-store-panel", FrickeldaveStorePanel);
}
