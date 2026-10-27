"""Regressionstests für die statischen Frontend-Assets des Panels.

Das Panel wird von ``panel_custom`` direkt als ES-Modul importiert. Ein nackter
Spezifizierer wie ``from "lit"`` lässt sich im Browser nicht auflösen, weil Home
Assistant keine Import-Map ausliefert. Lit muss deshalb als vendored Bundle
neben dem Panel liegen.
"""

from __future__ import annotations

import re
from pathlib import Path

FRONTEND_DIR = (
    Path(__file__).resolve().parents[1] / "custom_components" / "frickeldave_store" / "frontend"
)
PANEL_PATH = FRONTEND_DIR / "frickeldave-store-panel.js"
LIT_PATH = FRONTEND_DIR / "lit.js"

_FROM_RE = re.compile(
    r"""^\s*(?:import|export)\b[^;\n]*?\bfrom\s*(?P<quote>["'])(?P<specifier>[^"']+)(?P=quote)""",
    re.MULTILINE,
)
_SIDE_EFFECT_RE = re.compile(
    r"""^\s*import\s*(?P<quote>["'])(?P<specifier>[^"']+)(?P=quote)""",
    re.MULTILINE,
)
_EXPORT_BLOCK_RE = re.compile(r"export\s*\{([^}]*)\}")


def _module_specifiers(source: str) -> list[str]:
    return [match.group("specifier") for match in _FROM_RE.finditer(source)] + [
        match.group("specifier") for match in _SIDE_EFFECT_RE.finditer(source)
    ]


def _exported_names(code: str) -> set[str]:
    names: set[str] = set()
    for block in _EXPORT_BLOCK_RE.findall(code):
        for entry in block.split(","):
            entry = entry.strip()
            if entry:
                names.add(entry.split()[-1])
    return names


def test_panel_uses_only_relative_or_absolute_imports() -> None:
    source = PANEL_PATH.read_text(encoding="utf-8")
    specifiers = _module_specifiers(source)

    assert specifiers, "Das Panel sollte mindestens ./lit.js importieren."
    bare = [item for item in specifiers if not item.startswith(("./", "../", "/"))]
    assert not bare, (
        "Nackte Modulspezifizierer lassen sich im Browser nicht auflösen "
        f"(Home Assistant liefert keine Import-Map): {bare}"
    )
    assert "./lit.js" in specifiers


def test_lit_bundle_exists_and_exports_lit_element() -> None:
    assert LIT_PATH.is_file(), (
        "frontend/lit.js fehlt. Bitte `npm ci && npm run build:frontend` ausführen."
    )
    code = LIT_PATH.read_text(encoding="utf-8")
    assert code.strip(), "frontend/lit.js ist leer."
    assert "@license" in code and "BSD-3-Clause" in code, "Lit-Lizenzbanner fehlt."
    exports = _exported_names(code)
    assert {"LitElement", "html", "css"} <= exports, f"Unerwartete Lit-Exports: {exports}"
