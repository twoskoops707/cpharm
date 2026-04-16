"""
CPharm GUI — Windows desktop app for managing the LDPlayer phone farm.

Layout:
  Left  — Master Phone configurator (phone-frame visual + settings)
  Right — Farm control (cloned phones grid, RAM meter, global controls)

Requires: pip install customtkinter
Run:      python gui/cpharm_gui.py
"""

import customtkinter as ctk
import subprocess, threading, time, socket, os, webbrowser, sys
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────────
LDPLAYER   = r"C:\LDPlayer\LDPlayer9\ldconsole.exe"
APK_DIR    = Path(__file__).parent.parent / "apks"
DASH_PY    = Path(__file__).parent.parent / "automation" / "dashboard.py"
REFRESH_S  = 6
RAM_PER_PH = 1.5   # GB per running phone

# ── Theme ──────────────────────────────────────────────────────────────────────
G   = "#00e676"   # green
G2  = "#00c853"
R   = "#ff5252"   # red
Y   = "#ffd740"   # yellow
BG0 = "#080b0f"
BG1 = "#0d1117"
BG2 = "#161b22"
BG3 = "#1f2733"
BD  = "#2a3140"
T0  = "#e6edf3"
T1  = "#9198a1"
T2  = "#5a6270"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("green")

# ── LDPlayer helpers ────────────────────────────────────────────────────────────
def ld(*args) -> str:
    if not os.path.exists(LDPLAYER):
        return "ERROR: LDPlayer not found"
    try:
        r = subprocess.run([LDPLAYER, *args], capture_output=True, text=True, timeout=20)
        return r.stdout.strip()
    except Exception as e:
        return f"ERROR: {e}"

def list_phones() -> list[dict]:
    raw = ld("list2")
    phones = []
    for line in raw.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2 or not parts[0].isdigit():
            continue
        idx, name = int(parts[0]), parts[1]
        running_raw = ld("isrunning", "--index", str(idx))
        phones.append({
            "index":     idx,
            "name":      name,
            "running":   "running" in running_raw.lower(),
            "is_cpharm": name.lower().startswith("cpharm"),
        })
    return [p for p in phones if p["is_cpharm"]]

def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "?.?.?.?"

def get_free_ram_gb() -> float:
    try:
        out = subprocess.check_output(
            ["wmic", "OS", "get", "FreePhysicalMemory", "/Value"],
            text=True, timeout=5
        )
        kb = int([l for l in out.splitlines() if "=" in l][0].split("=")[1])
        return round(kb / 1024 / 1024, 1)
    except Exception:
        return 0.0

def get_master_config() -> dict:
    """Read current master phone (index 0) settings via ldconsole."""
    raw = ld("list2")
    cfg = {"resolution": "540x960", "cpu": "2", "ram": "1024", "name": "Master"}
    for line in raw.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if parts and parts[0] == "0" and len(parts) > 1:
            cfg["name"] = parts[1]
            if len(parts) > 4:
                cfg["resolution"] = f"{parts[2]}x{parts[3]}" if parts[2].isdigit() else cfg["resolution"]
            break
    return cfg

# ── Dashboard server ─────────────────────────────────────────────────────────────
_dash_proc = None

def start_dashboard():
    global _dash_proc
    if _dash_proc and _dash_proc.poll() is None:
        return
    _dash_proc = subprocess.Popen(
        [sys.executable, str(DASH_PY)],
        creationflags=subprocess.CREATE_NO_WINDOW
    )

def stop_dashboard():
    global _dash_proc
    if _dash_proc and _dash_proc.poll() is None:
        _dash_proc.terminate()
    _dash_proc = None

def dashboard_running() -> bool:
    return _dash_proc is not None and _dash_proc.poll() is None

# ── Reusable widgets ────────────────────────────────────────────────────────────
def label(parent, text, size=12, weight="normal", color=T0, mono=False, **kw):
    font_family = "Courier New" if mono else "Segoe UI"
    return ctk.CTkLabel(parent, text=text,
                        font=ctk.CTkFont(font_family, size, weight),
                        text_color=color, **kw)

def btn(parent, text, command, fg=BG3, hover=BD, tc=T0, width=110, height=32, bold=False, **kw):
    return ctk.CTkButton(parent, text=text, command=command,
                         fg_color=fg, hover_color=hover, text_color=tc,
                         width=width, height=height,
                         font=ctk.CTkFont("Segoe UI", 12, "bold" if bold else "normal"),
                         corner_radius=8, **kw)

def separator(parent, color=BD):
    return ctk.CTkFrame(parent, height=1, fg_color=color, corner_radius=0)

# ══════════════════════════════════════════════════════════════════════════════
# MASTER PHONE PANEL (left side)
# ══════════════════════════════════════════════════════════════════════════════
class MasterPanel(ctk.CTkFrame):
    def __init__(self, master_widget, app, **kw):
        super().__init__(master_widget, fg_color=BG1, corner_radius=0, **kw)
        self._app = app
        self._apk_path = None
        self._build()

    def _build(self):
        # ── Section title ──
        hdr = ctk.CTkFrame(self, fg_color=BG0, corner_radius=0, height=40)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        label(hdr, "  MASTER PHONE", size=11, weight="bold", color=G, mono=True).pack(side="left", pady=10)

        separator(self).pack(fill="x")

        # ── Phone frame visual ──
        phone_wrap = ctk.CTkFrame(self, fg_color="transparent")
        phone_wrap.pack(pady=(16, 8))
        self._phone_frame = PhoneFrame(phone_wrap)
        self._phone_frame.pack()

        separator(self, BD).pack(fill="x", padx=16, pady=(8, 0))

        # ── Settings ──
        settings = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0)
        settings.pack(fill="both", expand=True, padx=0)

        self._build_settings(settings)

        separator(self).pack(fill="x")

        # ── Action buttons ──
        actions = ctk.CTkFrame(self, fg_color=BG0, corner_radius=0, height=54)
        actions.pack(fill="x")
        actions.pack_propagate(False)

        inner = ctk.CTkFrame(actions, fg_color="transparent")
        inner.pack(expand=True, fill="both", padx=12, pady=10)

        btn(inner, "⚙  Apply to Master", self._apply_master,
            fg=BG3, hover=BD, tc=T0, width=160).pack(side="left", padx=(0, 8))
        btn(inner, "▶  Launch Master", self._launch_master,
            fg=G, hover=G2, tc="#000", width=140, bold=True).pack(side="left")

    def _build_settings(self, parent):
        def row(label_text, widget_fn):
            f = ctk.CTkFrame(parent, fg_color="transparent")
            f.pack(fill="x", padx=16, pady=4)
            label(f, label_text, size=10, color=T2, mono=True).pack(anchor="w")
            w = widget_fn(f)
            w.pack(fill="x", pady=(2, 0))
            return w

        # Resolution
        self._res = ctk.CTkOptionMenu(
            None, values=["540x960 (HD)", "720x1280 (HD+)", "1080x1920 (FHD)", "480x854 (Low)"],
            fg_color=BG3, button_color=BD, button_hover_color=BG3,
            text_color=T0, font=ctk.CTkFont("Courier New", 11), corner_radius=6,
            dynamic_resizing=False, width=200
        )
        row("RESOLUTION", lambda p: self._res.configure(master=p) or self._res)

        # CPU cores
        self._cpu = ctk.CTkOptionMenu(
            None, values=["1", "2", "3", "4", "6", "8"],
            fg_color=BG3, button_color=BD, button_hover_color=BG3,
            text_color=T0, font=ctk.CTkFont("Courier New", 11), corner_radius=6,
            dynamic_resizing=False, width=200
        )
        self._cpu.set("2")
        row("CPU CORES", lambda p: self._cpu.configure(master=p) or self._cpu)

        # RAM
        self._ram = ctk.CTkOptionMenu(
            None, values=["512 MB", "1024 MB", "1536 MB", "2048 MB", "3072 MB", "4096 MB"],
            fg_color=BG3, button_color=BD, button_hover_color=BG3,
            text_color=T0, font=ctk.CTkFont("Courier New", 11), corner_radius=6,
            dynamic_resizing=False, width=200
        )
        self._ram.set("1024 MB")
        row("RAM", lambda p: self._ram.configure(master=p) or self._ram)

        # DPI
        self._dpi = ctk.CTkOptionMenu(
            None, values=["160 (ldpi)", "240 (mdpi)", "320 (hdpi)", "480 (xhdpi)"],
            fg_color=BG3, button_color=BD, button_hover_color=BG3,
            text_color=T0, font=ctk.CTkFont("Courier New", 11), corner_radius=6,
            dynamic_resizing=False, width=200
        )
        self._dpi.set("240 (mdpi)")
        row("DPI", lambda p: self._dpi.configure(master=p) or self._dpi)

        # APK picker
        apk_f = ctk.CTkFrame(parent, fg_color="transparent")
        apk_f.pack(fill="x", padx=16, pady=4)
        label(apk_f, "APK FILE", size=10, color=T2, mono=True).pack(anchor="w")
        apk_row = ctk.CTkFrame(apk_f, fg_color="transparent")
        apk_row.pack(fill="x", pady=(2, 0))
        self._apk_lbl = label(apk_row, "no apk selected", size=10, color=T2, mono=True)
        self._apk_lbl.pack(side="left", fill="x", expand=True)
        btn(apk_row, "Browse", self._pick_apk, fg=BG3, hover=BD, tc=T1,
            width=70, height=28).pack(side="right")

        # Auto-detect APK in apks/ folder
        apks = list(APK_DIR.glob("*.apk"))
        if apks:
            self._apk_path = str(apks[0])
            self._apk_lbl.configure(text=apks[0].name, text_color=G)

        # Startup package
        pkg_f = ctk.CTkFrame(parent, fg_color="transparent")
        pkg_f.pack(fill="x", padx=16, pady=4)
        label(pkg_f, "STARTUP PACKAGE (optional)", size=10, color=T2, mono=True).pack(anchor="w")
        self._pkg = ctk.CTkEntry(pkg_f, fg_color=BG3, border_color=BD,
                                  text_color=T0, font=ctk.CTkFont("Courier New", 11),
                                  placeholder_text="com.example.app", height=32)
        self._pkg.pack(fill="x", pady=(2, 0))

        # ADB toggle
        adb_f = ctk.CTkFrame(parent, fg_color="transparent")
        adb_f.pack(fill="x", padx=16, pady=(8, 4))
        label(adb_f, "ADB ENABLED", size=10, color=T2, mono=True).pack(side="left")
        self._adb = ctk.CTkSwitch(adb_f, text="", width=44, height=22,
                                   fg_color=BD, progress_color=G,
                                   button_color=T1, button_hover_color=T0)
        self._adb.select()
        self._adb.pack(side="right")

        # Root toggle
        root_f = ctk.CTkFrame(parent, fg_color="transparent")
        root_f.pack(fill="x", padx=16, pady=(0, 4))
        label(root_f, "ROOT ACCESS", size=10, color=T2, mono=True).pack(side="left")
        self._root = ctk.CTkSwitch(root_f, text="", width=44, height=22,
                                    fg_color=BD, progress_color=Y,
                                    button_color=T1, button_hover_color=T0)
        self._root.pack(side="right")

        # Spacer
        ctk.CTkFrame(parent, fg_color="transparent", height=8).pack()

    def _pick_apk(self):
        import tkinter.filedialog as fd
        path = fd.askopenfilename(
            title="Select APK",
            filetypes=[("APK files", "*.apk"), ("All files", "*.*")],
            initialdir=str(APK_DIR)
        )
        if path:
            self._apk_path = path
            self._apk_lbl.configure(text=Path(path).name, text_color=G)

    def _apply_master(self):
        self._app._run_bg(self._do_apply_master)

    def _do_apply_master(self):
        self._app._set_status("Applying master settings…")
        res_raw = self._res.get().split(" ")[0]
        try:
            w, h = res_raw.split("x")
        except Exception:
            w, h = "540", "960"

        dpi_raw = self._dpi.get().split(" ")[0]
        cpu = self._cpu.get()
        ram = self._ram.get().split(" ")[0]

        ld("modify", "--index", "0",
           "--resolution", f"{w},{h},{dpi_raw}",
           "--cpu", cpu,
           "--memory", ram)

        if self._adb.get():
            ld("adb", "--index", "0", "--command", "shell settings put global adb_enabled 1")

        # Install APK if one is selected
        if self._apk_path and os.path.exists(self._apk_path):
            self._app._set_status("Installing APK on master…")
            ld("installapp", "--index", "0", "--filename", self._apk_path)

        self._app._set_status("Master configured ✓")
        self._phone_frame.set_running(True)

    def _launch_master(self):
        self._app._run_bg(lambda: (
            self._app._set_status("Launching master phone…"),
            ld("launch", "--index", "0"),
            self._phone_frame.set_running(True),
            self._app._set_status("Master phone running")
        ))


# ── Phone frame widget (visual mock phone) ─────────────────────────────────────
class PhoneFrame(ctk.CTkFrame):
    """Draws a stylized phone outline with a status screen inside."""

    W, H = 160, 280

    def __init__(self, parent, **kw):
        super().__init__(parent, fg_color="transparent", **kw)
        self._running = False
        self._canvas = ctk.CTkCanvas(self, width=self.W, height=self.H,
                                      bg=BG1, highlightthickness=0)
        self._canvas.pack()
        self._draw()

    def _draw(self):
        c = self._canvas
        c.delete("all")
        W, H = self.W, self.H
        r = 18  # corner radius

        # Phone body
        c.create_rounded_rect = lambda *a, **kw: None  # tk canvas doesn't have this natively
        # Draw body as polygon approximation
        body_color = BG2
        border_color = G if self._running else BD
        self._draw_phone_body(c, 4, 4, W-4, H-4, r, body_color, border_color)

        # Speaker grille
        gx = W // 2
        c.create_rectangle(gx-20, 14, gx+20, 18, fill=BD, outline="")

        # Screen area
        sx, sy, sw, sh = 14, 30, W-28, H-70
        screen_bg = BG0 if self._running else "#0a0e13"
        c.create_rectangle(sx, sy, sx+sw, sy+sh, fill=screen_bg, outline=BD)

        if self._running:
            # Status bar
            c.create_rectangle(sx, sy, sx+sw, sy+14, fill="#0d1117", outline="")
            c.create_text(sx+sw-6, sy+7, text="●", fill=G, font=("Courier New", 7), anchor="e")
            # App area — green glow
            c.create_rectangle(sx+4, sy+18, sx+sw-4, sy+sh-4,
                                fill="#001a0a", outline="#00e67622")
            c.create_text(sx+sw//2, sy+sh//2-8, text="[ C·PHARM ]",
                          fill=G, font=("Courier New", 9, "bold"))
            c.create_text(sx+sw//2, sy+sh//2+8, text="RUNNING",
                          fill=G2, font=("Courier New", 7))
        else:
            # Lock screen
            c.create_text(sx+sw//2, sy+sh//2-10, text="⏻",
                          fill=T2, font=("Arial", 22))
            c.create_text(sx+sw//2, sy+sh//2+14, text="stopped",
                          fill=T2, font=("Courier New", 8))

        # Home button area
        bx, by = W//2, H-18
        bc = G if self._running else BD
        c.create_oval(bx-10, by-10, bx+10, by+10, outline=bc, width=1.5)

        # Side buttons (power + volume)
        c.create_rectangle(W-5, 60, W-3, 90, fill=BD, outline="")   # power
        c.create_rectangle(2, 55, 4, 75, fill=BD, outline="")        # vol+
        c.create_rectangle(2, 80, 4, 98, fill=BD, outline="")        # vol-

    def _draw_phone_body(self, c, x1, y1, x2, y2, r, fill, outline):
        """Draw a rounded rectangle on a tk Canvas."""
        pts = [
            x1+r, y1,  x2-r, y1,
            x2,   y1,  x2,   y1+r,
            x2,   y2-r, x2,   y2,
            x2-r, y2,  x1+r, y2,
            x1,   y2,  x1,   y2-r,
            x1,   y1+r, x1,   y1,
        ]
        c.create_polygon(pts, fill=fill, outline=outline, width=2, smooth=True)

    def set_running(self, running: bool):
        self._running = running
        self._draw()


# ══════════════════════════════════════════════════════════════════════════════
# FARM PANEL (right side)
# ══════════════════════════════════════════════════════════════════════════════
class FarmPanel(ctk.CTkFrame):
    def __init__(self, master_widget, app, **kw):
        super().__init__(master_widget, fg_color=BG0, corner_radius=0, **kw)
        self._app = app
        self._cards = {}
        self._build()

    def _build(self):
        # ── Header ──
        hdr = ctk.CTkFrame(self, fg_color=BG1, corner_radius=0, height=40)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        label(hdr, "  PHONE FARM", size=11, weight="bold", color=G, mono=True).pack(side="left", pady=10)

        separator(self, BD).pack(fill="x")

        # ── Stats row ──
        stats = ctk.CTkFrame(self, fg_color=BG1, corner_radius=0, height=56)
        stats.pack(fill="x")
        stats.pack_propagate(False)

        sf = ctk.CTkFrame(stats, fg_color="transparent")
        sf.pack(fill="both", expand=True, padx=12, pady=6)

        self._s_total   = self._stat(sf, "PHONES",  "0")
        self._s_running = self._stat(sf, "RUNNING", "0", G)
        self._s_stopped = self._stat(sf, "STOPPED", "0")
        self._s_ram     = self._stat(sf, "RAM",     "0G", Y)
        self._s_max     = self._stat(sf, "MAX EST", "?",  T1)

        separator(self, BD).pack(fill="x")

        # ── RAM meter ──
        meter_f = ctk.CTkFrame(self, fg_color=BG1, corner_radius=0, height=28)
        meter_f.pack(fill="x")
        meter_f.pack_propagate(False)
        mf = ctk.CTkFrame(meter_f, fg_color="transparent")
        mf.pack(fill="both", padx=12, pady=5)
        label(mf, "RAM:", size=9, color=T2, mono=True).pack(side="left")
        self._ram_bar = ctk.CTkProgressBar(mf, height=8, corner_radius=4,
                                            progress_color=G, fg_color=BD)
        self._ram_bar.set(0)
        self._ram_bar.pack(side="left", fill="x", expand=True, padx=6)
        self._ram_lbl = label(mf, "—", size=9, color=T1, mono=True)
        self._ram_lbl.pack(side="right")

        separator(self, BD).pack(fill="x")

        # ── Clone controls ──
        clone_f = ctk.CTkFrame(self, fg_color=BG1, corner_radius=0, height=52)
        clone_f.pack(fill="x")
        clone_f.pack_propagate(False)
        cf = ctk.CTkFrame(clone_f, fg_color="transparent")
        cf.pack(fill="both", padx=12, pady=8)

        label(cf, "Clone from master:", size=11, color=T0).pack(side="left")
        self._clone_n = ctk.CTkEntry(cf, width=44, height=30, fg_color=BG3,
                                      border_color=BD, text_color=T0,
                                      font=ctk.CTkFont("Courier New", 12),
                                      justify="center")
        self._clone_n.insert(0, "1")
        self._clone_n.pack(side="left", padx=8)
        btn(cf, "Clone ＋", self._clone_phones,
            fg=G, hover=G2, tc="#000", width=90, bold=True).pack(side="left")

        separator(self, BD).pack(fill="x")

        # ── Global action buttons ──
        ctrl = ctk.CTkFrame(self, fg_color=BG1, corner_radius=0, height=50)
        ctrl.pack(fill="x")
        ctrl.pack_propagate(False)
        bf = ctk.CTkFrame(ctrl, fg_color="transparent")
        bf.pack(fill="both", padx=12, pady=8)

        btn(bf, "▶ Start All", lambda: self._app._run_bg(self._app._start_all),
            fg=G, hover=G2, tc="#000", width=100, bold=True).pack(side="left", padx=(0,6))
        btn(bf, "■ Stop All", lambda: self._app._run_bg(self._app._stop_all),
            fg=R, hover="#d32f2f", tc="#fff", width=94, bold=True).pack(side="left", padx=(0,6))
        btn(bf, "↺ Restart", lambda: self._app._run_bg(self._app._restart_all),
            fg=BG3, hover=BD, tc=T0, width=84).pack(side="left")

        separator(self, BD).pack(fill="x")

        # ── Phone grid (scrollable) ──
        self._scroll = ctk.CTkScrollableFrame(self, fg_color=BG0, corner_radius=0)
        self._scroll.pack(fill="both", expand=True)
        self._scroll.grid_columnconfigure((0, 1), weight=1)

        # ── Empty state label ──
        self._empty = label(self._scroll,
                             "No phones cloned yet.\nConfigure master → Clone ＋",
                             size=11, color=T2)
        self._empty.grid(row=0, column=0, columnspan=2, pady=40)

    def _stat(self, parent, lbl, val, color=T0):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.pack(side="left", padx=10)
        v = label(f, val, size=18, weight="bold", color=color, mono=True)
        v.pack()
        label(f, lbl, size=8, color=T2).pack()
        return v

    def _clone_phones(self):
        try:
            n = int(self._clone_n.get())
        except ValueError:
            n = 1
        n = max(1, min(n, 20))
        self._app._run_bg(lambda: self._app._clone_n_phones(n))

    def render(self, phones: list[dict]):
        # Remove stale cards
        current_indexes = {p["index"] for p in phones}
        for idx in list(self._cards.keys()):
            if idx not in current_indexes:
                self._cards[idx].destroy()
                del self._cards[idx]

        if phones:
            self._empty.grid_remove()
        else:
            self._empty.grid(row=0, column=0, columnspan=2, pady=40)

        for i, phone in enumerate(phones):
            idx = phone["index"]
            if idx in self._cards:
                self._update_card(self._cards[idx], phone)
            else:
                card = self._make_card(phone)
                card.grid(row=i // 2, column=i % 2,
                           padx=6, pady=5, sticky="ew")
                self._cards[idx] = card

        # Update stats
        running = sum(1 for p in phones if p["running"])
        stopped = len(phones) - running
        ram_used = running * RAM_PER_PH
        free_gb  = get_free_ram_gb()
        max_more = max(0, int(free_gb / RAM_PER_PH))
        total_possible = running + stopped + max_more

        self._s_total.configure(text=str(len(phones)))
        self._s_running.configure(text=str(running))
        self._s_stopped.configure(text=str(stopped))
        self._s_ram.configure(text=f"{ram_used:.0f}G")
        self._s_max.configure(text=str(total_possible))

        total_ram_gb = ram_used + free_gb
        bar_val = ram_used / total_ram_gb if total_ram_gb > 0 else 0
        self._ram_bar.set(min(bar_val, 1.0))
        bar_color = R if bar_val > 0.85 else Y if bar_val > 0.6 else G
        self._ram_bar.configure(progress_color=bar_color)
        self._ram_lbl.configure(text=f"{ram_used:.1f}/{total_ram_gb:.1f} GB")

    def _make_card(self, phone):
        running = phone["running"]
        card = ctk.CTkFrame(self._scroll, fg_color=BG2,
                             border_color=G+"30" if running else BD,
                             border_width=1, corner_radius=10)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(10, 4))

        dot = label(top, "●", size=9, color=G if running else T2)
        dot.pack(side="left")
        card._dot = dot

        label(top, f"  {phone['name']}", size=12, weight="bold",
              color=T0, mono=True).pack(side="left")

        status_lbl = label(top, "RUNNING" if running else "stopped",
                            size=9, color=G if running else T2, mono=True)
        status_lbl.pack(side="right")
        card._status = status_lbl
        card._running = running

        # Pills
        pills = ctk.CTkFrame(card, fg_color="transparent")
        pills.pack(fill="x", padx=12, pady=(0, 6))
        self._pill(pills, f"#{phone['index']}")
        if running:
            self._pill(pills, "~1.5 GB", bg="#ffd74014", fg=Y)
            self._pill(pills, "ADB", bg="#00e67614", fg=G)
        card._pills = pills

        # Button
        action_btn = btn(card,
                          "Stop" if running else "Start",
                          lambda i=phone["index"], r=running:
                              self._app._run_bg(lambda: self._app._toggle_phone(i, r)),
                          fg=R if running else G,
                          hover="#d32f2f" if running else G2,
                          tc="#fff" if running else "#000",
                          width=72, height=26, bold=True)
        action_btn.pack(anchor="e", padx=12, pady=(0, 10))
        card._btn = action_btn

        return card

    def _update_card(self, card, phone):
        running = phone["running"]
        if getattr(card, "_running", None) == running:
            return
        card._running = running
        color = G if running else T2
        card._dot.configure(text_color=color)
        card._status.configure(text="RUNNING" if running else "stopped", text_color=color)
        card.configure(border_color=G+"30" if running else BD)

        # Rebuild pills
        for w in card._pills.winfo_children():
            w.destroy()
        self._pill(card._pills, f"#{phone['index']}")
        if running:
            self._pill(card._pills, "~1.5 GB", bg="#ffd74014", fg=Y)
            self._pill(card._pills, "ADB",     bg="#00e67614", fg=G)

        card._btn.configure(
            text="Stop" if running else "Start",
            fg_color=R if running else G,
            hover_color="#d32f2f" if running else G2,
            text_color="#fff" if running else "#000",
            command=lambda i=phone["index"], r=running:
                self._app._run_bg(lambda: self._app._toggle_phone(i, r))
        )

    def _pill(self, parent, text, bg=BG3, fg=T2):
        ctk.CTkLabel(parent, text=text,
                     font=ctk.CTkFont("Courier New", 8),
                     fg_color=bg, text_color=fg,
                     corner_radius=4, padx=5, pady=1).pack(side="left", padx=(0, 3))


# ══════════════════════════════════════════════════════════════════════════════
# MAIN APPLICATION
# ══════════════════════════════════════════════════════════════════════════════
class CPharmApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("CPharm  —  Phone Farm Manager")
        self.geometry("1060x700")
        self.minsize(860, 580)
        self.configure(fg_color=BG0)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._phones     = []
        self._refreshing = False   # concurrency guard

        self._build_ui()
        self._schedule_refresh()

    # ── UI ─────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        # ── Top bar ──
        bar = ctk.CTkFrame(self, fg_color="#050709", corner_radius=0, height=48)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        label(bar, "  [ C·PHARM ]", size=14, weight="bold", color=G, mono=True).pack(side="left", pady=12)

        self._ip_lbl = label(bar, "", size=10, color=T2, mono=True)
        self._ip_lbl.pack(side="left", padx=12)

        # Right controls
        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.pack(side="right", padx=12)

        self._dash_btn = btn(right, "◉ Web Dashboard", self._toggle_dashboard,
                              fg=BG3, hover=BD, tc=T1, width=140, height=32)
        self._dash_btn.pack(side="right", padx=(6, 0))

        btn(right, "⟳", lambda: self._run_bg(self._do_refresh),
            fg=BG3, hover=BD, tc=T1, width=36, height=32).pack(side="right")

        # ── Activity dot ──
        self._dot = label(bar, "●", size=10, color=T2)
        self._dot.pack(side="right", padx=4)

        separator(self, BD).pack(fill="x")

        # ── Two-panel body ──
        body = ctk.CTkFrame(self, fg_color=BG0, corner_radius=0)
        body.pack(fill="both", expand=True)

        # Left — master phone (fixed width)
        self._master_panel = MasterPanel(body, self)
        self._master_panel.pack(side="left", fill="y", ipadx=0)
        self._master_panel.configure(width=240)
        self._master_panel.pack_propagate(False)

        sep_v = ctk.CTkFrame(body, width=1, fg_color=BD, corner_radius=0)
        sep_v.pack(side="left", fill="y")

        # Right — farm
        self._farm_panel = FarmPanel(body, self)
        self._farm_panel.pack(side="left", fill="both", expand=True)

        # ── Status bar ──
        separator(self, BD).pack(fill="x")
        foot = ctk.CTkFrame(self, fg_color="#050709", corner_radius=0, height=26)
        foot.pack(fill="x")
        foot.pack_propagate(False)
        self._status = label(foot, "Ready", size=10, color=T2, mono=True)
        self._status.pack(side="left", padx=12)

    # ── Actions ────────────────────────────────────────────────────────────────
    def _do_refresh(self):
        if self._refreshing:
            return
        self._refreshing = True
        try:
            self._set_status("Refreshing…")
            self._phones = list_phones()
            ip = get_local_ip()
            self.after(0, lambda: self._ip_lbl.configure(text=f"  {ip}:8080"))
            self.after(0, lambda: self._farm_panel.render(self._phones))
            self._set_status(f"{len(self._phones)} phones · {sum(1 for p in self._phones if p['running'])} running")
        finally:
            self._refreshing = False

    def _start_all(self):
        self._set_status("Starting all phones…")
        for p in self._phones:
            if not p["running"]:
                ld("launch", "--index", str(p["index"]))
        time.sleep(2)
        self._do_refresh()

    def _stop_all(self):
        self._set_status("Stopping all phones…")
        for p in self._phones:
            if p["running"]:
                ld("quit", "--index", str(p["index"]))
        time.sleep(1)
        self._do_refresh()

    def _restart_all(self):
        self._stop_all()
        time.sleep(2)
        self._start_all()

    def _toggle_phone(self, index, currently_running):
        if currently_running:
            self._set_status(f"Stopping CPharm-{index}…")
            ld("quit", "--index", str(index))
        else:
            self._set_status(f"Starting CPharm-{index}…")
            ld("launch", "--index", str(index))
        time.sleep(1)
        self._do_refresh()

    def _clone_n_phones(self, n: int):
        existing = len(self._phones)
        for i in range(1, n + 1):
            name = f"CPharm-{existing + i}"
            self._set_status(f"Cloning {name}…")
            ld("copy", "--name", name, "--from", "0")
            time.sleep(4)
        self._do_refresh()
        self._set_status(f"Cloned {n} phone(s) ✓")

    def _toggle_dashboard(self):
        self._run_bg(self._do_toggle_dashboard)

    def _do_toggle_dashboard(self):
        if dashboard_running():
            stop_dashboard()
            self.after(0, lambda: self._dash_btn.configure(
                text="◉ Web Dashboard", text_color=T1))
            self._set_status("Dashboard stopped.")
        else:
            start_dashboard()
            time.sleep(1)
            ip = get_local_ip()
            self.after(0, lambda: self._dash_btn.configure(
                text="◉ Dashboard  ON", text_color=G))
            self._set_status(f"Dashboard → http://{ip}:8080")
            webbrowser.open("http://localhost:8080")

    # ── Helpers ────────────────────────────────────────────────────────────────
    def _run_bg(self, fn):
        def wrapped():
            self._set_busy(True)
            try:
                fn()
            finally:
                self._set_busy(False)
        threading.Thread(target=wrapped, daemon=True).start()

    def _set_busy(self, on):
        color = G if on else T2
        self.after(0, lambda: self._dot.configure(text_color=color))

    def _set_status(self, msg):
        self.after(0, lambda: self._status.configure(text=msg))

    def _schedule_refresh(self):
        self._run_bg(self._do_refresh)
        self.after(REFRESH_S * 1000, self._schedule_refresh)

    def _on_close(self):
        if dashboard_running():
            stop_dashboard()
        self.destroy()


# ── Entry ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = CPharmApp()
    app.mainloop()
