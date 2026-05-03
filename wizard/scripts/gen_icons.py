"""
One-off generator for wizard PNG assets (monochrome / accent-tinted glyphs).

Run from repo root or wizard dir:
  py -3 wizard/scripts/gen_icons.py

Writes PNGs into wizard/assets/ (idempotent).
Requires Pillow.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

# Match wizard_theme.py accent / ink (approximate for standalone script)
ACCENT = (62, 224, 208)
INK = (148, 163, 184)
INK_DIM = (100, 116, 139)
SURFACE = (18, 24, 38)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets"


def _save(name: str, im: Image.Image) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    im.save(p, format="PNG")
    print("wrote", p.relative_to(ROOT.parent))


def _circle(draw: ImageDraw.ImageDraw, xy: tuple[int, int, int, int], fill) -> None:
    draw.ellipse(xy, fill=fill)


def icon_chevron_right(size: int = 12) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    m = size // 4
    d.polygon([(m, m), (size - m, size // 2), (m, size - m)], fill=INK + (255,))
    return im


def icon_diamond(filled: bool, size: int = 24) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    cx, cy = size // 2, size // 2
    r = size // 2 - 3
    pts = [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]
    fill = ACCENT + (255,) if filled else (0, 0, 0, 0)
    outline = ACCENT + (255,) if filled else INK_DIM + (255,)
    d.polygon(pts, fill=fill, outline=outline, width=2 if not filled else 1)
    return im


def icon_phone_outline(size: int = 24) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    m = 4
    d.rounded_rectangle([m, m + 2, size - m, size - m - 2], radius=3, outline=ACCENT + (255,), width=2)
    d.rectangle([size // 2 - 3, m + 5, size // 2 + 3, m + 7], fill=INK + (255,))
    return im


def icon_globe(size: int = 24) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    m = 3
    d.ellipse([m, m, size - m, size - m], outline=ACCENT + (255,), width=2)
    cx = size // 2
    d.line([(cx, m), (cx, size - m)], fill=INK + (255,), width=1)
    d.arc([m, m, size - m, size - m], 200, 340, fill=INK + (255,), width=1)
    d.arc([m, m, size - m, size - m], 20, 160, fill=INK + (255,), width=1)
    return im


def icon_arrows_cycle(size: int = 24) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    m = 4
    d.arc([m, m, size - m - 2, size - m], 30, 210, fill=ACCENT + (255,), width=2)
    d.polygon([(size - m - 2, size // 2), (size - m - 6, size // 2 - 4), (size - m - 6, size // 2 + 4)],
              fill=ACCENT + (255,))
    d.arc([m + 2, m + 2, size - m, size - m], 210, 390, fill=INK + (255,), width=2)
    d.polygon([(m + 2, size // 2 + 2), (m + 6, size // 2 - 2), (m + 6, size // 2 + 6)], fill=INK + (255,))
    return im


def icon_play_badge(size: int = 24) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    m = 3
    d.rounded_rectangle([m, m, size - m, size - m], radius=4, outline=ACCENT + (255,), width=2)
    d.polygon([(size // 2 - 3, size // 2 - 6), (size // 2 + 8, size // 2), (size // 2 - 3, size // 2 + 6)],
              fill=ACCENT + (220,))
    return im


def icon_parallel(size: int = 24) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for i, x in enumerate([6, 15]):
        d.rounded_rectangle([x, 6, x + 6, size - 6], radius=2, outline=ACCENT + (255,) if i == 0 else INK + (255,), width=2)
    return im


def icon_chip(size: int = 24) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    m = 5
    d.rounded_rectangle([m, m + 2, size - m, size - m - 2], radius=2, outline=ACCENT + (255,), width=2)
    for x in range(m + 4, size - m - 4, 4):
        d.line([(x, m), (x, m + 3)], fill=INK + (255,))
        d.line([(x, size - m - 3), (x, size - m)], fill=INK + (255,))
    return im


def icon_java_cup(size: int = 22) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.arc([4, 6, 16, 18], 200, 360, fill=ACCENT + (255,), width=2)
    d.line([(17, 10), (19, 12), (19, 16)], fill=ACCENT + (255,), width=2)
    return im


def icon_python_snake(size: int = 22) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.arc([3, 5, 18, 18], 20, 200, fill=ACCENT + (255,), width=2)
    d.arc([4, 7, 17, 17], 220, 400, fill=INK + (255,), width=2)
    return im


def icon_package(size: int = 22) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    m = 5
    d.polygon([(size // 2, m), (size - m, m + 5), (size - m, size - m), (m, size - m), (m, m + 5)],
              outline=ACCENT + (255,), width=2)
    d.line([(size // 2, m), (size // 2, size - m)], fill=INK + (255,), width=1)
    return im


def icon_tor_onion(size: int = 22) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for i in range(4):
        o = 3 + i * 2
        d.ellipse([o, o, size - o, size - o], outline=INK + (180 - i * 40,), width=1)
    d.ellipse([8, 8, size - 8, size - 8], outline=ACCENT + (255,), width=2)
    return im


def icon_lightning(size: int = 22) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.polygon([(11, 4), (7, 12), (11, 12), (9, 18), (15, 10), (11, 10), (13, 4)], fill=ACCENT + (255,))
    return im


def icon_gamepad(size: int = 22) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([3, 7, size - 3, size - 5], radius=4, outline=ACCENT + (255,), width=2)
    _circle(d, (6, 11, 10, 15), fill=INK + (255,))
    _circle(d, (12, 11, 16, 15), fill=INK + (255,))
    return im


def icon_usb(size: int = 22) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([size // 2 - 3, 4, size // 2 + 3, 8], fill=ACCENT + (255,))
    d.rectangle([size // 2 - 2, 8, size // 2 + 2, 14], fill=INK + (255,))
    d.rectangle([size // 2 - 4, 14, size // 2 + 4, 18], fill=ACCENT + (255,))
    return im


def icon_pencil(size: int = 16) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.line([(3, 13), (11, 5)], fill=ACCENT + (255,), width=2)
    d.polygon([(2, 14), (4, 14), (3, 13)], fill=INK + (255,))
    return im


def icon_clipboard(size: int = 18) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([4, 5, size - 4, size - 3], radius=2, outline=ACCENT + (255,), width=2)
    d.rectangle([7, 3, size - 7, 6], outline=INK + (255,), width=2)
    d.line([(7, 10), (size - 7, 10)], fill=INK_DIM + (255,))
    return im


def icon_trash(size: int = 18) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([5, 6, size - 5, 9], outline=INK + (255,), width=2)
    d.rectangle([4, 9, size - 4, size - 3], outline=ACCENT + (255,), width=2)
    d.line([(7, 12), (7, size - 5)], fill=INK_DIM + (255,))
    d.line([(11, 12), (11, size - 5)], fill=INK_DIM + (255,))
    return im


def icon_download(size: int = 20) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.line([(size // 2, 4), (size // 2, 12)], fill=ACCENT + (255,), width=2)
    d.polygon([(size // 2, 16), (size // 2 - 5, 10), (size // 2 + 5, 10)], fill=ACCENT + (255,))
    d.line([(4, 17), (size - 4, 17)], fill=INK + (255,), width=2)
    return im


def main() -> int:
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        print("Install Pillow: pip install pillow", file=sys.stderr)
        return 1

    _save("chevron_right.png", icon_chevron_right(12))
    _save("milestone_on.png", icon_diamond(True, 24))
    _save("milestone_off.png", icon_diamond(False, 24))
    _save("feat_phones.png", icon_phone_outline(24))
    _save("feat_network.png", icon_globe(24))
    _save("feat_identity.png", icon_arrows_cycle(24))
    _save("feat_play.png", icon_play_badge(24))
    _save("feat_parallel.png", icon_parallel(24))
    _save("feat_arm.png", icon_chip(24))
    _save("prereq_java.png", icon_java_cup(22))
    _save("prereq_python.png", icon_python_snake(22))
    _save("prereq_packages.png", icon_package(22))
    _save("prereq_tor.png", icon_tor_onion(22))
    _save("prereq_cpharm.png", icon_lightning(22))
    _save("row_mumu.png", icon_gamepad(22))
    _save("row_avd.png", icon_phone_outline(22))
    _save("row_usb.png", icon_usb(22))
    _save("icon_edit.png", icon_pencil(16))
    _save("icon_clone.png", icon_clipboard(18))
    _save("icon_trash.png", icon_trash(18))
    _save("icon_download.png", icon_download(20))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
