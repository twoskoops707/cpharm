"""
CPharm wizard visual tokens + Tk helpers (dark theme, readonly logs, ttk scrollbars).

PyInstaller: keep `setup_wizard.py` as entrypoint; this module is imported as a sibling
(`import wizard_theme`) when building from the `wizard/` directory — add
`--hidden-import wizard_theme` if the analyzer misses it.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# --- palette ---
BG = "#0d1117"
BG2 = "#161b22"
BG3 = "#21262d"
BORDER = "#30363d"
ACCENT = "#58a6ff"
GREEN = "#3fb950"
RED = "#f85149"
YELLOW = "#d29922"
PURPLE = "#bc8cff"
T1 = "#e6edf3"
T2 = "#8b949e"
T3 = "#6e7681"

# spacing scale (px) — use for padx/pady; Tk has no border-radius — use padding + borders
SP = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "card": 14}

# font roles (2–3 semantic sizes + mono)
FONT_TITLE = ("Segoe UI", 20, "bold")
FONT_LEAD = ("Segoe UI", 12)
FONT_BODY = ("Segoe UI", 10)
FONT_UI = ("Segoe UI", 11)
FONT_MONO = ("Consolas", 10)

# legacy tuple names used across setup_wizard (title / body / ui / mono)
FH = FONT_TITLE
FS = FONT_BODY
FB = FONT_UI
FG = ("Segoe UI", 13)
FM = FONT_MONO

# Themed ttk scrollbars — style name used after `_style_scrollbars(root)` runs.
CPharm_TSCROLL = "CPharm.Vertical.TScrollbar"


def _style_scrollbars(root: tk.Misc) -> None:
    """Configure ttk vertical scrollbars to match BG2 / BORDER / ACCENT. Idempotent."""
    try:
        style = ttk.Style(root)
    except tk.TclError:
        return
    style.configure(
        CPharm_TSCROLL,
        background=BG2,
        troughcolor=BG3,
        bordercolor=BORDER,
        arrowcolor=T2,
        borderwidth=0,
        relief="flat",
        width=10,
    )
    style.map(
        CPharm_TSCROLL,
        background=[("active", BG3), ("pressed", BORDER)],
        arrowcolor=[("active", ACCENT), ("pressed", ACCENT)],
        troughcolor=[("active", BG3)],
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
