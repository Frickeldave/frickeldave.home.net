"""Regressionstests für die Versionskonstanten (Issue #289).

Der Footer des Panels und die Modul-URL für den Browser-Cache hängen an
:data:`custom_components.frickeldave_store.const.VERSION`. Diese Konstante muss
deshalb mit ``manifest.json`` (Quelle der Wahrheit für HACS) und
``pyproject.toml`` übereinstimmen — genau diese Abweichung ließ 0.2.1 im Footer
weiterhin 0.2.0 anzeigen.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from custom_components.frickeldave_store.const import (
    REPOSITORY_URL,
    VERSION,
    module_url,
)

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "frickeldave_store"


def _manifest() -> dict[str, object]:
    return json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))


def _project_version() -> str:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return str(project["version"])


def test_version_matches_manifest() -> None:
    assert _manifest()["version"] == VERSION, (
        "VERSION muss der Manifest-Version entsprechen; das Manifest ist die "
        "Quelle der Wahrheit für HACS"
    )


def test_version_matches_pyproject() -> None:
    assert _project_version() == VERSION


def test_repository_url_names_the_renamed_repository() -> None:
    assert REPOSITORY_URL == "https://github.com/Frickeldave/homeassistant-frickeldave-store"


def test_module_url_cache_bust_carries_the_version() -> None:
    assert module_url("frickeldave-store-panel.js").endswith(f"?v={VERSION}")
