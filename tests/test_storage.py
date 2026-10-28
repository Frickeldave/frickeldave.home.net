"""Tests für den konfigurierbaren Speicherort (config/share/media) und Migration."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.frickeldave_store import (
    FrickeldaveStoreRuntime,
    async_set_data_path,
)
from custom_components.frickeldave_store import storage as storage_module
from custom_components.frickeldave_store.ai import AIService
from custom_components.frickeldave_store.const import (
    BACKUP_DIR_NAME,
    DOMAIN,
    IMAGES_DIR_NAME,
    STORAGE_KEY,
)
from custom_components.frickeldave_store.models import ConflictError, ValidationError
from custom_components.frickeldave_store.permissions import PermissionManager
from custom_components.frickeldave_store.products import ProductService
from custom_components.frickeldave_store.storage import (
    DATA_PATH_VALUES,
    base_dir_for,
    data_path_for_dir,
    ensure_data_dir_sync,
    has_existing_data,
    normalise_data_path,
    resolve_data_dir,
    storage_payload,
)
from custom_components.frickeldave_store.store import CatalogStore
from custom_components.frickeldave_store.views import SettingsView


class FakeConfig:
    def __init__(self, root: Path) -> None:
        self._root = root

    def path(self, *parts: str) -> str:
        return str(self._root.joinpath(*parts))


class FakeConfigEntries:
    def __init__(self) -> None:
        self.reloads: list[str] = []

    def async_update_entry(self, entry: Any, *, options: dict[str, Any]) -> None:
        entry.options = dict(options)

    def async_schedule_reload(self, entry_id: str) -> None:
        self.reloads.append(entry_id)


class FakeHass:
    def __init__(self, root: Path) -> None:
        self.data: dict[str, Any] = {}
        self.config = FakeConfig(root)
        self.config_entries = FakeConfigEntries()

    async def async_add_executor_job(self, func: Any, *args: Any) -> Any:
        return func(*args)


class FakeRequest:
    def __init__(
        self, hass: FakeHass, user_id: str | None, *, body: dict[str, Any] | None = None
    ) -> None:
        self.app = {"hass": hass}
        self._user = SimpleNamespace(id=user_id, name="Test") if user_id else None
        self._body = body

    def get(self, key: str, default: Any = None) -> Any:
        if key == "hass_user":
            return self._user
        return default

    async def json(self) -> dict[str, Any]:
        if self._body is None:
            raise ValueError("kein JSON")
        return self._body


def _patch_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Ersetzt /share und /media durch tmp_path (niemals echte Mounts anfassen)."""

    monkeypatch.setattr(storage_module, "SHARE_ROOT", tmp_path / "share")
    monkeypatch.setattr(storage_module, "MEDIA_ROOT", tmp_path / "media")


async def _runtime(
    hass: FakeHass,
    data_dir: Path,
    *,
    users: dict[str, list[str]] | None = None,
    data_path: str = "config",
) -> FrickeldaveStoreRuntime:
    store = CatalogStore(data_dir=data_dir)
    await store.async_load()
    permissions = PermissionManager()
    permissions.load(users or {"admin": ["app_admin"]})
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={"users": ""},
        options={"data_path": data_path},
    )
    runtime = FrickeldaveStoreRuntime(
        entry=entry,  # type: ignore[arg-type]
        store=store,
        products=ProductService(store),
        permissions=permissions,
        ai=AIService(store),
        export_path=Path("export"),
    )
    hass.data[DOMAIN] = runtime
    return runtime


def _payload(response: Any) -> Any:
    return json.loads(response.body.decode("utf-8"))


def test_resolve_data_dir_for_all_choices(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_roots(monkeypatch, tmp_path)
    hass = FakeHass(tmp_path / "config")

    assert resolve_data_dir(hass, "config") == tmp_path / "config" / "frickeldave_store"
    assert resolve_data_dir(hass, "share") == tmp_path / "share" / "frickeldave_store"
    assert resolve_data_dir(hass, "media") == tmp_path / "media" / "frickeldave_store"
    assert normalise_data_path(None) == "config"
    assert base_dir_for("config") is None
    assert base_dir_for("share") == tmp_path / "share"
    assert base_dir_for("media") == tmp_path / "media"


@pytest.mark.parametrize("value", ["/etc/passwd", "../share", "share/../media", "CONFIG"])
def test_resolve_data_dir_rejects_unknown_values(tmp_path: Path, value: str) -> None:
    hass = FakeHass(tmp_path / "config")
    with pytest.raises(ValidationError):
        resolve_data_dir(hass, value)


def test_ensure_data_dir_rejects_missing_mount(tmp_path: Path) -> None:
    base = tmp_path / "share"
    with pytest.raises(ValidationError, match="/share"):
        ensure_data_dir_sync(base / "frickeldave_store", base)


def test_ensure_data_dir_rejects_non_writable_target(tmp_path: Path) -> None:
    blocked = tmp_path / "blocked"
    blocked.write_text("kein Verzeichnis", encoding="utf-8")
    with pytest.raises(ValidationError):
        ensure_data_dir_sync(blocked)


async def test_store_opens_at_explicit_data_dir(tmp_path: Path) -> None:
    data_dir = tmp_path / "custom"
    store = CatalogStore(data_dir=data_dir)
    await store.async_load()
    await ProductService(store).async_create({"name": "Schild", "category": "LA"})
    await store.async_save()
    await store.images.async_write("LAH3K3", "LAH3K3-01.jpg", b"bild")

    assert (data_dir / STORAGE_KEY).is_file()
    assert store.images.base_dir == data_dir / IMAGES_DIR_NAME
    assert store.backup_dir == data_dir / BACKUP_DIR_NAME
    assert (data_dir / IMAGES_DIR_NAME / "LAH3K3" / "LAH3K3-01.jpg").read_bytes() == b"bild"


async def test_async_set_data_path_migrates_and_keeps_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_roots(monkeypatch, tmp_path)
    (tmp_path / "share").mkdir()
    source = tmp_path / "config" / "frickeldave_store"
    hass = FakeHass(tmp_path / "config")
    runtime = await _runtime(hass, source)
    await ProductService(runtime.store).async_create({"name": "Schild", "category": "LA"})
    await runtime.store.async_save()
    await runtime.store.images.async_write("LAH3K3", "LAH3K3-01.jpg", b"bild")
    runtime.store.backup_dir.mkdir(parents=True, exist_ok=True)
    (runtime.store.backup_dir / "snapshot.json").write_text("{}", encoding="utf-8")

    reload_required = await async_set_data_path(hass, runtime.entry, "share")

    target = tmp_path / "share" / "frickeldave_store"
    assert reload_required is True
    assert (target / STORAGE_KEY).is_file()
    assert (target / IMAGES_DIR_NAME / "LAH3K3" / "LAH3K3-01.jpg").read_bytes() == b"bild"
    assert (target / BACKUP_DIR_NAME / "snapshot.json").read_text(encoding="utf-8") == "{}"
    # Quelle bleibt als Sicherheitskopie erhalten (manuelle Bereinigung).
    assert (source / STORAGE_KEY).is_file()
    assert (source / IMAGES_DIR_NAME / "LAH3K3" / "LAH3K3-01.jpg").is_file()
    assert runtime.entry.options["data_path"] == "share"
    assert runtime.migrated_to == target


async def test_async_set_data_path_rejects_non_empty_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_roots(monkeypatch, tmp_path)
    source = tmp_path / "config" / "frickeldave_store"
    hass = FakeHass(tmp_path / "config")
    runtime = await _runtime(hass, source)
    await runtime.store.async_save()

    target = tmp_path / "share" / "frickeldave_store"
    target.mkdir(parents=True)
    (target / STORAGE_KEY).write_text("{}", encoding="utf-8")
    assert has_existing_data(target) is True

    with pytest.raises(ConflictError):
        await async_set_data_path(hass, runtime.entry, "share")

    assert runtime.entry.options["data_path"] == "config"
    assert runtime.store.data_dir == source


async def test_async_set_data_path_same_location_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_roots(monkeypatch, tmp_path)
    source = tmp_path / "config" / "frickeldave_store"
    hass = FakeHass(tmp_path / "config")
    runtime = await _runtime(hass, source)
    await runtime.store.async_save()

    assert await async_set_data_path(hass, runtime.entry, "config") is False
    assert runtime.entry.options["data_path"] == "config"


async def test_settings_reports_storage_and_backup_exclusion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_roots(monkeypatch, tmp_path)
    data_dir = tmp_path / "config" / "frickeldave_store"
    hass = FakeHass(tmp_path / "config")
    await _runtime(hass, data_dir)

    response = await SettingsView().get(FakeRequest(hass, "admin"))
    assert response.status == 200
    storage = _payload(response)["storage"]
    assert storage["data_path"] == "config"
    assert storage["effective_path"] == str(data_dir)
    assert storage["backup_included"] is True
    assert storage["exclude_from_backup"] is False
    assert storage["paths"] == [
        {"label": "Metadaten", "path": str(data_dir / STORAGE_KEY)},
        {"label": "Bilder", "path": str(data_dir / IMAGES_DIR_NAME)},
        {"label": "Snapshots", "path": str(data_dir / BACKUP_DIR_NAME)},
    ]
    assert [choice["id"] for choice in storage["choices"]] == list(DATA_PATH_VALUES)
    assert [choice["backup_included"] for choice in storage["choices"]] == [True, False, False]
    assert all(choice["label"] and choice["description"] for choice in storage["choices"])


async def test_settings_patch_migrates_and_flags_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_roots(monkeypatch, tmp_path)
    (tmp_path / "share").mkdir()
    source = tmp_path / "config" / "frickeldave_store"
    hass = FakeHass(tmp_path / "config")
    runtime = await _runtime(hass, source)
    await ProductService(runtime.store).async_create({"name": "Schild", "category": "LA"})
    await runtime.store.async_save()

    response = await SettingsView().patch(
        FakeRequest(hass, "admin", body={"storage": {"data_path": "share"}})
    )

    target = tmp_path / "share" / "frickeldave_store"
    assert response.status == 200
    body = _payload(response)
    assert body["reload_required"] is True
    assert body["storage"]["data_path"] == "share"
    assert body["storage"]["effective_path"] == str(target)
    assert body["storage"]["backup_included"] is False
    assert body["storage"]["exclude_from_backup"] is True
    assert body["storage"]["paths"] == [
        {"label": "Metadaten", "path": str(target / STORAGE_KEY)},
        {"label": "Bilder", "path": str(target / IMAGES_DIR_NAME)},
        {"label": "Snapshots", "path": str(target / BACKUP_DIR_NAME)},
    ]
    assert (target / STORAGE_KEY).is_file()
    assert (source / STORAGE_KEY).is_file()
    assert runtime.entry.options["data_path"] == "share"


async def test_settings_patch_rejects_invalid_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_roots(monkeypatch, tmp_path)
    hass = FakeHass(tmp_path / "config")
    await _runtime(hass, tmp_path / "config" / "frickeldave_store")

    response = await SettingsView().patch(
        FakeRequest(hass, "admin", body={"storage": {"data_path": "/etc/frickeldave"}})
    )
    assert response.status == 400
    assert _payload(response)["error"]["code"] == "validation_error"


async def test_settings_patch_rejects_unknown_storage_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_roots(monkeypatch, tmp_path)
    hass = FakeHass(tmp_path / "config")
    await _runtime(hass, tmp_path / "config" / "frickeldave_store")

    response = await SettingsView().patch(
        FakeRequest(hass, "admin", body={"storage": {"data_path": "config", "path": "/custom"}})
    )
    assert response.status == 400
    assert _payload(response)["error"]["code"] == "validation_error"


async def test_settings_patch_returns_conflict_for_non_empty_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_roots(monkeypatch, tmp_path)
    hass = FakeHass(tmp_path / "config")
    runtime = await _runtime(hass, tmp_path / "config" / "frickeldave_store")
    await runtime.store.async_save()

    target = tmp_path / "share" / "frickeldave_store"
    target.mkdir(parents=True)
    (target / STORAGE_KEY).write_text("{}", encoding="utf-8")

    response = await SettingsView().patch(
        FakeRequest(hass, "admin", body={"storage": {"data_path": "share"}})
    )
    assert response.status == 409
    assert _payload(response)["error"]["code"] == "conflict"
    assert runtime.entry.options["data_path"] == "config"


async def test_settings_storage_change_requires_app_admin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_roots(monkeypatch, tmp_path)
    hass = FakeHass(tmp_path / "config")
    await _runtime(
        hass,
        tmp_path / "config" / "frickeldave_store",
        users={"admin": ["app_admin"], "user-1": ["product"]},
    )

    response = await SettingsView().patch(
        FakeRequest(hass, "user-1", body={"storage": {"data_path": "share"}})
    )
    assert response.status == 403
    assert _payload(response)["error"]["code"] == "forbidden"
    assert not (tmp_path / "share").exists()


def test_data_path_for_dir_maps_open_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_roots(monkeypatch, tmp_path)
    hass = FakeHass(tmp_path / "config")
    assert data_path_for_dir(hass, tmp_path / "share" / "frickeldave_store") == "share"
    assert data_path_for_dir(hass, tmp_path / "media" / "frickeldave_store") == "media"
    assert data_path_for_dir(hass, tmp_path / "config" / "frickeldave_store") == "config"


def test_storage_payload_marks_share_as_excluded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_roots(monkeypatch, tmp_path)
    hass = FakeHass(tmp_path / "config")

    payload = storage_payload(hass, "media")

    assert payload["data_path"] == "media"
    assert payload["backup_included"] is False
    assert payload["exclude_from_backup"] is True
    assert payload["paths"][0]["path"].startswith(str(tmp_path / "media" / "frickeldave_store"))


OPTIONS: dict[str, Any] = {
    "users": "user-1: app_admin",
    "ai_endpoint": "https://api.deepseek.com/chat/completions",
    "ai_model": "deepseek-flash",
    "ai_prompt": "Beschreibung",
    "export_path": "frickeldave_store/export",
    "data_path": "config",
}


async def _setup_entry(hass: Any, options: dict[str, Any]) -> MockConfigEntry:
    assert await async_setup_component(hass, "http", {})
    assert await async_setup_component(hass, "frontend", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"users": "user-1: app_admin"},
        options=options,
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_uses_share_data_dir(
    hass: Any, enable_custom_integrations: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    share = tmp_path / "share"
    share.mkdir()
    monkeypatch.setattr(storage_module, "SHARE_ROOT", share)

    entry = await _setup_entry(hass, {**OPTIONS, "data_path": "share"})

    runtime: FrickeldaveStoreRuntime = hass.data[DOMAIN]
    assert runtime.store.data_dir == share / "frickeldave_store"
    assert runtime.store.images.base_dir == share / "frickeldave_store" / IMAGES_DIR_NAME
    assert (share / "frickeldave_store").is_dir()
    assert entry.entry_id


async def test_setup_fails_clearly_without_share_mount(
    hass: Any, enable_custom_integrations: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(storage_module, "SHARE_ROOT", tmp_path / "missing")

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"users": "user-1: app_admin"},
        options={**OPTIONS, "data_path": "share"},
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert DOMAIN not in hass.data


async def test_options_flow_migrates_to_media(
    hass: Any, enable_custom_integrations: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    share = tmp_path / "share"
    share.mkdir()
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setattr(storage_module, "SHARE_ROOT", share)
    monkeypatch.setattr(storage_module, "MEDIA_ROOT", media)
    entry = await _setup_entry(hass, {**OPTIONS, "data_path": "share"})
    runtime: FrickeldaveStoreRuntime = hass.data[DOMAIN]
    await runtime.products.async_create({"name": "Schild", "category": "LA"})
    await runtime.store.async_save()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"data_path": "media"}
    )
    await hass.async_block_till_done()

    assert result["type"] == "create_entry"
    assert entry.options["data_path"] == "media"
    assert runtime.store.data_dir == share / "frickeldave_store"  # alte Kopie bleibt
    assert hass.data[DOMAIN].store.data_dir == media / "frickeldave_store"


async def test_options_flow_rejects_non_empty_target(
    hass: Any, enable_custom_integrations: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    share = tmp_path / "share"
    share.mkdir()
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setattr(storage_module, "SHARE_ROOT", share)
    monkeypatch.setattr(storage_module, "MEDIA_ROOT", media)
    entry = await _setup_entry(hass, {**OPTIONS, "data_path": "share"})
    runtime: FrickeldaveStoreRuntime = hass.data[DOMAIN]
    await runtime.store.async_save()

    target = media / "frickeldave_store"
    target.mkdir()
    (target / STORAGE_KEY).write_text("{}", encoding="utf-8")

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"data_path": "media"}
    )
    await hass.async_block_till_done()

    assert result["type"] == "form"
    assert result["errors"] == {"data_path": "target_not_empty"}
    assert entry.options["data_path"] == "share"
