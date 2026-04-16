"""
CPharm GUI — Windows desktop app for managing the LDPlayer phone farm.

Layout:
  Left  — Master Phone configurator (phone-frame visual + full settings)
  Right — Farm control (cloned phones grid, RAM meter, GPU stats, log)

Requires: pip install customtkinter
Run:      python gui/cpharm_gui.py
"""

import customtkinter as ctk
import subprocess, threading, time, socket, os, webbrowser, sys, json, datetime
from pathlib import Path
import tkinter.filedialog as fd

# ── Config ─────────────────────────────────────────────────────────────────────
LDPLAYER    = r"C:\LDPlayer\LDPlayer9\ldconsole.exe"
FIRST_RUN_F = Path(__file__).parent.parent / ".cpharm_setup_done"
APK_DIR    = Path(__file__).parent.parent / "apks"
DASH_PY    = Path(__file__).parent.parent / "automation" / "dashboard.py"
REFRESH_S  = 8
RAM_PER_PH = 1.5   # GB per running phone (default estimate)

# ── Theme ──────────────────────────────────────────────────────────────────────
G   = "#00e676"
G2  = "#00c853"
R   = "#ff5252"
Y   = "#ffd740"
B   = "#448aff"
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

# ── Global log buffer ──────────────────────────────────────────────────────────
_log_lines: list[str] = []
_log_cb = None  # set by app after UI build

def log(msg: str):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}]  {msg}"
    _log_lines.append(line)
    if len(_log_lines) > 200:
        _log_lines.pop(0)
    if _log_cb:
        _log_cb(line)

# ── LDPlayer helpers ────────────────────────────────────────────────────────────
def ld(*args) -> str:
    if not os.path.exists(LDPLAYER):
        return "ERROR: LDPlayer not found"
    try:
        r = subprocess.run([LDPLAYER, *args], capture_output=True, text=True, timeout=25)
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

def get_total_ram_gb() -> float:
    try:
        out = subprocess.check_output(
            ["wmic", "ComputerSystem", "get", "TotalPhysicalMemory", "/Value"],
            text=True, timeout=5
        )
        b = int([l for l in out.splitlines() if "=" in l][0].split("=")[1])
        return round(b / 1024 / 1024 / 1024, 1)
    except Exception:
        return 0.0

def get_gpu_info() -> dict:
    try:
        out = subprocess.check_output(
            ["wmic", "path", "win32_VideoController", "get",
             "Name,AdapterRAM,CurrentRefreshRate", "/Format:csv"],
            text=True, timeout=5
        )
        lines = [l.strip() for l in out.splitlines() if l.strip() and "Node" not in l]
        if lines:
            parts = lines[0].split(",")
            name = parts[2] if len(parts) > 2 else "Unknown GPU"
            ram_bytes = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            ram_gb = round(ram_bytes / 1024**3, 1) if ram_bytes > 0 else 0
            return {"name": name, "vram_gb": ram_gb}
    except Exception:
        pass
    return {"name": "GPU N/A", "vram_gb": 0}

def take_screenshot(index: int) -> str | None:
    """Take screenshot of phone, save to screenshots folder, return path."""
    out_dir = Path(__file__).parent.parent / "screenshots"
    out_dir.mkdir(exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = str(out_dir / f"phone_{index}_{ts}.png")
    ld("screencap", "--index", str(index), "--fileName", out_path)
    if Path(out_path).exists():
        return out_path
    return None

def open_adb_shell(index: int):
    """Open ADB shell in a new PowerShell window."""
    port = 5554 + index * 2
    cmd = f'adb -s emulator-{port} shell'
    subprocess.Popen(
        ["powershell", "-NoExit", "-Command", cmd],
        creationflags=subprocess.CREATE_NEW_CONSOLE
    )

def delete_phone(index: int) -> str:
    return ld("remove", "--index", str(index))

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

def option_menu(parent, values, default=None, width=200, **kw):
    om = ctk.CTkOptionMenu(
        parent, values=values,
        fg_color=BG3, button_color=BD, button_hover_color=BG3,
        text_color=T0, font=ctk.CTkFont("Courier New", 11),
        corner_radius=6, dynamic_resizing=False, width=width, **kw
    )
    if default:
        om.set(default)
    return om

def setting_row(parent, label_text):
    f = ctk.CTkFrame(parent, fg_color="transparent")
    f.pack(fill="x", padx=16, pady=4)
    label(f, label_text, size=9, color=T2, mono=True).pack(anchor="w")
    return f

# ══════════════════════════════════════════════════════════════════════════════
# PHONE FRAME VISUAL
# ══════════════════════════════════════════════════════════════════════════════
class PhoneFrame(ctk.CTkFrame):
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
        r = 18

        border_color = G if self._running else BD
        self._draw_rounded_rect(c, 4, 4, W-4, H-4, r, BG2, border_color)

        # Speaker grille
        gx = W // 2
        c.create_rectangle(gx-20, 14, gx+20, 18, fill=BD, outline="")

        # Screen
        sx, sy, sw, sh = 14, 30, W-28, H-70
        screen_bg = BG0 if self._running else "#0a0e13"
        c.create_rectangle(sx, sy, sx+sw, sy+sh, fill=screen_bg, outline=BD)

        if self._running:
            c.create_rectangle(sx, sy, sx+sw, sy+14, fill="#0d1117", outline="")
            c.create_text(sx+sw-6, sy+7, text="●", fill=G, font=("Courier New", 7), anchor="e")
            c.create_rectangle(sx+4, sy+18, sx+sw-4, sy+sh-4,
                                fill="#001a0a", outline="#00e67622")
            c.create_text(sx+sw//2, sy+sh//2-8, text="[ C·PHARM ]",
                          fill=G, font=("Courier New", 9, "bold"))
            c.create_text(sx+sw//2, sy+sh//2+8, text="RUNNING",
                          fill=G2, font=("Courier New", 7))
        else:
            c.create_text(sx+sw//2, sy+sh//2-10, text="⏻",
                          fill=T2, font=("Arial", 22))
            c.create_text(sx+sw//2, sy+sh//2+14, text="stopped",
                          fill=T2, font=("Courier New", 8))

        # Home button
        bx, by = W//2, H-18
        bc = G if self._running else BD
        c.create_oval(bx-10, by-10, bx+10, by+10, outline=bc, width=1.5)

        # Side buttons
        c.create_rectangle(W-5, 60, W-3, 90, fill=BD, outline="")
        c.create_rectangle(2, 55, 4, 75, fill=BD, outline="")
        c.create_rectangle(2, 80, 4, 98, fill=BD, outline="")

    def _draw_rounded_rect(self, c, x1, y1, x2, y2, r, fill, outline):
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
# MASTER PHONE PANEL (left side)
# ══════════════════════════════════════════════════════════════════════════════
class MasterPanel(ctk.CTkFrame):
    def __init__(self, master_widget, app, **kw):
        super().__init__(master_widget, fg_color=BG1, corner_radius=0, **kw)
        self._app = app
        self._apk_path = None
        self._build()

    def _build(self):
        hdr = ctk.CTkFrame(self, fg_color=BG0, corner_radius=0, height=40)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        label(hdr, "  MASTER PHONE", size=11, weight="bold", color=G, mono=True).pack(side="left", pady=10)

        separator(self).pack(fill="x")

        # Phone visual
        phone_wrap = ctk.CTkFrame(self, fg_color="transparent")
        phone_wrap.pack(pady=(16, 8))
        self._phone_frame = PhoneFrame(phone_wrap)
        self._phone_frame.pack()

        separator(self, BD).pack(fill="x", padx=16, pady=(8, 0))

        # Scrollable settings
        settings = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0)
        settings.pack(fill="both", expand=True, padx=0)
        self._build_settings(settings)

        separator(self).pack(fill="x")

        # Action buttons
        actions = ctk.CTkFrame(self, fg_color=BG0, corner_radius=0, height=54)
        actions.pack(fill="x")
        actions.pack_propagate(False)
        inner = ctk.CTkFrame(actions, fg_color="transparent")
        inner.pack(expand=True, fill="both", padx=12, pady=10)
        btn(inner, "⚙  Apply", self._apply_master,
            fg=BG3, hover=BD, tc=T0, width=90).pack(side="left", padx=(0, 6))
        btn(inner, "▶  Launch Master", self._launch_master,
            fg=G, hover=G2, tc="#000", width=148, bold=True).pack(side="left")

    def _build_settings(self, p):
        # ── DISPLAY ──
        self._section(p, "DISPLAY")

        r = setting_row(p, "RESOLUTION")
        self._res = option_menu(r, ["480x854 (Low)", "540x960 (HD)", "720x1280 (HD+)", "1080x1920 (FHD)"],
                                 default="540x960 (HD)")
        self._res.pack(fill="x")

        r = setting_row(p, "DPI")
        self._dpi = option_menu(r, ["160 (ldpi)", "240 (mdpi)", "320 (hdpi)", "480 (xhdpi)"],
                                 default="240 (mdpi)")
        self._dpi.pack(fill="x")

        # ── HARDWARE ──
        self._section(p, "HARDWARE")

        r = setting_row(p, "CPU CORES")
        self._cpu = option_menu(r, ["1", "2", "3", "4", "6", "8"], default="2")
        self._cpu.pack(fill="x")

        r = setting_row(p, "RAM")
        self._ram = option_menu(r, ["512 MB", "1024 MB", "1536 MB", "2048 MB", "3072 MB", "4096 MB"],
                                 default="1024 MB")
        self._ram.pack(fill="x")

        r = setting_row(p, "GPU RENDERING")
        self._gpu_mode = option_menu(r, ["DirectX (fastest)", "OpenGL (compat)", "Vulkan (advanced)"],
                                      default="DirectX (fastest)")
        self._gpu_mode.pack(fill="x")

        # ── ANDROID ──
        self._section(p, "ANDROID")

        r = setting_row(p, "ANDROID VERSION")
        self._android = option_menu(r, ["Android 5.1", "Android 7.1", "Android 9", "Android 11", "Android 12"],
                                     default="Android 9")
        self._android.pack(fill="x")

        r = setting_row(p, "DEVICE MODEL")
        self._model = ctk.CTkEntry(r, fg_color=BG3, border_color=BD,
                                    text_color=T0, font=ctk.CTkFont("Courier New", 11),
                                    placeholder_text="Samsung SM-G973F", height=32)
        self._model.pack(fill="x")

        r = setting_row(p, "LOCALE")
        self._locale = option_menu(r, ["en-US", "en-GB", "zh-CN", "ja-JP", "ko-KR",
                                        "de-DE", "fr-FR", "es-ES", "pt-BR", "ru-RU"],
                                    default="en-US")
        self._locale.pack(fill="x")

        # ── NETWORK ──
        self._section(p, "NETWORK")

        r = setting_row(p, "NETWORK TYPE")
        self._net = option_menu(r, ["NAT (default)", "Bridge (LAN)"], default="NAT (default)")
        self._net.pack(fill="x")

        r = setting_row(p, "PROXY (host:port)")
        self._proxy = ctk.CTkEntry(r, fg_color=BG3, border_color=BD,
                                    text_color=T0, font=ctk.CTkFont("Courier New", 11),
                                    placeholder_text="leave blank for none", height=32)
        self._proxy.pack(fill="x")

        # ── APP ──
        self._section(p, "APP / APK")

        apk_f = ctk.CTkFrame(p, fg_color="transparent")
        apk_f.pack(fill="x", padx=16, pady=4)
        label(apk_f, "APK FILE", size=9, color=T2, mono=True).pack(anchor="w")
        apk_row = ctk.CTkFrame(apk_f, fg_color="transparent")
        apk_row.pack(fill="x", pady=(2, 0))
        self._apk_lbl = label(apk_row, "no apk selected", size=10, color=T2, mono=True)
        self._apk_lbl.pack(side="left", fill="x", expand=True)
        btn(apk_row, "Browse", self._pick_apk, fg=BG3, hover=BD, tc=T1,
            width=70, height=28).pack(side="right")

        apks = list(APK_DIR.glob("*.apk"))
        if apks:
            self._apk_path = str(apks[0])
            self._apk_lbl.configure(text=apks[0].name, text_color=G)

        r = setting_row(p, "STARTUP PACKAGE (optional)")
        self._pkg = ctk.CTkEntry(r, fg_color=BG3, border_color=BD,
                                  text_color=T0, font=ctk.CTkFont("Courier New", 11),
                                  placeholder_text="com.example.app", height=32)
        self._pkg.pack(fill="x")

        r = setting_row(p, "CLONE NAME PREFIX")
        self._prefix = ctk.CTkEntry(r, fg_color=BG3, border_color=BD,
                                     text_color=T0, font=ctk.CTkFont("Courier New", 11),
                                     placeholder_text="CPharm", height=32)
        self._prefix.insert(0, "CPharm")
        self._prefix.pack(fill="x")

        # ── TOGGLES ──
        self._section(p, "OPTIONS")
        self._adb   = self._toggle_row(p, "ADB ENABLED",       default_on=True,  color=G)
        self._root  = self._toggle_row(p, "ROOT ACCESS",       default_on=False, color=Y)
        self._rand  = self._toggle_row(p, "RANDOMIZE ANDROID ID", default_on=True, color=B)
        self._hgpu  = self._toggle_row(p, "HIGH-PERF GPU",     default_on=False, color=Y)

        ctk.CTkFrame(p, fg_color="transparent", height=12).pack()

    def _section(self, parent, title):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.pack(fill="x", padx=16, pady=(12, 2))
        label(f, f"── {title} ──", size=8, color=T2, mono=True).pack(anchor="w")

    def _toggle_row(self, parent, text, default_on=False, color=G):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.pack(fill="x", padx=16, pady=2)
        label(f, text, size=9, color=T2, mono=True).pack(side="left")
        sw = ctk.CTkSwitch(f, text="", width=44, height=22,
                            fg_color=BD, progress_color=color,
                            button_color=T1, button_hover_color=T0)
        if default_on:
            sw.select()
        sw.pack(side="right")
        return sw

    def _pick_apk(self):
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
        log("Applying master phone settings")

        res_raw = self._res.get().split(" ")[0]
        try:
            w, h = res_raw.split("x")
        except Exception:
            w, h = "540", "960"

        dpi_raw  = self._dpi.get().split(" ")[0]
        cpu      = self._cpu.get()
        ram      = self._ram.get().split(" ")[0]

        ld("modify", "--index", "0",
           "--resolution", f"{w},{h},{dpi_raw}",
           "--cpu", cpu,
           "--memory", ram)

        if self._adb.get():
            ld("adb", "--index", "0", "--command",
               "shell settings put global adb_enabled 1")

        if self._apk_path and os.path.exists(self._apk_path):
            self._app._set_status("Installing APK on master…")
            log(f"Installing APK: {Path(self._apk_path).name}")
            ld("installapp", "--index", "0", "--filename", self._apk_path)

        model = self._model.get().strip()
        if model:
            ld("adb", "--index", "0", "--command",
               f"shell setprop ro.product.model \"{model}\"")

        proxy = self._proxy.get().strip()
        if proxy and ":" in proxy:
            h, port = proxy.rsplit(":", 1)
            ld("adb", "--index", "0", "--command",
               f"shell settings put global http_proxy {h}:{port}")

        log("Master phone configured ✓")
        self._app._set_status("Master configured ✓")
        self._phone_frame.set_running(True)

    def _launch_master(self):
        self._app._run_bg(lambda: (
            self._app._set_status("Launching master phone…"),
            log("Launching master phone"),
            ld("launch", "--index", "0"),
            self._phone_frame.set_running(True),
            self._app._set_status("Master phone running")
        ))

    def get_clone_prefix(self) -> str:
        return self._prefix.get().strip() or "CPharm"


# ══════════════════════════════════════════════════════════════════════════════
# FARM PANEL (right side)
# ══════════════════════════════════════════════════════════════════════════════
class FarmPanel(ctk.CTkFrame):
    def __init__(self, master_widget, app, **kw):
        super().__init__(master_widget, fg_color=BG0, corner_radius=0, **kw)
        self._app   = app
        self._cards = {}
        self._auto_restart = False
        self._build()

    def _build(self):
        # Header
        hdr = ctk.CTkFrame(self, fg_color=BG1, corner_radius=0, height=40)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        label(hdr, "  PHONE FARM", size=11, weight="bold", color=G, mono=True).pack(side="left", pady=10)

        # Auto-restart toggle in header
        ar_f = ctk.CTkFrame(hdr, fg_color="transparent")
        ar_f.pack(side="right", padx=12)
        label(ar_f, "AUTO-RESTART", size=8, color=T2, mono=True).pack(side="left", padx=(0, 6))
        self._ar_sw = ctk.CTkSwitch(ar_f, text="", width=40, height=20,
                                     fg_color=BD, progress_color=Y,
                                     button_color=T1, button_hover_color=T0,
                                     command=self._toggle_auto_restart)
        self._ar_sw.pack(side="left")

        separator(self, BD).pack(fill="x")

        # Stats row
        stats = ctk.CTkFrame(self, fg_color=BG1, corner_radius=0, height=60)
        stats.pack(fill="x")
        stats.pack_propagate(False)
        sf = ctk.CTkFrame(stats, fg_color="transparent")
        sf.pack(fill="both", expand=True, padx=12, pady=6)
        self._s_total   = self._stat(sf, "PHONES",  "0")
        self._s_running = self._stat(sf, "RUNNING", "0", G)
        self._s_stopped = self._stat(sf, "STOPPED", "0")
        self._s_ram     = self._stat(sf, "RAM USED",  "0G", Y)
        self._s_max     = self._stat(sf, "MAX EST",   "?",  T1)
        self._s_gpu     = self._stat(sf, "GPU VRAM",  "?",  B)

        separator(self, BD).pack(fill="x")

        # RAM bar
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

        # Clone + global controls
        ctrl = ctk.CTkFrame(self, fg_color=BG1, corner_radius=0, height=52)
        ctrl.pack(fill="x")
        ctrl.pack_propagate(False)
        cf = ctk.CTkFrame(ctrl, fg_color="transparent")
        cf.pack(fill="both", padx=12, pady=8)

        label(cf, "Clone:", size=11, color=T0).pack(side="left")
        self._clone_n = ctk.CTkEntry(cf, width=40, height=30, fg_color=BG3,
                                      border_color=BD, text_color=T0,
                                      font=ctk.CTkFont("Courier New", 12),
                                      justify="center")
        self._clone_n.insert(0, "1")
        self._clone_n.pack(side="left", padx=6)
        btn(cf, "Clone ＋", self._clone_phones,
            fg=G, hover=G2, tc="#000", width=84, bold=True).pack(side="left", padx=(0, 12))

        separator_v = ctk.CTkFrame(cf, width=1, fg_color=BD, corner_radius=0)
        separator_v.pack(side="left", fill="y", padx=6)

        btn(cf, "▶ All", lambda: self._app._run_bg(self._app._start_all),
            fg=G, hover=G2, tc="#000", width=68, bold=True).pack(side="left", padx=(6, 4))
        btn(cf, "■ All", lambda: self._app._run_bg(self._app._stop_all),
            fg=R, hover="#d32f2f", tc="#fff", width=66, bold=True).pack(side="left", padx=(0, 4))
        btn(cf, "↺ Restart", lambda: self._app._run_bg(self._app._restart_all),
            fg=BG3, hover=BD, tc=T0, width=82).pack(side="left")

        separator(self, BD).pack(fill="x")

        # Scrollable phone grid
        self._scroll = ctk.CTkScrollableFrame(self, fg_color=BG0, corner_radius=0)
        self._scroll.pack(fill="both", expand=True)
        self._scroll.grid_columnconfigure((0, 1), weight=1)

        self._empty = label(self._scroll,
                             "No phones cloned yet.\nConfigure master → Clone ＋",
                             size=11, color=T2)
        self._empty.grid(row=0, column=0, columnspan=2, pady=40)

    def _stat(self, parent, lbl, val, color=T0):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.pack(side="left", padx=8)
        v = label(f, val, size=16, weight="bold", color=color, mono=True)
        v.pack()
        label(f, lbl, size=7, color=T2).pack()
        return v

    def _toggle_auto_restart(self):
        self._auto_restart = bool(self._ar_sw.get())
        log(f"Auto-restart {'ON' if self._auto_restart else 'OFF'}")

    def _clone_phones(self):
        try:
            n = int(self._clone_n.get())
        except ValueError:
            n = 1
        n = max(1, min(n, 32))
        self._app._run_bg(lambda: self._app._clone_n_phones(n))

    def render(self, phones: list[dict]):
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
                card.grid(row=i // 2, column=i % 2, padx=6, pady=5, sticky="ew")
                self._cards[idx] = card

        # Auto-restart check
        if self._auto_restart:
            for p in phones:
                if not p["running"]:
                    log(f"Auto-restart: launching {p['name']}")
                    self._app._run_bg(lambda i=p["index"]: ld("launch", "--index", str(i)))

        # Stats
        running      = sum(1 for p in phones if p["running"])
        stopped      = len(phones) - running
        ram_used     = running * RAM_PER_PH
        free_gb      = get_free_ram_gb()
        max_more     = max(0, int(free_gb / RAM_PER_PH))
        total_possible = running + stopped + max_more

        self._s_total.configure(text=str(len(phones)))
        self._s_running.configure(text=str(running))
        self._s_stopped.configure(text=str(stopped))
        self._s_ram.configure(text=f"{ram_used:.0f}G")
        self._s_max.configure(text=str(total_possible))

        total_ram = ram_used + free_gb
        bar_val   = ram_used / total_ram if total_ram > 0 else 0
        self._ram_bar.set(min(bar_val, 1.0))
        bar_color = R if bar_val > 0.85 else Y if bar_val > 0.6 else G
        self._ram_bar.configure(progress_color=bar_color)
        self._ram_lbl.configure(text=f"{ram_used:.1f}/{total_ram:.1f} GB")

    def update_gpu(self, gpu: dict):
        vram = gpu.get("vram_gb", 0)
        self._s_gpu.configure(text=f"{vram}G" if vram else "N/A")

    def _make_card(self, phone):
        running = phone["running"]
        card = ctk.CTkFrame(self._scroll, fg_color=BG2,
                             border_color=G+"44" if running else BD,
                             border_width=1, corner_radius=10)

        # Top row: dot + name + status
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(8, 2))
        dot = label(top, "●", size=9, color=G if running else T2)
        dot.pack(side="left")
        label(top, f"  {phone['name']}", size=11, weight="bold", color=T0, mono=True).pack(side="left")
        status_lbl = label(top, "RUNNING" if running else "stopped",
                            size=8, color=G if running else T2, mono=True)
        status_lbl.pack(side="right")
        card._dot    = dot
        card._status = status_lbl
        card._running = running

        # Pills
        pills = ctk.CTkFrame(card, fg_color="transparent")
        pills.pack(fill="x", padx=10, pady=(0, 4))
        self._pill(pills, f"#{phone['index']}")
        if running:
            self._pill(pills, f"~{RAM_PER_PH:.1f}GB", bg="#ffd74014", fg=Y)
            self._pill(pills, "ADB", bg="#00e67614", fg=G)
        card._pills = pills

        # Action buttons row
        bf = ctk.CTkFrame(card, fg_color="transparent")
        bf.pack(fill="x", padx=10, pady=(0, 8))

        toggle_btn = btn(bf, "Stop" if running else "Start",
                          lambda i=phone["index"], r=running:
                              self._app._run_bg(lambda: self._app._toggle_phone(i, r)),
                          fg=R if running else G,
                          hover="#d32f2f" if running else G2,
                          tc="#fff" if running else "#000",
                          width=62, height=26, bold=True)
        toggle_btn.pack(side="left", padx=(0, 4))
        card._btn = toggle_btn

        btn(bf, "⌘", lambda i=phone["index"]: self._app._run_bg(lambda: self._do_screenshot(i)),
            fg=BG3, hover=BD, tc=T1, width=30, height=26).pack(side="left", padx=(0, 4))

        btn(bf, "ADB", lambda i=phone["index"]: open_adb_shell(i),
            fg=BG3, hover=BD, tc=B, width=44, height=26).pack(side="left", padx=(0, 4))

        btn(bf, "✕", lambda i=phone["index"]: self._confirm_delete(i),
            fg=BG3, hover="#3a1515", tc=R, width=28, height=26).pack(side="right")

        return card

    def _update_card(self, card, phone):
        running = phone["running"]
        if getattr(card, "_running", None) == running:
            return
        card._running = running
        color = G if running else T2
        card._dot.configure(text_color=color)
        card._status.configure(text="RUNNING" if running else "stopped", text_color=color)
        card.configure(border_color=G+"44" if running else BD)

        for w in card._pills.winfo_children():
            w.destroy()
        self._pill(card._pills, f"#{phone['index']}")
        if running:
            self._pill(card._pills, f"~{RAM_PER_PH:.1f}GB", bg="#ffd74014", fg=Y)
            self._pill(card._pills, "ADB", bg="#00e67614", fg=G)

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

    def _do_screenshot(self, index: int):
        self._app._set_status(f"Screenshotting phone #{index}…")
        path = take_screenshot(index)
        if path:
            log(f"Screenshot saved: {path}")
            self._app._set_status(f"Screenshot saved → {Path(path).name}")
            os.startfile(path)
        else:
            log(f"Screenshot failed for phone #{index}")
            self._app._set_status("Screenshot failed")

    def _confirm_delete(self, index: int):
        dialog = ctk.CTkInputDialog(
            text=f"Type 'delete' to remove phone #{index}:",
            title="Confirm Delete"
        )
        resp = dialog.get_input()
        if resp and resp.strip().lower() == "delete":
            self._app._run_bg(lambda: self._do_delete(index))

    def _do_delete(self, index: int):
        self._app._set_status(f"Deleting phone #{index}…")
        log(f"Deleting phone #{index}")
        delete_phone(index)
        self._app._do_refresh()
        self._app._set_status("Phone deleted")


# ══════════════════════════════════════════════════════════════════════════════
# LOG PANEL (bottom strip)
# ══════════════════════════════════════════════════════════════════════════════
class LogPanel(ctk.CTkFrame):
    def __init__(self, parent, **kw):
        super().__init__(parent, fg_color=BG0, corner_radius=0, height=110, **kw)
        self.pack_propagate(False)
        self._build()

    def _build(self):
        hdr = ctk.CTkFrame(self, fg_color=BG1, corner_radius=0, height=22)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        label(hdr, "  ACTIVITY LOG", size=8, color=T2, mono=True).pack(side="left", padx=8)
        btn(hdr, "Clear", self._clear, fg=BG1, hover=BG2, tc=T2,
            width=50, height=20).pack(side="right", padx=4, pady=1)

        self._txt = ctk.CTkTextbox(self, fg_color=BG0, text_color=T2,
                                    font=ctk.CTkFont("Courier New", 9),
                                    border_width=0, corner_radius=0, wrap="word",
                                    activate_scrollbars=True)
        self._txt.pack(fill="both", expand=True)
        self._txt.configure(state="disabled")

    def append(self, line: str):
        self._txt.configure(state="normal")
        self._txt.insert("end", line + "\n")
        self._txt.see("end")
        self._txt.configure(state="disabled")

    def _clear(self):
        self._txt.configure(state="normal")
        self._txt.delete("1.0", "end")
        self._txt.configure(state="disabled")
        _log_lines.clear()


# ══════════════════════════════════════════════════════════════════════════════
# MAIN APPLICATION
# ══════════════════════════════════════════════════════════════════════════════
class CPharmApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("CPharm  —  Phone Farm Manager")
        self.geometry("1180x760")
        self.minsize(900, 600)
        self.configure(fg_color=BG0)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._phones     = []
        self._refreshing = False
        self._gpu        = {}

        self._build_ui()

        # Wire log callback
        global _log_cb
        _log_cb = lambda line: self.after(0, lambda l=line: self._log_panel.append(l))

        log("CPharm started")
        self._schedule_refresh()
        # Load GPU info once in background
        self._run_bg(self._load_gpu)

    # ── UI ─────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        # Top bar
        bar = ctk.CTkFrame(self, fg_color="#050709", corner_radius=0, height=48)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        label(bar, "  [ C·PHARM ]", size=14, weight="bold", color=G, mono=True).pack(side="left", pady=12)
        self._ip_lbl = label(bar, "", size=10, color=T2, mono=True)
        self._ip_lbl.pack(side="left", padx=12)

        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.pack(side="right", padx=12)

        self._dash_btn = btn(right, "◉ Web Dashboard", self._toggle_dashboard,
                              fg=BG3, hover=BD, tc=T1, width=148, height=32)
        self._dash_btn.pack(side="right", padx=(6, 0))

        btn(right, "⟳", lambda: self._run_bg(self._do_refresh),
            fg=BG3, hover=BD, tc=T1, width=36, height=32).pack(side="right")

        self._dot = label(bar, "●", size=10, color=T2)
        self._dot.pack(side="right", padx=4)

        separator(self, BD).pack(fill="x")

        # Body
        body = ctk.CTkFrame(self, fg_color=BG0, corner_radius=0)
        body.pack(fill="both", expand=True)

        # Left: master
        self._master_panel = MasterPanel(body, self)
        self._master_panel.pack(side="left", fill="y")
        self._master_panel.configure(width=256)
        self._master_panel.pack_propagate(False)

        ctk.CTkFrame(body, width=1, fg_color=BD, corner_radius=0).pack(side="left", fill="y")

        # Right: farm + log stacked
        right_col = ctk.CTkFrame(body, fg_color=BG0, corner_radius=0)
        right_col.pack(side="left", fill="both", expand=True)

        self._farm_panel = FarmPanel(right_col, self)
        self._farm_panel.pack(fill="both", expand=True)

        separator(right_col, BD).pack(fill="x")

        self._log_panel = LogPanel(right_col)
        self._log_panel.pack(fill="x")

        # Status bar
        separator(self, BD).pack(fill="x")
        foot = ctk.CTkFrame(self, fg_color="#050709", corner_radius=0, height=26)
        foot.pack(fill="x")
        foot.pack_propagate(False)
        self._status = label(foot, "Ready", size=10, color=T2, mono=True)
        self._status.pack(side="left", padx=12)

    # ── Background tasks ───────────────────────────────────────────────────────
    def _load_gpu(self):
        self._gpu = get_gpu_info()
        name = self._gpu.get("name", "?")
        vram = self._gpu.get("vram_gb", 0)
        log(f"GPU: {name}  {vram}GB VRAM")
        self.after(0, lambda: self._farm_panel.update_gpu(self._gpu))

    def _do_refresh(self):
        if self._refreshing:
            return
        self._refreshing = True
        try:
            self._phones = list_phones()
            ip = get_local_ip()
            self.after(0, lambda: self._ip_lbl.configure(text=f"  {ip}:8080"))
            self.after(0, lambda: self._farm_panel.render(self._phones))
            n_run = sum(1 for p in self._phones if p["running"])
            self._set_status(f"{len(self._phones)} phones · {n_run} running")
        finally:
            self._refreshing = False

    def _start_all(self):
        self._set_status("Starting all phones…")
        log("Starting all phones")
        for p in self._phones:
            if not p["running"]:
                ld("launch", "--index", str(p["index"]))
        time.sleep(2)
        self._do_refresh()

    def _stop_all(self):
        self._set_status("Stopping all phones…")
        log("Stopping all phones")
        for p in self._phones:
            if p["running"]:
                ld("quit", "--index", str(p["index"]))
        time.sleep(1)
        self._do_refresh()

    def _restart_all(self):
        log("Restarting all phones")
        self._stop_all()
        time.sleep(2)
        self._start_all()

    def _toggle_phone(self, index, currently_running):
        name = f"phone #{index}"
        if currently_running:
            self._set_status(f"Stopping {name}…")
            log(f"Stopping {name}")
            ld("quit", "--index", str(index))
        else:
            self._set_status(f"Starting {name}…")
            log(f"Starting {name}")
            ld("launch", "--index", str(index))
        time.sleep(1)
        self._do_refresh()

    def _clone_n_phones(self, n: int):
        prefix = self._master_panel.get_clone_prefix()
        existing = len(self._phones)
        log(f"Cloning {n} phone(s) from master…")
        for i in range(1, n + 1):
            name = f"{prefix}-{existing + i}"
            self._set_status(f"Cloning {name}…")
            log(f"Cloning {name}")
            ld("copy", "--name", name, "--from", "0")
            time.sleep(4)
        self._do_refresh()
        self._set_status(f"Cloned {n} phone(s) ✓")
        log(f"Clone complete: {n} phone(s)")

    def _toggle_dashboard(self):
        self._run_bg(self._do_toggle_dashboard)

    def _do_toggle_dashboard(self):
        if dashboard_running():
            stop_dashboard()
            self.after(0, lambda: self._dash_btn.configure(text="◉ Web Dashboard", text_color=T1))
            self._set_status("Dashboard stopped.")
            log("Web dashboard stopped")
        else:
            start_dashboard()
            time.sleep(1)
            ip = get_local_ip()
            self.after(0, lambda: self._dash_btn.configure(text="◉ Dashboard  ON", text_color=G))
            self._set_status(f"Dashboard → http://{ip}:8080")
            log(f"Web dashboard started → http://{ip}:8080")
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


# ══════════════════════════════════════════════════════════════════════════════
# SETUP WIZARD (shown on first launch)
# ══════════════════════════════════════════════════════════════════════════════
class SetupWizard(ctk.CTkToplevel):
    """Step-by-step first-launch wizard. Closes and lets main app open when done."""

    STEPS = [
        {
            "icon": "📱",
            "title": "Welcome to CPharm!",
            "body": (
                "CPharm runs multiple virtual Android phones on your PC using LDPlayer 9.\n\n"
                "You can use them to test your own apps and ads — each phone has real internet "
                "access just like a real device.\n\n"
                "This wizard will get you set up in under 2 minutes."
            ),
            "action": None,
        },
        {
            "icon": "🔍",
            "title": "Step 1 — LDPlayer 9",
            "body": (
                "LDPlayer 9 is the free Android emulator CPharm uses.\n\n"
                "If you don't have it yet, click 'Download LDPlayer' below — it's free and "
                "takes about 3 minutes to install.\n\n"
                "Already have it installed?  Click Next."
            ),
            "action": ("Download LDPlayer", "https://www.ldplayer.net/"),
        },
        {
            "icon": "⚙️",
            "title": "Step 2 — Configure Master Phone",
            "body": (
                "The Master Phone is your template.\n\n"
                "Set it up once with the right settings (RAM, resolution, your app), "
                "then clone it as many times as your PC can handle.\n\n"
                "After this wizard, use the left panel to configure the master phone. "
                "Then click 'Clone ＋' in the right panel to make copies."
            ),
            "action": None,
        },
        {
            "icon": "📶",
            "title": "Step 3 — Internet & Your App",
            "body": (
                "Each phone gets real internet access automatically — no extra setup needed.\n\n"
                "To install your app:\n"
                "  • Drop your .apk file in the  apks/  folder\n"
                "  • Or use the 'Browse' button in the Master Phone panel\n"
                "  • Click 'Apply' — it installs on master, then every clone gets it too.\n\n"
                "ADB is enabled by default so you can use developer tools."
            ),
            "action": None,
        },
        {
            "icon": "🚀",
            "title": "You're All Set!",
            "body": (
                "Here's the workflow:\n\n"
                "  1. LEFT PANEL → Configure master phone settings\n"
                "  2. Click  ▶ Launch Master  to start it\n"
                "  3. Click  ⚙ Apply  to push settings + install your APK\n"
                "  4. RIGHT PANEL → Enter a number and click  Clone ＋\n"
                "  5. Click  ▶ All  to start all phones\n\n"
                "The web dashboard lets you control phones from your phone browser too!"
            ),
            "action": None,
        },
    ]

    def __init__(self, parent):
        super().__init__(parent)
        self.title("CPharm — Setup")
        self.geometry("520x420")
        self.resizable(False, False)
        self.configure(fg_color=BG1)
        self.grab_set()
        self.lift()
        self.focus_force()

        self._step = 0
        self._build()
        self._show_step(0)

    def _build(self):
        # Progress dots
        dots_f = ctk.CTkFrame(self, fg_color=BG0, height=8, corner_radius=0)
        dots_f.pack(fill="x")
        self._dots_inner = ctk.CTkFrame(dots_f, fg_color="transparent")
        self._dots_inner.pack(pady=8)

        # Icon + title
        self._icon_lbl = label(self, "", size=40)
        self._icon_lbl.pack(pady=(24, 0))

        self._title_lbl = label(self, "", size=16, weight="bold", color=T0)
        self._title_lbl.pack(pady=(6, 0))

        # Body text
        self._body_lbl = ctk.CTkLabel(self, text="", wraplength=440,
                                       font=ctk.CTkFont("Segoe UI", 12),
                                       text_color=T1, justify="left")
        self._body_lbl.pack(padx=32, pady=16, fill="x")

        # Action link button (optional)
        self._action_btn = btn(self, "", lambda: None, fg=B, hover="#2979ff", tc="#fff",
                                width=200, height=34)
        self._action_btn.pack(pady=(0, 8))
        self._action_btn.pack_forget()

        # Nav buttons
        nav = ctk.CTkFrame(self, fg_color=BG0, height=60, corner_radius=0)
        nav.pack(fill="x", side="bottom")
        nav.pack_propagate(False)
        nf = ctk.CTkFrame(nav, fg_color="transparent")
        nf.pack(fill="both", padx=24, pady=12)

        self._back_btn = btn(nf, "← Back", self._back, fg=BG3, hover=BD, tc=T1, width=90)
        self._back_btn.pack(side="left")

        self._next_btn = btn(nf, "Next →", self._next, fg=G, hover=G2, tc="#000",
                              width=110, bold=True)
        self._next_btn.pack(side="right")

    def _show_step(self, idx):
        step = self.STEPS[idx]
        total = len(self.STEPS)

        # Progress dots
        for w in self._dots_inner.winfo_children():
            w.destroy()
        for i in range(total):
            color = G if i == idx else BD
            ctk.CTkFrame(self._dots_inner, width=8, height=8,
                          fg_color=color, corner_radius=4).pack(side="left", padx=3)

        self._icon_lbl.configure(text=step["icon"])
        self._title_lbl.configure(text=step["title"])
        self._body_lbl.configure(text=step["body"])

        if step["action"]:
            txt, url = step["action"]
            self._action_btn.configure(text=txt, command=lambda u=url: webbrowser.open(u))
            self._action_btn.pack(pady=(0, 8))
        else:
            self._action_btn.pack_forget()

        self._back_btn.configure(state="normal" if idx > 0 else "disabled")
        if idx == total - 1:
            self._next_btn.configure(text="Launch CPharm  🚀")
        else:
            self._next_btn.configure(text="Next →")

    def _next(self):
        if self._step < len(self.STEPS) - 1:
            self._step += 1
            self._show_step(self._step)
        else:
            FIRST_RUN_F.touch()
            self.destroy()

    def _back(self):
        if self._step > 0:
            self._step -= 1
            self._show_step(self._step)


# ── Entry ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = CPharmApp()
    # Show setup wizard on first run
    if not FIRST_RUN_F.exists():
        wizard = SetupWizard(app)
        app.wait_window(wizard)
    app.mainloop()
