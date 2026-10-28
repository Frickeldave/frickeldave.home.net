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

const API_ROOT = "/api/frickeldave_store";
const REQUEST_TIMEOUT_MS = 30000;
const TOAST_TIMEOUT_MS = 6000;

const TABS = Object.freeze([
  { id: "users", label: "Benutzerverwaltung", panelId: "fd-config-panel-users" },
  { id: "ai", label: "KI-Einstellungen", panelId: "fd-config-panel-ai" },
]);

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
  };
};

export class FrickeldaveStoreConfigPanel extends LitElement {
  static properties = {
    hass: { attribute: false, noAccessor: true },

    _accessState: { state: true },
    _accessError: { state: true },
    _me: { state: true },

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

    this._toast = null;

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
    for (const controller of this._controllers) {
      controller.abort();
    }
    this._controllers.clear();
    clearTimeout(this._toastTimer);
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

  /* --------------------------------------------------------------------- */
  /* Zugriff und Tabs                                                       */
  /* --------------------------------------------------------------------- */

  _bootstrap() {
    if (!this.hass || !this._token()) {
      return;
    }
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
      };
      if (this._can("app_admin")) {
        this._accessState = "ready";
        this._loadUsers();
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
    if (!TABS.some((entry) => entry.id === tab) || this._tab === tab) {
      return;
    }
    this._tab = tab;
    if (tab === "ai" && this._accessState === "ready" && !this._ai.loaded && !this._ai.loading) {
      this._loadSettings();
    }
  }

  _onTabKeydown(event) {
    const currentIndex = TABS.findIndex((entry) => entry.id === this._tab);
    let nextIndex = currentIndex;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      nextIndex = (currentIndex + 1) % TABS.length;
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      nextIndex = (currentIndex - 1 + TABS.length) % TABS.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = TABS.length - 1;
    } else {
      return;
    }
    event.preventDefault();
    const next = TABS[nextIndex];
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
    const active = this.renderRoot?.activeElement;
    this._focusReturn = active && typeof active.focus === "function" ? active : null;
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
    };
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
    };
  }

  _closeRoleDialog() {
    if (this._roleDialog?.busy) {
      return;
    }
    this._roleDialog = null;
    this._addUserFilter = "";
    this._addUserSelection = "";
    this._restoreFocus();
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
  /* KI-Einstellungen                                                       */
  /* --------------------------------------------------------------------- */

  async _loadSettings() {
    if (!this._can("app_admin") || this._ai.loading) {
      return;
    }
    this._ai = { ...this._ai, loading: true, error: "", notice: "" };
    try {
      const data = await this._api(`${API_ROOT}/settings`);
      const normalized = normalizeSettingsPayload(data);
      this._ai = {
        ...this._ai,
        loading: false,
        loaded: true,
        form: normalized.form,
        apiKeySet: normalized.apiKeySet,
      };
    } catch (error) {
      if (isAbortError(error)) {
        this._ai = { ...this._ai, loading: false };
        return;
      }
      this._ai = { ...this._ai, loading: false, error: this._errorText(error) };
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
      const normalized = normalizeSettingsPayload(data);
      const hasPayload = Boolean(data && typeof data === "object" && (data.ai || "endpoint" in data));
      if (hasPayload) {
        this._ai = {
          ...this._ai,
          saving: false,
          loaded: true,
          error: "",
          notice: "Einstellungen gespeichert.",
          form: normalized.form,
          apiKeySet: normalized.apiKeySet || Boolean(key),
        };
      } else {
        this._ai = {
          ...this._ai,
          saving: false,
          loaded: true,
          error: "",
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
            ${TABS.map((tab) => {
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

          ${this._tab === "users" ? this._renderUsersTab() : this._renderAiTab()}
        </div>
      </div>
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
          <p>Noch keine Benutzer freigegeben.</p>
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
        class="fd-dialog"
        aria-labelledby="fd-role-dialog-title"
        @cancel=${(event) => this._handleDialogCancel(event, () => this._closeRoleDialog())}
        @click=${(event) => {
          if (event.target === event.currentTarget && !dialog.busy) {
            this._closeRoleDialog();
          }
        }}
      >
        <div class="fd-dialog-head">
          <div>
            <h2 id="fd-role-dialog-title">${title}</h2>
            <p class="fd-muted fd-small">
              ${isAdd
                ? "Home-Assistant-Benutzer freigeben und Rollen zuweisen."
                : "Rollen des freigegebenen Benutzers anpassen."}
            </p>
          </div>
          <button
            type="button"
            class="fd-icon-button"
            @click=${() => this._closeRoleDialog()}
            aria-label="Dialog schließen"
            ?disabled=${dialog.busy}
          >
            ✕
          </button>
        </div>

        <div class="fd-dialog-body">
          ${dialog.error
            ? html`<div class="fd-banner fd-banner--error" role="alert">${dialog.error}</div>`
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
            class="fd-button"
            @click=${() => this._closeRoleDialog()}
            ?disabled=${dialog.busy}
          >
            Abbrechen
          </button>
          <button
            type="button"
            class="fd-button fd-button--primary"
            @click=${() => this._saveRoleDialog()}
            ?disabled=${saveDisabled}
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
        <label class="fd-label" for="fd-add-user-select">Home-Assistant-Benutzer</label>
        <select
          id="fd-add-user-select"
          class="fd-input fd-select"
          size="6"
          data-autofocus
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
        @cancel=${(event) => this._handleDialogCancel(event, () => this._closeDeleteDialog())}
        @click=${(event) => {
          if (event.target === event.currentTarget && !dialog.busy) {
            this._closeDeleteDialog();
          }
        }}
      >
        <div class="fd-dialog-head">
          <h2 id="fd-delete-dialog-title">Benutzer entfernen</h2>
          <button
            type="button"
            class="fd-icon-button"
            @click=${() => this._closeDeleteDialog()}
            aria-label="Dialog schließen"
            ?disabled=${dialog.busy}
          >
            ✕
          </button>
        </div>
        <div class="fd-dialog-body">
          ${dialog.error
            ? html`<div class="fd-banner fd-banner--error" role="alert">${dialog.error}</div>`
            : ""}
          <p>
            Soll die Freigabe für <strong>${dialog.name}</strong> wirklich entfernt werden? Der
            Benutzer verliert damit den Zugriff auf den Frickeldave Store.
          </p>
        </div>
        <div class="fd-dialog-foot">
          <button
            type="button"
            class="fd-button"
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
              <button type="submit" class="fd-button fd-button--primary" ?disabled=${ai.saving || ai.loading}>
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
      --fd-space-6: 24px;
      --fd-space-8: 32px;

      --fd-radius-lg: var(--ha-card-border-radius, 12px);
      --fd-radius: var(--ha-card-border-radius, 12px);
      --fd-radius-sm: 8px;

      --fd-surface: var(--ha-card-background, var(--card-background-color, #ffffff));
      --fd-surface-alt: var(--secondary-background-color, rgba(127, 127, 127, 0.08));
      --fd-text: var(--primary-text-color, #212121);
      --fd-text-muted: var(--secondary-text-color, #6b6b6b);
      --fd-accent: var(--primary-color, #03a9f4);
      --fd-on-accent: var(--text-primary-color, #ffffff);
      --fd-error: var(--error-color, #c62828);
      --fd-warning: var(--warning-color, #ff9800);
      --fd-success: var(--success-color, #2e7d32);
      --fd-divider: var(--divider-color, rgba(0, 0, 0, 0.12));
      --fd-shadow: var(--ha-card-box-shadow, none);
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
      background: var(--secondary-background-color, rgba(127, 127, 127, 0.16));
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
      min-height: 44px;
      padding: 8px 16px;
      border: none;
      border-bottom: 3px solid transparent;
      border-radius: 0;
      background: transparent;
      color: var(--fd-text-muted);
      font: inherit;
      font-weight: 500;
      cursor: pointer;
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

    .fd-empty-state {
      padding: var(--fd-space-6);
      border: 1px dashed var(--fd-divider);
      border-radius: var(--fd-radius);
      text-align: center;
    }

    .fd-empty-state p {
      margin: 0 0 4px;
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
      padding: 0;
    }

    .fd-user-row {
      display: grid;
      grid-template-columns: minmax(160px, 1.2fr) minmax(180px, 2fr) auto;
      align-items: center;
      gap: var(--fd-space-3);
      padding: var(--fd-space-3);
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
      border-radius: 999px;
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
      gap: 6px;
      min-height: 44px;
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
    }

    .fd-status--warn {
      border-color: var(--fd-warning);
    }

    /* Dialoge ------------------------------------------------------------- */

    .fd-dialog {
      width: min(820px, calc(100vw - 32px));
      max-height: calc(100vh - 48px);
      padding: 0;
      border: none;
      border-radius: var(--fd-radius-lg);
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
      .fd-spinner {
        animation: none;
      }

      * {
        transition: none !important;
      }
    }
  `;
}

if (!customElements.get("frickeldave-store-config-panel")) {
  customElements.define("frickeldave-store-config-panel", FrickeldaveStoreConfigPanel);
}
