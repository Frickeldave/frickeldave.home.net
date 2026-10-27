"""Generate Home Assistant brand icons for the frickeldave_store integration.

Colours follow the website palette: the brand ink is tailwind `txt.p` (#000) and
`darkmode.txt.p` (#fff); the accent is the site's default cyan/teal action tone.

Usage: .venv/bin/python scripts/generate_brand_icons.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

BASE_SIZE = 256
SUPERSAMPLE = 8

LIGHT_INK = (0, 0, 0)
LIGHT_ACCENT = (14, 116, 144)  # cyan-700, readable on light backgrounds
DARK_INK = (255, 255, 255)
DARK_ACCENT = (34, 211, 238)  # cyan-400, readable on dark backgrounds

# Store bag with a subtle handle; coordinates are in a 256x256 grid.
BAG = (56, 80, 200, 212)
HANDLE = (96, 44, 160, 116)
MONOGRAM = (
    (90, 118),
    (166, 118),
    (166, 132),
    (106, 132),
    (106, 142),
    (148, 142),
    (148, 154),
    (106, 154),
    (106, 174),
    (90, 174),
)


def _scaled(points: tuple[tuple[int, int], ...], factor: int) -> list[tuple[int, int]]:
    return [(x * factor, y * factor) for x, y in points]


def build_icon(size: int, ink: tuple[int, int, int], accent: tuple[int, int, int]) -> Image.Image:
    """Render the store mark at `size` px (RGBA, transparent background)."""
    canvas_size = BASE_SIZE * SUPERSAMPLE
    canvas = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    stroke = 14 * SUPERSAMPLE

    # Handle of the store bag, blending into the bag's top edge.
    draw.arc(
        tuple(value * SUPERSAMPLE for value in HANDLE),
        start=180,
        end=360,
        fill=(*ink, 255),
        width=stroke,
    )

    # Bag body.
    x0, y0, x1, y1 = (value * SUPERSAMPLE for value in BAG)
    draw.rounded_rectangle(
        (x0, y0, x1, y1),
        radius=22 * SUPERSAMPLE,
        outline=(*ink, 255),
        width=stroke,
    )

    # "F" monogram for Frickeldave.
    draw.polygon(_scaled(MONOGRAM, SUPERSAMPLE), fill=(*accent, 255))

    return canvas.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    brand_dir = root / "custom_components" / "frickeldave_store" / "brand"
    brand_dir.mkdir(parents=True, exist_ok=True)

    variants = {
        "icon.png": (256, LIGHT_INK, LIGHT_ACCENT),
        "icon@2x.png": (512, LIGHT_INK, LIGHT_ACCENT),
        "dark_icon.png": (256, DARK_INK, DARK_ACCENT),
        "dark_icon@2x.png": (512, DARK_INK, DARK_ACCENT),
    }

    for filename, (size, ink, accent) in variants.items():
        build_icon(size, ink, accent).save(brand_dir / filename, format="PNG", optimize=True)


if __name__ == "__main__":
    main()
