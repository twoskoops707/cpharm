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

# --- palette (ink / slate surfaces, single aurora accent) ---
BG = "#0b0e14"
BG2 = "#121826"
BG3 = "#1a2233"
BG4 = "#222c3d"
BORDER = "#2f3d52"
BORDER_STRONG = "#3d5166"

ACCENT = "#3ee0d0"
ACCENT_DIM = "#2bb8aa"
ACCENT_GLOW = "#5eeadb"

GREEN = "#4ade80"
RED = "#f87171"
YELLOW = "#fbbf24"
PURPLE = "#a78bfa"

T1 = "#e8edf5"
T2 = "#94a3b8"
T3 = "#64748b"

# Text on filled accent buttons (dark ink)
ON_ACCENT = "#071016"

# spacing scale (px)
SP = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "card": 14}

# Typography — Segoe UI / Variable; keep legacy aliases used by setup_wizard
FONT_HERO = ("Segoe UI", 24, "bold")
FONT_H1 = ("Segoe UI", 20, "bold")
FONT_H2 = ("Segoe UI", 12, "bold")
FONT_LEAD = ("Segoe UI", 11)
FONT_BODY = ("Segoe UI", 10)
FONT_UI = ("Segoe UI", 11)
FONT_CAPTION = ("Segoe UI", 9)
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
    """Configure ttk vertical scrollbars to match surfaces / accent. Idempotent."""
    try:
        style = ttk.Style(root)
    except tk.TclError:
        return
    style.configure(
        CPharm_TSCROLL,
        background=BG3,
        troughcolor=BG4,
        bordercolor=BORDER,
        arrowcolor=T2,
        borderwidth=0,
        relief="flat",
        width=10,
    )
    style.map(
        CPharm_TSCROLL,
        background=[("active", BG4), ("pressed", BORDER_STRONG)],
        arrowcolor=[("active", ACCENT), ("pressed", ACCENT)],
        troughcolor=[("active", BG4)],
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
