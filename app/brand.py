"""Brand colours and the app icon.

The master artwork is assets/brand/icon.png: a fuchsia tile with a volt speech-bubble
glyph. The tray, window, notification and in-app icons are all made from it; the
tile and glyph are separated so each dictation state can recolour them.
"""

from __future__ import annotations

import functools
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "assets" / "brand" / "icon.png"
STATIC_IMG = Path(__file__).resolve().parent / "static" / "img"

# Electropop palette + two supporting neutrals.
VOLT = (0xCC, 0xFF, 0x00)
EMBER = (0xFF, 0x6B, 0x00)
FUCHSIA = (0xF9, 0x00, 0xFF)
ULTRAVIOLET = (0x52, 0x00, 0xFF)
INK = (0x12, 0x0A, 0x26)

# state -> (tile colour, glyph colour)
STATE_COLORS = {
    "idle": (FUCHSIA, VOLT),
    "recording": (EMBER, INK),
    "transcribing": (ULTRAVIOLET, VOLT),
    "polishing": (VOLT, FUCHSIA),
}


@functools.cache
def _masks():
    """(tile alpha, glyph alpha) at full resolution, from the master icon."""
    from PIL import Image, ImageChops

    im = Image.open(MASTER).convert("RGBA")
    tile = im.getchannel("A")
    # The glyph is the volt part: high green. The fuchsia tile has almost none.
    green = im.getchannel("G").point(lambda g: max(0, min(255, (g - 40) * 255 // 170)))
    glyph = ImageChops.multiply(green, tile)
    return tile, glyph


def make_icon(state: str = "idle", size: int = 64):
    """The app icon recoloured for a dictation state, as a PIL RGBA image."""
    from PIL import Image

    tile_mask, glyph_mask = _masks()
    tile_color, glyph_color = STATE_COLORS.get(state, STATE_COLORS["idle"])
    full = Image.new("RGBA", tile_mask.size, (0, 0, 0, 0))
    full.paste(Image.new("RGBA", full.size, (*tile_color, 255)), (0, 0), tile_mask)
    full.paste(Image.new("RGBA", full.size, (*glyph_color, 255)), (0, 0), glyph_mask)
    return full.resize((size, size), Image.LANCZOS)


def write_static_images() -> None:
    """Regenerate app/static/img (the in-app icon and the glyph mask used by CSS)."""
    from PIL import Image

    STATIC_IMG.mkdir(parents=True, exist_ok=True)
    make_icon("idle", 256).save(STATIC_IMG / "icon.png", optimize=True)
    _, glyph = _masks()
    white = Image.new("RGBA", glyph.size, (255, 255, 255, 0))
    white.putalpha(glyph)
    white.resize((256, 256), Image.LANCZOS).save(STATIC_IMG / "glyph.png", optimize=True)


if __name__ == "__main__":
    write_static_images()
    print(f"wrote {STATIC_IMG}")
