"""
CPharm wizard visual tokens + Tk helpers (premium dark theme, readonly logs, ttk scrollbars).

PyInstaller one-file: bundle assets next to the frozen module, e.g. from ``wizard/``:
  ``--add-data "assets;assets"``
At runtime icons resolve via ``sys._MEIPASS/assets`` when frozen.
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk

# --- palette v3 (deep slate/zinc + teal–cyan — aligned with automation/dashboard.html) ---
BG = "#030712"
BG2 = "#0a101a"
BG3 = "#0f1724"
BG4 = "#151f2e"
BORDER = "#2a3f55"
BORDER_STRONG = "#3d5670"

ACCENT = "#34e4d0"
ACCENT_DIM = "#22d3ee"
ACCENT_GLOW = "#6af0e0"
ACCENT_MUTED = "#143d38"

GREEN = "#4ade80"
RED = "#f87171"
YELLOW = "#fbbf24"
PURPLE = "#a78bfa"

T1 = "#e8edf5"
T2 = "#94a3b8"
T3 = "#64748b"

# Text on filled accent buttons (dark ink)
ON_ACCENT = "#061016"

# spacing scale (px)
SP = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "card": 14}

# corner radius (px) — canvas cards / parity with dashboard --rad
RADIUS = {"sm": 6, "md": 10, "lg": 14, "xl": 18}

# Typography — Segoe UI family (Tk bundles); weights evoke dashboard Plus Jakarta / DM Sans scale
FONT_HERO = ("Segoe UI", 24, "bold")
FONT_H1 = ("Segoe UI", 20, "bold")
FONT_H2 = ("Segoe UI", 12, "bold")
FONT_DISPLAY = ("Segoe UI", 28, "bold")
FONT_LEAD = ("Segoe UI", 11)
FONT_BODY = ("Segoe UI", 10)
FONT_UI = ("Segoe UI", 11)
FONT_CAPTION = ("Segoe UI", 9)
FONT_EYEBROW = ("Segoe UI", 8, "bold")
FONT_MONO = ("Consolas", 10)

FH = FONT_H1
FS = FONT_BODY
FB = FONT_UI
FG = ("Segoe UI", 13)
FM = FONT_MONO

# Themed ttk scrollbars
CPharm_TSCROLL = "CPharm.Vertical.TScrollbar"

# Icon cache (must retain references for Tk images)
_icon_cache: dict[str, tk.PhotoImage] = {}


def assets_dir() -> Path:
    """Directory containing ``*.png`` icons (development or PyInstaller extract)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        bundled = Path(sys._MEIPASS) / "assets"
        if bundled.is_dir():
            return bundled
    return Path(__file__).resolve().parent / "assets"


def load_icon(name: str, master: tk.Misc) -> tk.PhotoImage | None:
    """
    Load ``assets/{name}.png`` as ``PhotoImage``. Returns None if missing.
    Safe to call repeatedly; images are cached per process.
    """
    base = name if name.endswith(".png") else f"{name}.png"
    if base in _icon_cache:
        return _icon_cache[base]
    path = assets_dir() / base
    if not path.is_file():
        return None
    try:
        img = tk.PhotoImage(master=master, file=str(path))
    except tk.TclError:
        return None
    _icon_cache[base] = img
    return img


def style_primary_button(w: tk.Button) -> None:
    """Accent-filled primary action."""
    w.configure(
        bg=ACCENT,
        fg=ON_ACCENT,
        activebackground=ACCENT_DIM,
        activeforeground=ON_ACCENT,
        relief="flat",
        bd=0,
        highlightthickness=0,
        cursor="hand2",
    )


def style_secondary_button(w: tk.Button) -> None:
    """Muted surface — outline via border highlight."""
    w.configure(
        bg=BG3,
        fg=T1,
        activebackground=BG4,
        activeforeground=T1,
        relief="flat",
        bd=0,
        highlightthickness=1,
        highlightbackground=BORDER,
        cursor="hand2",
    )


def style_danger_button(w: tk.Button) -> None:
    w.configure(
        bg=RED,
        fg=ON_ACCENT,
        activebackground="#ef4444",
        activeforeground=ON_ACCENT,
        relief="flat",
        bd=0,
        highlightthickness=0,
        cursor="hand2",
    )


def draw_round_rect(
    canvas: tk.Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    r: float,
    **kwargs,
) -> None:
    """Rounded rectangle using arcs + rectangles (Tk has no native radius)."""
    tags = kwargs.pop("tags", ())
    fill = kwargs.pop("fill", "")
    outline = kwargs.pop("outline", "")
    width = int(kwargs.pop("width", 1))
    r = min(float(r), (x2 - x1) / 2, (y2 - y1) / 2)
    if r <= 0:
        canvas.create_rectangle(x1, y1, x2, y2, fill=fill, outline=outline, width=width, tags=tags)
        return
    d = 2 * r
    if fill:
        canvas.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline="", tags=tags)
        canvas.create_rectangle(x1, y1 + r, x2, y2 - r, fill=fill, outline="", tags=tags)
        canvas.create_arc(x1, y1, x1 + d, y1 + d, start=90, extent=90, fill=fill, outline="", tags=tags)
        canvas.create_arc(x2 - d, y1, x2, y1 + d, start=0, extent=90, fill=fill, outline="", tags=tags)
        canvas.create_arc(x2 - d, y2 - d, x2, y2, start=270, extent=90, fill=fill, outline="", tags=tags)
        canvas.create_arc(x1, y2 - d, x1 + d, y2, start=180, extent=90, fill=fill, outline="", tags=tags)
    if outline:
        canvas.create_line(x1 + r, y1, x2 - r, y1, fill=outline, width=width, tags=tags)
        canvas.create_line(x1 + r, y2, x2 - r, y2, fill=outline, width=width, tags=tags)
        canvas.create_line(x1, y1 + r, x1, y2 - r, fill=outline, width=width, tags=tags)
        canvas.create_line(x2, y1 + r, x2, y2 - r, fill=outline, width=width, tags=tags)
        canvas.create_arc(
            x1, y1, x1 + d, y1 + d, start=90, extent=90,
            style="arc", outline=outline, width=width, tags=tags,
        )
        canvas.create_arc(
            x2 - d, y1, x2, y1 + d, start=0, extent=90,
            style="arc", outline=outline, width=width, tags=tags,
        )
        canvas.create_arc(
            x2 - d, y2 - d, x2, y2, start=270, extent=90,
            style="arc", outline=outline, width=width, tags=tags,
        )
        canvas.create_arc(
            x1, y2 - d, x1 + d, y2, start=180, extent=90,
            style="arc", outline=outline, width=width, tags=tags,
        )


def _style_scrollbars(root: tk.Misc) -> None:
    """Themed ttk scrollbars: flat rail, no arrow chrome, thumb hover/press (Windows vista+)."""
    try:
        style = ttk.Style(root)
    except tk.TclError:
        return

    # Rail + thumb only — reads like a modern overlay scrollbar (no up/down triangles).
    # Element names match Vertical.TScrollbar on Windows ``vista`` / classic Tk.
    try:
        style.layout(
            CPharm_TSCROLL,
            [
                (
                    "Vertical.Scrollbar.trough",
                    {
                        "sticky": "ns",
                        "children": [
                            (
                                "Vertical.Scrollbar.thumb",
                                {"expand": "1", "sticky": "nswe"},
                            )
                        ],
                    },
                )
            ],
        )
    except tk.TclError:
        pass

    # Dedicated scrollbar ramp so lists/logs feel intentional, not default gray.
    sc_trough = "#06080d"
    sc_thumb = "#3f526b"
    sc_thumb_hi = "#546d8f"
    sc_thumb_press = ACCENT_DIM

    style.configure(
        CPharm_TSCROLL,
        background=sc_thumb,
        troughcolor=sc_trough,
        bordercolor=sc_trough,
        lightcolor=sc_thumb,
        darkcolor=sc_thumb,
        arrowcolor=T3,
        arrowsize=0,
        borderwidth=0,
        relief="flat",
        width=11,
    )
    style.map(
        CPharm_TSCROLL,
        background=[
            ("pressed !disabled", sc_thumb_press),
            ("active !disabled", sc_thumb_hi),
            ("disabled", BG4),
        ],
        troughcolor=[("readonly", sc_trough)],
    )


def _readonly_log_key(event: tk.Event) -> str | None:
    """Block typing/edits; allow Ctrl+A/C, navigation, and selection."""
    ks = event.keysym
    if ks in (
        "Shift_L", "Shift_R", "Control_L", "Control_R",
        "Alt_L", "Alt_R", "Meta_L", "Meta_R", "Caps_Lock", "Num_Lock",
    ):
        return
    if event.state & 0x0004:
        kl = ks.lower()
        if kl in ("a", "c"):
            return
        return "break"
    if ks in (
        "Left", "Right", "Up", "Down", "Home", "End",
        "Prior", "Next", "Tab",
    ):
        return
    if ks.startswith("KP_") and ks in (
        "KP_Left", "KP_Right", "KP_Up", "KP_Down",
        "KP_Prior", "KP_Next", "KP_Home", "KP_End",
    ):
        return
    return "break"


def _attach_readonly_log_text(w: tk.Text) -> None:
    """Read-only log/status Text: selectable and Ctrl+A/C work; no typing or paste."""
    w.configure(state="normal", cursor="arrow")
    w.bind("<Key>", _readonly_log_key)
    w.bind("<<Paste>>", lambda e: "break")
    w.bind("<Control-v>", lambda e: "break")
    w.bind("<Control-V>", lambda e: "break")
    w.bind("<Control-x>", lambda e: "break")
    w.bind("<Control-X>", lambda e: "break")
