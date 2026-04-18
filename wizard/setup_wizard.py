"""
CPharm Setup Wizard
Virtual phone farm using Android Studio AVD emulators.

Works on Snapdragon ARM Windows — Android Studio is the ONLY emulator
that officially supports ARM64 Windows and gets Google Play testing right.

Build:
    pip install pyinstaller pillow
    pyinstaller --onefile --windowed --name CPharmSetup setup_wizard.py
"""

import json
import os
import platform
import re
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

REPO_URL       = "https://github.com/twoskoops707/cpharm.git"
DASHBOARD_PORT = 8080
IS_WIN         = platform.system() == "Windows"

BG     = "#0d1117"
BG2    = "#161b22"
BG3    = "#21262d"
BORDER = "#30363d"
ACCENT = "#58a6ff"
GREEN  = "#3fb950"
RED    = "#f85149"
YELLOW = "#d29922"
PURPLE = "#bc8cff"
T1     = "#e6edf3"
T2     = "#8b949e"
T3     = "#6e7681"

FH = ("Segoe UI", 20, "bold")
FS = ("Segoe UI", 10)
FB = ("Segoe UI", 11)
FG = ("Segoe UI", 13)
FM = ("Consolas", 10)

STEP_ICONS = {
    "open_url":        "🌐",
    "tap":             "👆",
    "wait":            "⏳",
    "swipe":           "↕",
    "keyevent":        "⌨",
    "close_app":       "❌",
    "clear_cookies":   "🧹",
    "rotate_identity": "🔄",
    "type_text":       "✏",
}
STEP_LABELS = {
    "open_url":        "Open a website in Chrome",
    "tap":             "Tap the screen at a spot",
    "wait":            "Wait N seconds",
    "swipe":           "Swipe up or down",
    "keyevent":        "Press Back / Home / Enter",
    "close_app":       "Close the app",
    "clear_cookies":   "Clear browser cookies",
    "rotate_identity": "Change IP (Tor)",
    "type_text":       "Type some text",
}

state = {
    "cpharm_dir":  "",
    "python_cmd":  "python",
    "sdk_path":    "",
    "num_phones":  3,
    "phones":      [],
    "avds":        [],
    "_emu_procs":  [],
    "groups": [{
        "name":           "Group 1",
        "phones":         [],
        "steps":          [],
        "stagger_secs":   30,
        "repeat":         1,
        "repeat_forever": False,
    }],
}


# ─── helpers ──────────────────────────────────────────────────────────────────

def run_cmd(cmd, cwd=None, timeout=120):
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=cwd, timeout=timeout,
            shell=isinstance(cmd, str)
        )
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except Exception as e:
        return False, str(e)


def adb(*args, serial=None, timeout=20):
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    cmd += list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def list_adb_devices():
    raw = adb("devices", "-l")
    devices = []
    for line in raw.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2 or parts[1] != "device":
            continue
        serial = parts[0]
        name = serial
        for p in parts[2:]:
            if p.startswith("model:"):
                name = p.split(":", 1)[1].replace("_", " ")
                break
        devices.append({"serial": serial, "name": name})
    return devices


def find_sdk():
    candidates = [
        os.environ.get("ANDROID_HOME", ""),
        os.environ.get("ANDROID_SDK_ROOT", ""),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Android", "Sdk"),
        os.path.expanduser("~/Android/Sdk"),
        os.path.expanduser("~/Library/Android/sdk"),
        "C:/Android/Sdk",
        "C:/Users/Public/Android/Sdk",
    ]
    for p in candidates:
        if p and Path(p, "platform-tools").exists():
            return str(Path(p))
    return ""


def sdk_tool(name):
    sdk = state.get("sdk_path") or find_sdk()
    if not sdk:
        suffix = ".bat" if IS_WIN else ""
        return name + suffix
    base = Path(sdk)
    exts = [".bat", ".cmd", ".exe", ""] if IS_WIN else ["", ".sh"]
    search_dirs = [
        base / "cmdline-tools" / "latest" / "bin",
        base / "cmdline-tools" / "bin",
        base / "emulator",
        base / "platform-tools",
        base / "tools" / "bin",
    ]
    for d in search_dirs:
        for ext in exts:
            p = d / (name + ext)
            if p.exists():
                return str(p)
    return name


def list_avds():
    ok, out = run_cmd([sdk_tool("avdmanager"), "list", "avd", "-c"])
    if not ok:
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip() and not ln.startswith("Error")]


def _machine_arch():
    m = platform.machine().lower()
    if "arm" in m or "aarch" in m:
        return "arm64-v8a"
    return "x86_64"


def create_avd(name, log_fn=None):
    sdk = state.get("sdk_path") or find_sdk()
    if not sdk:
        return False, "Android SDK not found"

    sdkmgr = sdk_tool("sdkmanager")
    avdmgr = sdk_tool("avdmanager")
    arch   = _machine_arch()
    image  = f"system-images;android-34;google_apis;{arch}"

    if log_fn:
        log_fn(f"  Installing Android 14 image ({arch}) — first time takes a while…\n")

    ok, out = run_cmd(
        [sdkmgr, "--install", image, "--sdk_root", sdk],
        timeout=900
    )
    if log_fn and out:
        tail = out[-400:] if len(out) > 400 else out
        log_fn(tail + "\n")
    if not ok:
        return False, f"sdkmanager failed: {out[-200:]}"

    if log_fn:
        log_fn(f"  Creating AVD: {name}…\n")

    proc = subprocess.Popen(
        [avdmgr, "create", "avd", "-n", name,
         "-k", image, "-d", "pixel_6", "--force"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = proc.communicate(input="no\n", timeout=90)
    except subprocess.TimeoutExpired:
        proc.kill()
        return False, "avdmanager timed out"

    if proc.returncode == 0:
        if log_fn:
            log_fn(f"  ✅  {name} created.\n")
        return True, ""
    return False, stderr.strip()


def start_emulator(avd_name, port):
    emu   = sdk_tool("emulator")
    flags = subprocess.CREATE_NO_WINDOW if IS_WIN else 0
    proc  = subprocess.Popen(
        [emu, "-avd", avd_name, "-port", str(port),
         "-no-snapshot-save", "-no-boot-anim", "-wipe-data"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    return proc


def wait_for_boot(serial, timeout=240):
    adb("wait-for-device", serial=serial, timeout=30)
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = adb("shell", "getprop", "sys.boot_completed", serial=serial)
        if out.strip() == "1":
            return True
        time.sleep(3)
    return False


def rotate_android_id(serial):
    new_id = format(hash(serial + str(time.time())) & 0xFFFFFFFFFFFFFFFF, "016x")
    adb("shell", "settings", "put", "secure", "android_id", new_id, serial=serial)
    return new_id


def describe_step(step):
    t    = step.get("type", "")
    icon = STEP_ICONS.get(t, "•")
    if t == "open_url":        return f"{icon}  Open  → {step.get('url', '')}"
    if t == "tap":             return f"{icon}  Tap   → ({step.get('x', 0)}, {step.get('y', 0)})"
    if t == "wait":            return f"{icon}  Wait  → {step.get('seconds', 1)}s"
    if t == "swipe":           return (f"{icon}  Swipe → ({step.get('x1',0)},{step.get('y1',0)})"
                                       f" → ({step.get('x2',0)},{step.get('y2',0)})")
    if t == "keyevent":        return f"{icon}  Key   → {step.get('key', 'BACK')}"
    if t == "close_app":       return f"{icon}  Close → {step.get('package', 'Chrome')}"
    if t == "clear_cookies":   return f"{icon}  Clear cookies"
    if t == "rotate_identity": return f"{icon}  Rotate IP + Android ID"
    if t == "type_text":       return f"{icon}  Type  → \"{step.get('text', '')}\""
    return f"• {t}"


def execute_steps(steps, serial):
    for step in steps:
        t = step.get("type", "")
        if t == "open_url":
            adb("shell", "am", "start", "-a", "android.intent.action.VIEW",
                "-d", step.get("url", ""), serial=serial)
        elif t == "tap":
            adb("shell", "input", "tap",
                str(step.get("x", 0)), str(step.get("y", 0)), serial=serial)
        elif t == "wait":
            time.sleep(float(step.get("seconds", 1)))
        elif t == "swipe":
            adb("shell", "input", "swipe",
                str(step.get("x1", 0)), str(step.get("y1", 0)),
                str(step.get("x2", 0)), str(step.get("y2", 0)),
                str(step.get("ms", 500)), serial=serial)
        elif t == "keyevent":
            adb("shell", "input", "keyevent", step.get("key", "BACK"), serial=serial)
        elif t == "close_app":
            adb("shell", "am", "force-stop",
                step.get("package", "com.android.chrome"), serial=serial)
        elif t == "clear_cookies":
            adb("shell", "pm", "clear", "com.android.chrome", serial=serial)
        elif t == "type_text":
            text = step.get("text", "").replace(" ", "%s").replace("'", "")
            adb("shell", "input", "text", text, serial=serial)
        elif t == "rotate_identity":
            rotate_android_id(serial)
        time.sleep(0.4)


# ─── base page ────────────────────────────────────────────────────────────────

class PageBase(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=BG)
        self.app = parent

    def on_enter(self):  pass
    def can_advance(self): return True

    def header(self, title, sub=""):
        tk.Label(self, text=title, font=FH, bg=BG, fg=T1,
                 justify="left", anchor="w").pack(fill="x", pady=(0, 4))
        if sub:
            tk.Label(self, text=sub, font=FS, bg=BG, fg=T2,
                     justify="left", anchor="w", wraplength=660).pack(fill="x", pady=(0, 12))

    def btn(self, parent, text, cmd, color=ACCENT, width=None, side="left", pady=7):
        kw = dict(
            text=text, command=cmd,
            bg=color, fg=BG if color not in (BG3, T3) else T1,
            font=("Segoe UI", 10, "bold"),
            relief="flat", cursor="hand2", pady=pady, padx=14, bd=0,
        )
        if width:
            kw["width"] = width
        b = tk.Button(parent, **kw)
        b.pack(side=side, padx=(0, 8))
        return b

    def code_row(self, parent, text):
        row = tk.Frame(parent, bg="#000c1a", padx=10, pady=8)
        row.pack(fill="x", pady=(4, 0))
        tk.Label(row, text=text, font=FM, bg="#000c1a", fg=GREEN,
                 anchor="w").pack(side="left", fill="x", expand=True)
        tk.Button(row, text="Copy", font=FS, bg=BG3, fg=T1,
                  relief="flat", cursor="hand2", padx=8,
                  command=lambda t=text: (self.clipboard_clear(),
                                          self.clipboard_append(t))).pack(side="right")


# ─── page 1: welcome ──────────────────────────────────────────────────────────

class WelcomePage(PageBase):
    def __init__(self, parent):
        super().__init__(parent)

        tk.Label(self, text="CPharm Phone Farm", font=("Segoe UI", 26, "bold"),
                 bg=BG, fg=T1).pack(pady=(20, 4))
        tk.Label(self,
                 text="Virtual Android phones on your Snapdragon laptop.\n"
                      "Each phone runs independently — browse, tap, install apps, test, repeat.",
                 font=("Segoe UI", 12), bg=BG, fg=T2, justify="center").pack(pady=(0, 16))

        # Architecture diagram
        c = tk.Canvas(self, bg=BG2, height=140,
                      highlightthickness=1, highlightbackground=BORDER)
        c.pack(fill="x", padx=16, pady=(0, 14))
        c.bind("<Configure>", lambda e: self._draw_diagram(c))

    def _draw_diagram(self, c):
        c.delete("all")
        w = c.winfo_width() or 700
        boxes = [
            (ACCENT,  "Android\nStudio SDK"),
            (GREEN,   "Virtual Phones\n(AVD Emulators)"),
            (YELLOW,  "CPharm\nDashboard"),
            (PURPLE,  "Wizard\nControl"),
        ]
        bw = 130
        gap = (w - len(boxes) * bw) // (len(boxes) + 1)
        y1, y2 = 25, 100
        positions = []
        for i, (col, label) in enumerate(boxes):
            x1 = gap + i * (bw + gap)
            x2 = x1 + bw
            mx, my = (x1 + x2) // 2, (y1 + y2) // 2
            c.create_rectangle(x1, y1, x2, y2, fill=col + "22",
                                outline=col, width=2, tags="box")
            c.create_text(mx, my, text=label, fill=T1,
                          font=("Segoe UI", 9, "bold"), justify="center")
            positions.append((x1, x2, y1, y2))

        for i in range(len(positions) - 1):
            _, rx, _, _ = positions[i]
            lx, _, _, _ = positions[i + 1]
            mid_y = (y1 + y2) // 2
            c.create_line(rx, mid_y, lx, mid_y, fill=T3, width=2, arrow="last")

        c.create_text(w // 2, 125,
                      text="All on your Snapdragon Windows laptop  —  no extra hardware",
                      fill=T2, font=("Segoe UI", 9))

        tk.Label(self, text="What you get:",
                 font=("Segoe UI", 10, "bold"), bg=BG, fg=YELLOW,
                 anchor="w").pack(fill="x", padx=4)
        f = tk.Frame(self, bg=BG2, padx=16, pady=12)
        f.pack(fill="x", pady=(4, 0))
        items = [
            ("🤖", "Multiple isolated Android phones — each its own world"),
            ("🌐", "Each phone browses, taps, scrolls, installs apps via ADB"),
            ("🔄", "New Android ID + different IP (Tor) on every session"),
            ("📱", "Google Play closed testing — phones act like real registered devices"),
            ("⚡", "Parallel groups — 5 phones test your app while 5 browse your site"),
            ("🎯", "Works on Snapdragon ARM — uses Windows Hypervisor, no Intel needed"),
        ]
        for icon, text in items:
            row = tk.Frame(f, bg=BG2)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=icon, font=("Segoe UI", 12), bg=BG2,
                     width=3).pack(side="left")
            tk.Label(row, text=text, font=FB, bg=BG2, fg=T1,
                     anchor="w").pack(side="left")


# ─── page 2: android studio ───────────────────────────────────────────────────

class AndroidStudioPage(PageBase):
    def __init__(self, parent):
        super().__init__(parent)
        self._ready = False
        self.header(
            "Step 1 — Android Studio",
            "Google's free developer tool. It includes the emulator that powers your virtual phones.\n"
            "The ARM64 version runs natively on Snapdragon — no emulation layers."
        )

        # Status box
        self._status_box = tk.Frame(self, bg=BG2, padx=14, pady=12,
                                    highlightthickness=1, highlightbackground=BORDER)
        self._status_box.pack(fill="x", pady=(0, 10))
        self._sdk_lbl = tk.Label(self._status_box, text="Checking…",
                                  font=FB, bg=BG2, fg=T2, anchor="w")
        self._sdk_lbl.pack(fill="x")
        self._path_lbl = tk.Label(self._status_box, text="",
                                   font=FM, bg=BG2, fg=T3, anchor="w")
        self._path_lbl.pack(fill="x")

        # Install guide
        guide = tk.Frame(self, bg=BG3, padx=14, pady=12)
        guide.pack(fill="x", pady=(0, 10))
        tk.Label(guide, text="INSTALL ANDROID STUDIO  (if you haven't yet):",
                 font=("Segoe UI", 10, "bold"), bg=BG3, fg=YELLOW, anchor="w").pack(fill="x")
        steps = (
            "1.  Click the button below to open the Android Studio download page\n"
            "2.  Click 'Download Android Studio' — choose  Windows ARM64\n"
            "3.  Run the downloaded installer (android-studio-*.exe)\n"
            "4.  Click Next on every screen — all defaults are perfect\n"
            "5.  Open Android Studio after install — it will download the SDK automatically\n"
            "6.  Wait for the 'Android Studio Setup Wizard' to finish completely\n"
            "7.  Come back here and click 'Check Again'"
        )
        tk.Label(guide, text=steps, font=FB, bg=BG3, fg=T2,
                 justify="left", anchor="w").pack(fill="x", pady=(6, 8))
        tk.Button(guide, text="📥  Open Android Studio Download Page",
                  font=("Segoe UI", 10, "bold"), bg=ACCENT, fg=BG,
                  relief="flat", cursor="hand2", padx=14, pady=7,
                  command=lambda: webbrowser.open(
                      "https://developer.android.com/studio")).pack(anchor="w")

        # Manual SDK path
        sep = tk.Frame(self, bg=BORDER, height=1)
        sep.pack(fill="x", pady=8)
        tk.Label(self, text="Already installed? Paste your SDK folder path here (optional):",
                 font=FS, bg=BG, fg=T2, anchor="w").pack(fill="x")
        path_row = tk.Frame(self, bg=BG)
        path_row.pack(fill="x", pady=4)
        self._path_var = tk.StringVar()
        tk.Entry(path_row, textvariable=self._path_var, font=FM, bg=BG2, fg=T1,
                 insertbackground=T1, relief="flat",
                 width=52).pack(side="left", padx=(0, 6))
        tk.Button(path_row, text="Browse…", font=FS, bg=BG3, fg=T1,
                  relief="flat", cursor="hand2", padx=10, pady=5,
                  command=self._browse).pack(side="left")

        # Check button
        check_row = tk.Frame(self, bg=BG)
        check_row.pack(fill="x", pady=8)
        self.btn(check_row, "🔍  Check Again", self._check)
        self._result_lbl = tk.Label(check_row, text="", font=FS, bg=BG, fg=T2)
        self._result_lbl.pack(side="left", padx=8)

    def on_enter(self):
        self._check()

    def _browse(self):
        d = filedialog.askdirectory(
            title="Select Android SDK folder  (the one containing platform-tools)")
        if d:
            self._path_var.set(d)
            self._check()

    def _check(self):
        manual = self._path_var.get().strip()
        if manual and Path(manual, "platform-tools").exists():
            state["sdk_path"] = manual
        else:
            sdk = find_sdk()
            if sdk:
                state["sdk_path"] = sdk
                self._path_var.set(sdk)

        sdk = state.get("sdk_path", "")
        if not sdk:
            self._sdk_lbl.config(text="❌  Android SDK not found.", fg=RED)
            self._path_lbl.config(text="Install Android Studio, let it finish setup, then click Check Again.")
            self._result_lbl.config(text="", fg=T2)
            self._ready = False
            return False

        has_emu = Path(sdk_tool("emulator")).exists()
        has_avd = Path(sdk_tool("avdmanager")).exists()
        has_sdk = Path(sdk_tool("sdkmanager")).exists()
        has_adb = run_cmd(["adb", "version"])[0]

        if has_emu and has_avd and has_sdk:
            self._sdk_lbl.config(
                text="✅  Android Studio SDK found — emulator + tools ready!",
                fg=GREEN)
            self._path_lbl.config(text=f"SDK: {sdk}", fg=T3)
            self._result_lbl.config(text="All good — click Next →", fg=GREEN)
            self._ready = True
        elif sdk:
            missing = []
            if not has_emu: missing.append("emulator")
            if not has_avd: missing.append("avdmanager")
            if not has_sdk: missing.append("sdkmanager")
            self._sdk_lbl.config(
                text=f"⚠  SDK found but missing: {', '.join(missing)}",
                fg=YELLOW)
            self._path_lbl.config(
                text="Open Android Studio → SDK Manager → install 'Android SDK Command-line Tools'",
                fg=YELLOW)
            self._result_lbl.config(text="", fg=T2)
            self._ready = False

        if not has_adb:
            self._result_lbl.config(
                text="  ⚠  ADB not in PATH. Open a new Terminal after Android Studio installs.",
                fg=YELLOW)

        return self._ready

    def can_advance(self):
        if not self._check():
            messagebox.showerror(
                "Android Studio not ready",
                "Android Studio SDK is required.\n\n"
                "1. Download from developer.android.com/studio (ARM64 version)\n"
                "2. Install and open it — let Setup Wizard finish\n"
                "3. Click 'Check Again' here\n\n"
                "If SDK is installed but not found, click Browse and select your SDK folder."
            )
            return False
        return True


# ─── page 3: phone farm setup ─────────────────────────────────────────────────

class PhoneFarmPage(PageBase):
    def __init__(self, parent):
        super().__init__(parent)
        self._done = False
        self.header(
            "Step 2 — Create Virtual Phones",
            "The wizard downloads Android 14 and creates your virtual phones automatically.\n"
            "One-time setup. Each phone uses ~2 GB RAM + ~4 GB disk."
        )

        # Count selection
        count_box = tk.Frame(self, bg=BG3, padx=16, pady=14)
        count_box.pack(fill="x", pady=(0, 10))
        tk.Label(count_box, text="How many virtual phones?",
                 font=("Segoe UI", 11, "bold"), bg=BG3, fg=T1, anchor="w").pack(fill="x")
        tk.Label(count_box,
                 text="16 GB RAM → up to 5 phones  |  32 GB RAM → up to 10 phones\n"
                      "Start small. You can always add more later.",
                 font=FS, bg=BG3, fg=T2, anchor="w").pack(fill="x", pady=(4, 10))

        picker = tk.Frame(count_box, bg=BG3)
        picker.pack(fill="x")
        self._count_btns: dict[int, tk.Button] = {}
        for n in [1, 2, 3, 5, 8, 10]:
            b = tk.Button(picker, text=str(n), width=4,
                          font=("Segoe UI", 12, "bold"),
                          bg=ACCENT if n == 3 else BG2,
                          fg=BG if n == 3 else T1,
                          relief="flat", cursor="hand2",
                          command=lambda v=n: self._pick_count(v))
            b.pack(side="left", padx=3)
            self._count_btns[n] = b

        self._count_lbl = tk.Label(count_box, text="3 phones selected",
                                    font=FS, bg=BG3, fg=T2)
        self._count_lbl.pack(anchor="w", pady=(8, 0))

        # What happens box
        explain = tk.Frame(self, bg=BG2, padx=14, pady=10)
        explain.pack(fill="x", pady=(0, 10))
        tk.Label(explain, text="What clicking Create does:",
                 font=("Segoe UI", 10, "bold"), bg=BG2, fg=T1, anchor="w").pack(fill="x")
        tk.Label(explain,
                 text="  1.  Downloads Android 14 system image (~1 GB) — only once\n"
                      "  2.  Creates one Pixel 6 virtual phone per slot\n"
                      "  3.  Each phone gets a unique device ID and storage\n"
                      "  4.  Names them CPharm_Phone_1, CPharm_Phone_2, etc.",
                 font=FB, bg=BG2, fg=T2, justify="left", anchor="w").pack(fill="x", pady=(4, 0))

        # Create button
        create_row = tk.Frame(self, bg=BG)
        create_row.pack(fill="x", pady=(0, 8))
        self._create_btn = tk.Button(create_row,
                                     text="  ▶  Create Phone Farm  ",
                                     font=("Segoe UI", 12, "bold"),
                                     bg=GREEN, fg=BG, relief="flat",
                                     cursor="hand2", padx=20, pady=10,
                                     command=self._create)
        self._create_btn.pack(side="left", padx=(0, 10))
        self._progress_lbl = tk.Label(create_row, text="", font=FS, bg=BG, fg=T2)
        self._progress_lbl.pack(side="left")

        # Log
        log_fr = tk.Frame(self, bg=BG2)
        log_fr.pack(fill="both", expand=True, pady=(4, 0))
        self._log_box = tk.Text(log_fr, height=9, font=FM, bg=BG2, fg=T1,
                                relief="flat", state="disabled", wrap="word")
        sb = tk.Scrollbar(log_fr, orient="vertical", command=self._log_box.yview)
        self._log_box.configure(yscrollcommand=sb.set)
        self._log_box.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    def _pick_count(self, n):
        state["num_phones"] = n
        for num, btn in self._count_btns.items():
            btn.config(bg=ACCENT if num == n else BG2,
                       fg=BG    if num == n else T1)
        ram  = n * 2
        disk = n * 4
        self._count_lbl.config(
            text=f"{n} phone{'s' if n != 1 else ''} selected  "
                 f"(~{ram} GB RAM, ~{disk} GB disk needed)")

    def on_enter(self):
        self._pick_count(state.get("num_phones", 3))
        existing = [a for a in list_avds() if a.startswith("CPharm_Phone_")]
        if existing:
            state["avds"] = existing
            self._log_write(f"Found {len(existing)} existing phone(s): "
                            f"{', '.join(existing)}\n")
            self._log_write("Phones already created! Click Next → to continue.\n")
            self._progress_lbl.config(
                text=f"✅  {len(existing)} phone(s) ready", fg=GREEN)
            self._done = True

    def _log_write(self, text):
        self._log_box.config(state="normal")
        self._log_box.insert("end", text)
        self._log_box.see("end")
        self._log_box.config(state="disabled")

    def _create(self):
        n = state.get("num_phones", 3)
        self._create_btn.config(state="disabled", text=" Creating phones… ")
        self._log_write(f"Creating {n} virtual phone(s). This may take 10–30 minutes.\n\n")

        def go():
            created = []
            for i in range(1, n + 1):
                name = f"CPharm_Phone_{i}"
                self._progress_lbl.config(
                    text=f"Creating {i}/{n}: {name}…", fg=YELLOW)
                self._log_write(f"══ Phone {i} of {n}: {name} ══\n")
                ok, err = create_avd(name, log_fn=self._log_write)
                if ok:
                    created.append(name)
                else:
                    self._log_write(f"  ❌  Error: {err}\n")

            state["avds"] = created
            total = len(created)

            if created:
                self._log_write(f"\n✅  Done! {total} phone(s) created.\n")
                self._log_write("Click Next → to start the phones.\n")
                self._done = True
                self._progress_lbl.config(
                    text=f"✅  {total} phone(s) ready!", fg=GREEN)
            else:
                self._log_write("\n❌  No phones were created. "
                                "Check errors above.\n")
                self._progress_lbl.config(
                    text="❌  Failed — check log", fg=RED)

            self._create_btn.config(
                state="normal",
                text="✅  Phones Created!" if created else "  ▶  Try Again  ")

        threading.Thread(target=go, daemon=True).start()

    def can_advance(self):
        if not state.get("avds"):
            existing = [a for a in list_avds() if a.startswith("CPharm_Phone_")]
            if existing:
                state["avds"] = existing
                return True
            messagebox.showinfo(
                "Phones not created yet",
                "Click 'Create Phone Farm' and wait for it to finish.\n\n"
                "This only needs to happen once. If it's slow, it's downloading Android — just wait."
            )
            return False
        return True


# ─── page 4: boot phones ──────────────────────────────────────────────────────

class BootPage(PageBase):
    def __init__(self, parent):
        super().__init__(parent)
        self.header(
            "Step 3 — Start the Phones",
            "Boots your virtual phones. First boot takes 2–5 minutes per phone."
        )

        self._status_frame = tk.Frame(self, bg=BG)
        self._status_frame.pack(fill="x", pady=(0, 10))
        self._status_rows: dict[str, tk.Label] = {}

        ctrl = tk.Frame(self, bg=BG)
        ctrl.pack(fill="x", pady=6)
        self._boot_btn = tk.Button(ctrl, text="▶  Start All Phones",
                                   font=("Segoe UI", 10, "bold"),
                                   bg=GREEN, fg=BG, relief="flat",
                                   cursor="hand2", padx=14, pady=7,
                                   command=self._boot_all)
        self._boot_btn.pack(side="left", padx=(0, 8))
        self._stop_btn = tk.Button(ctrl, text="■  Stop All",
                                   font=("Segoe UI", 10, "bold"),
                                   bg=RED, fg=BG, relief="flat",
                                   cursor="hand2", padx=14, pady=7,
                                   state="disabled",
                                   command=self._stop_all)
        self._stop_btn.pack(side="left")
        self._overall_lbl = tk.Label(ctrl, text="", font=FS, bg=BG, fg=T2)
        self._overall_lbl.pack(side="left", padx=10)

        log_fr = tk.Frame(self, bg=BG2)
        log_fr.pack(fill="both", expand=True, pady=6)
        self._log = tk.Text(log_fr, height=8, font=FM, bg=BG2, fg=T1,
                            relief="flat", state="disabled", wrap="word")
        sb = tk.Scrollbar(log_fr, orient="vertical", command=self._log.yview)
        self._log.configure(yscrollcommand=sb.set)
        self._log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    def on_enter(self):
        self._rebuild_grid()
        devs = list_adb_devices()
        running = [d for d in devs if d["serial"].startswith("emulator-")]
        if running:
            state["phones"] = running
            self._overall_lbl.config(
                text=f"✅  {len(running)} phone(s) already running!",
                fg=GREEN)
            for d in running:
                row_lbl = self._status_rows.get(d["name"])
                if row_lbl:
                    row_lbl.config(text="✅  Running", fg=GREEN)

    def _rebuild_grid(self):
        for w in self._status_frame.winfo_children():
            w.destroy()
        self._status_rows.clear()
        for i, avd in enumerate(state.get("avds", [])):
            port   = 5554 + i * 2
            serial = f"emulator-{port}"
            row    = tk.Frame(self._status_frame, bg=BG2, padx=12, pady=7,
                              highlightthickness=1, highlightbackground=BORDER)
            row.pack(fill="x", pady=2)
            tk.Label(row, text="📱", font=("Segoe UI", 14),
                     bg=BG2).pack(side="left")
            tk.Label(row, text=avd, font=FB, bg=BG2, fg=T1,
                     width=26, anchor="w").pack(side="left")
            tk.Label(row, text=f"ADB: {serial}", font=FM, bg=BG2,
                     fg=T3, width=20, anchor="w").pack(side="left")
            lbl = tk.Label(row, text="Not started", font=FS,
                           bg=BG2, fg=T3)
            lbl.pack(side="left", padx=6)
            self._status_rows[avd] = lbl

    def _log_write(self, text):
        self._log.config(state="normal")
        self._log.insert("end", text)
        self._log.see("end")
        self._log.config(state="disabled")

    def _boot_all(self):
        avds = state.get("avds", [])
        if not avds:
            messagebox.showerror("No phones", "Go back and create phones first.")
            return
        self._boot_btn.config(state="disabled")
        self._stop_btn.config(state="normal")
        self._log_write(f"Starting {len(avds)} phone(s)…\n")

        def go():
            procs  = []
            for i, avd in enumerate(avds):
                port   = 5554 + i * 2
                serial = f"emulator-{port}"
                self._status_rows[avd].config(text="⏳  Launching…", fg=YELLOW)
                proc = start_emulator(avd, port)
                procs.append((avd, port, serial, proc))
                self._log_write(f"  Launched {avd} on port {port}\n")
                time.sleep(2)

            state["_emu_procs"] = [p for _, _, _, p in procs]

            phones = []
            for avd, port, serial, proc in procs:
                self._status_rows[avd].config(text="⏳  Booting…", fg=YELLOW)
                self._log_write(f"  Waiting for {avd} to finish booting…\n")
                ok = wait_for_boot(serial)
                if ok:
                    new_id = rotate_android_id(serial)
                    phones.append({"serial": serial, "name": avd})
                    self._status_rows[avd].config(text="✅  Ready", fg=GREEN)
                    self._log_write(f"  ✅  {avd} ready! Android ID: {new_id[:8]}…\n")
                else:
                    self._status_rows[avd].config(text="❌  Timed out", fg=RED)
                    self._log_write(f"  ❌  {avd} didn't boot in time.\n")

            state["phones"] = phones
            n = len(phones)
            self._overall_lbl.config(
                text=f"✅  {n} phone(s) running!" if n else "❌  No phones booted.",
                fg=GREEN if n else RED)
            self._log_write(
                f"\n{n}/{len(avds)} phone(s) running.\n" +
                ("Click Next → to configure groups.\n" if n else ""))

        threading.Thread(target=go, daemon=True).start()

    def _stop_all(self):
        for proc in state.get("_emu_procs", []):
            try: proc.terminate()
            except Exception: pass
        state["_emu_procs"] = []
        state["phones"]     = []
        for lbl in self._status_rows.values():
            lbl.config(text="Stopped", fg=T3)
        self._overall_lbl.config(text="All phones stopped.", fg=T2)
        self._boot_btn.config(state="normal")
        self._stop_btn.config(state="disabled")

    def can_advance(self):
        if not state.get("phones"):
            messagebox.showwarning(
                "Phones not running",
                "Start the phones and wait for ✅ before continuing.\n\n"
                "Click 'Start All Phones' and wait a few minutes."
            )
            return False
        return True


# ─── page 5: google play testing guide ───────────────────────────────────────

class PlayStorePage(PageBase):
    """Explains how to use the phone farm for Google Play closed testing."""
    def __init__(self, parent):
        super().__init__(parent)
        self.header(
            "Step 4 — Google Play Closed Testing",
            "How to use your virtual phones to get your app into the Play Store."
        )

        # Canvas scrolled area
        outer = tk.Frame(self, bg=BG)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        sb     = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(canvas, bg=BG)
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfig(win_id, width=e.width))

        sections = [
            (ACCENT, "What is Closed Testing?",
             "Google lets you test your app with specific people before going live.\n"
             "These testers install the app through the normal Play Store — it looks real.\n"
             "Your virtual phones act as the 'testers'. Google accepts emulators for this."),

            (GREEN, "Step A — Upload Your APK to Play Console",
             "1. Go to play.google.com/console and sign in\n"
             "2. Create a new app (or open your existing one)\n"
             "3. Go to:  Testing → Internal testing  (or Closed testing)\n"
             "4. Click 'Create new release'\n"
             "5. Upload your APK or AAB file\n"
             "6. Fill in release notes, click Save"),

            (YELLOW, "Step B — Add Tester Google Accounts",
             "1. In Play Console, go to:  Testing → Internal testing → Testers\n"
             "2. Click 'Create email list' or 'Add email addresses'\n"
             "3. Add one Gmail address per virtual phone you want to use\n"
             "4. Create free Gmail accounts at gmail.com — one per phone\n"
             "5. Click Save changes\n"
             "6. Copy the 'opt-in URL' — you'll open it on each virtual phone"),

            (PURPLE, "Step C — Sign In Each Virtual Phone",
             "On each virtual phone screen (visible after booting):\n"
             "1. Open Settings → Accounts → Add account → Google\n"
             "2. Sign in with one of the tester Gmail accounts\n"
             "3. Open the opt-in URL in Chrome (paste with adb or type it)\n"
             "4. The phone will see your app in the Play Store\n"
             "5. Install and test normally"),

            (RED, "Step D — Identity Rotation Between Test Sessions",
             "To make each test session look fresh:\n"
             "1. CPharm rotates Android ID automatically on every phone start\n"
             "2. Enable 'Rotate IP' steps in your sequence to change the IP via Tor\n"
             "3. Wipe-data flag is set so each emulator boot is factory fresh\n"
             "4. Use different Google accounts per phone for account diversity"),
        ]

        for color, title, body in sections:
            box = tk.Frame(inner, bg=BG2, padx=14, pady=12,
                           highlightthickness=2, highlightbackground=color)
            box.pack(fill="x", pady=4, padx=2)
            hdr = tk.Frame(box, bg=BG2)
            hdr.pack(fill="x")
            tk.Frame(hdr, bg=color, width=4).pack(side="left", fill="y", padx=(0, 10))
            tk.Label(hdr, text=title, font=("Segoe UI", 11, "bold"),
                     bg=BG2, fg=color, anchor="w").pack(side="left")
            tk.Label(box, text=body, font=FB, bg=BG2, fg=T2,
                     justify="left", anchor="w").pack(fill="x", pady=(8, 0))

        tk.Button(inner, text="Open Google Play Console",
                  font=("Segoe UI", 10, "bold"), bg=GREEN, fg=BG,
                  relief="flat", cursor="hand2", padx=14, pady=8,
                  command=lambda: webbrowser.open("https://play.google.com/console")).pack(
                      anchor="w", pady=10, padx=2)


# ─── page 6: groups & sequences ───────────────────────────────────────────────

class GroupsPage(PageBase):
    def __init__(self, parent):
        super().__init__(parent)
        self.header(
            "Step 5 — Groups & Sequences",
            "Split phones into groups. Each group does something different at the same time."
        )

        ctrl = tk.Frame(self, bg=BG)
        ctrl.pack(fill="x", pady=(0, 8))
        self.btn(ctrl, "+ Add Group", self._add_group, color=GREEN)
        self._count_lbl = tk.Label(ctrl, text="", font=FS, bg=BG, fg=T2)
        self._count_lbl.pack(side="left", padx=8)

        wrapper = tk.Frame(self, bg=BG)
        wrapper.pack(fill="both", expand=True)
        self._canvas = tk.Canvas(wrapper, bg=BG, highlightthickness=0)
        sb = tk.Scrollbar(wrapper, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)
        self._inner = tk.Frame(self._canvas, bg=BG)
        self._win_id = self._canvas.create_window((0, 0), window=self._inner, anchor="nw")
        self._inner.bind("<Configure>",
                         lambda e: self._canvas.configure(
                             scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>",
                          lambda e: self._canvas.itemconfig(self._win_id, width=e.width))
        self._cards: list[tk.Frame] = []

    def on_enter(self):
        if state["phones"] and state["groups"]:
            for g in state["groups"]:
                if not g["phones"]:
                    g["phones"] = [p["serial"] for p in state["phones"]]
        self._rebuild()

    def _add_group(self):
        n = len(state["groups"]) + 1
        state["groups"].append({
            "name":           f"Group {n}",
            "phones":         [],
            "steps":          [],
            "stagger_secs":   30,
            "repeat":         1,
            "repeat_forever": False,
        })
        self._rebuild()

    def _remove_group(self, idx):
        if len(state["groups"]) <= 1:
            messagebox.showinfo("Can't remove", "Need at least one group.")
            return
        state["groups"].pop(idx)
        self._rebuild()

    def _rebuild(self):
        for c in self._cards:
            c.destroy()
        self._cards.clear()
        for i, g in enumerate(state["groups"]):
            card = self._build_card(i, g)
            card.pack(fill="x", pady=5, padx=2)
            self._cards.append(card)
        n = len(state["groups"])
        self._count_lbl.config(
            text=f"{n} group{'s' if n != 1 else ''}  — all run at the same time")

    def _build_card(self, idx, group):
        colors = [ACCENT, GREEN, YELLOW, RED, PURPLE]
        col    = colors[idx % len(colors)]

        card = tk.Frame(self._inner, bg=BG2, padx=14, pady=12,
                        highlightthickness=1, highlightbackground=BORDER)

        hdr = tk.Frame(card, bg=BG2)
        hdr.pack(fill="x")
        tk.Frame(hdr, bg=col, width=5).pack(side="left", fill="y", padx=(0, 10))

        name_var = tk.StringVar(value=group["name"])
        tk.Entry(hdr, textvariable=name_var, font=("Segoe UI", 13, "bold"),
                 bg=BG3, fg=T1, relief="flat", width=20,
                 insertbackground=T1).pack(side="left")
        name_var.trace("w", lambda *_: group.update({"name": name_var.get()}))

        if len(state["groups"]) > 1:
            tk.Button(hdr, text="Remove", font=FS, bg=RED, fg=BG,
                      relief="flat", cursor="hand2", padx=8, pady=3,
                      command=lambda i=idx: self._remove_group(i)).pack(side="right")

        tk.Frame(card, bg=BORDER, height=1).pack(fill="x", pady=8)

        # Phone assignment
        tk.Label(card, text="Which phones are in this group?",
                 font=("Segoe UI", 10, "bold"), bg=BG2, fg=T1, anchor="w").pack(fill="x")
        pf = tk.Frame(card, bg=BG2)
        pf.pack(fill="x", pady=(4, 8))

        if not state["phones"]:
            tk.Label(pf, text="Go back to Step 3 and start the phones first.",
                     font=FS, bg=BG2, fg=RED).pack(anchor="w")
        else:
            for phone in state["phones"]:
                var = tk.BooleanVar(value=phone["serial"] in group["phones"])

                def toggle(v=var, s=phone["serial"], g=group):
                    if v.get():
                        if s not in g["phones"]: g["phones"].append(s)
                    else:
                        g["phones"] = [x for x in g["phones"] if x != s]

                tk.Checkbutton(pf, text=f"  {phone['name']}  ({phone['serial']})",
                               variable=var, onvalue=True, offvalue=False,
                               command=toggle, font=FB,
                               bg=BG2, fg=T1, selectcolor=BG3,
                               activebackground=BG2).pack(anchor="w")

        tk.Frame(card, bg=BORDER, height=1).pack(fill="x", pady=(0, 8))

        # Sequence
        seq_ctrl = tk.Frame(card, bg=BG2)
        seq_ctrl.pack(fill="x")
        tk.Label(seq_ctrl, text="What these phones do:",
                 font=("Segoe UI", 10, "bold"), bg=BG2, fg=T1, anchor="w").pack(fill="x")

        step_row = tk.Frame(card, bg=BG2)
        step_row.pack(fill="x", pady=(4, 8))
        step_lbl = tk.Label(step_row,
                            text=f"{len(group['steps'])} steps",
                            font=FB, bg=BG2,
                            fg=GREEN if group["steps"] else YELLOW)
        step_lbl.pack(side="left", padx=(0, 10))

        def edit(i=idx, lbl=step_lbl):
            win = SequenceEditorWindow(self.app, state["groups"][i])
            self.app.wait_window(win)
            cnt = len(state["groups"][i]["steps"])
            lbl.config(text=f"{cnt} steps",
                       fg=GREEN if cnt else YELLOW)

        tk.Button(step_row, text="✏  Edit Sequence", font=FS,
                  bg=ACCENT, fg=BG, relief="flat", cursor="hand2",
                  padx=10, pady=4, command=edit).pack(side="left")

        tk.Frame(card, bg=BORDER, height=1).pack(fill="x", pady=(0, 8))

        # Timing
        timing = tk.Frame(card, bg=BG2)
        timing.pack(fill="x")

        def lbl(text): return tk.Label(timing, text=text, font=FS, bg=BG2, fg=T2)
        def spin(var, lo, hi, w=5):
            return tk.Spinbox(timing, from_=lo, to=hi, textvariable=var,
                              width=w, font=FM, bg=BG3, fg=T1, relief="flat")

        stag_var    = tk.IntVar(value=group["stagger_secs"])
        rep_var     = tk.IntVar(value=group["repeat"])
        forever_var = tk.BooleanVar(value=group["repeat_forever"])

        lbl("Delay between phones:").pack(side="left")
        spin(stag_var, 0, 3600).pack(side="left", padx=4)
        lbl("sec  |  Repeat:").pack(side="left", padx=(4, 0))
        spin(rep_var, 1, 9999).pack(side="left", padx=4)
        lbl("times  |").pack(side="left")
        tk.Checkbutton(timing, text="Forever", variable=forever_var,
                       font=FS, bg=BG2, fg=T1, selectcolor=BG3,
                       activebackground=BG2).pack(side="left", padx=4)

        def save(*_):
            group.update({"stagger_secs": stag_var.get(),
                          "repeat":       rep_var.get(),
                          "repeat_forever": forever_var.get()})

        stag_var.trace("w", save)
        rep_var.trace("w", save)
        forever_var.trace("w", save)

        return card

    def can_advance(self):
        for g in state["groups"]:
            if not g["phones"]:
                messagebox.showwarning(
                    "Empty group",
                    f"'{g['name']}' has no phones assigned.\n"
                    "Tick at least one phone or remove the group.")
                return False
        return True


# ─── sequence editor window ───────────────────────────────────────────────────

class SequenceEditorWindow(tk.Toplevel):
    def __init__(self, parent, group):
        super().__init__(parent)
        self.group = group
        self.title(f"Sequence — {group['name']}")
        self.config(bg=BG)
        self.geometry("640x560")
        self.resizable(True, True)
        self.grab_set()
        self.transient(parent)

        tk.Label(self, text=f"Sequence: {group['name']}",
                 font=("Segoe UI", 14, "bold"), bg=BG, fg=T1).pack(
                     pady=(14, 2), padx=16, anchor="w")
        tk.Label(self,
                 text="Each step runs top-to-bottom on the phone. "
                      "Drag to reorder, or use ▲ / ▼.",
                 font=FS, bg=BG, fg=T2).pack(padx=16, anchor="w", pady=(0, 8))

        ctrl = tk.Frame(self, bg=BG)
        ctrl.pack(fill="x", padx=16, pady=4)
        for text, cmd, color in [
            ("+ Add Step",  self._add,    GREEN),
            ("Remove",      self._remove, RED),
            ("▲ Up",        self._up,     BG3),
            ("▼ Down",      self._dn,     BG3),
        ]:
            tk.Button(ctrl, text=text, command=cmd,
                      bg=color, fg=BG if color not in (BG3,) else T1,
                      font=("Segoe UI", 10, "bold"),
                      relief="flat", cursor="hand2",
                      pady=6, padx=12).pack(side="left", padx=(0, 6))

        fr = tk.Frame(self, bg=BG2)
        fr.pack(fill="both", expand=True, padx=16, pady=8)
        self._lb = tk.Listbox(fr, font=FM, bg=BG2, fg=T1,
                              selectbackground=ACCENT, relief="flat",
                              height=12, activestyle="none")
        sb = tk.Scrollbar(fr, orient="vertical", command=self._lb.yview)
        self._lb.configure(yscrollcommand=sb.set)
        self._lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        bottom = tk.Frame(self, bg=BG)
        bottom.pack(fill="x", padx=16, pady=10)
        if state["phones"]:
            tk.Button(bottom, text="▶ Test on Phone 1", font=FS,
                      bg=YELLOW, fg=BG, relief="flat", cursor="hand2",
                      command=self._test, padx=10, pady=6).pack(side="left")
        tk.Button(bottom, text="Done ✓",
                  font=("Segoe UI", 11, "bold"),
                  bg=GREEN, fg=BG, relief="flat",
                  cursor="hand2", command=self.destroy,
                  padx=16, pady=8).pack(side="right")

        self._refresh()

    def _refresh(self):
        self._lb.delete(0, "end")
        for i, s in enumerate(self.group["steps"], 1):
            self._lb.insert("end", f"  {i:>2}.  {describe_step(s)}")

    def _add(self):
        dlg = AddStepDialog(self)
        self.wait_window(dlg)
        if dlg.result:
            self.group["steps"].append(dlg.result)
            self._refresh()

    def _remove(self):
        sel = self._lb.curselection()
        if sel:
            del self.group["steps"][sel[0]]
            self._refresh()

    def _up(self):
        sel = self._lb.curselection()
        if not sel or sel[0] == 0: return
        i = sel[0]
        self.group["steps"][i-1], self.group["steps"][i] = \
            self.group["steps"][i], self.group["steps"][i-1]
        self._refresh(); self._lb.selection_set(i-1)

    def _dn(self):
        sel = self._lb.curselection()
        if not sel or sel[0] >= len(self.group["steps"]) - 1: return
        i = sel[0]
        self.group["steps"][i], self.group["steps"][i+1] = \
            self.group["steps"][i+1], self.group["steps"][i]
        self._refresh(); self._lb.selection_set(i+1)

    def _test(self):
        serial = state["phones"][0]["serial"]
        steps  = list(self.group["steps"])
        threading.Thread(
            target=lambda: execute_steps(steps, serial), daemon=True).start()


# ─── add step dialog ──────────────────────────────────────────────────────────

class AddStepDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.result = None
        self.title("Add a Step")
        self.config(bg=BG)
        self.geometry("500x440")
        self.resizable(False, False)
        self.grab_set()
        self.transient(parent)

        tk.Label(self, text="What should the phone do?",
                 font=("Segoe UI", 13, "bold"), bg=BG, fg=T1).pack(pady=(16, 8))

        type_frame = tk.Frame(self, bg=BG2, padx=12, pady=8)
        type_frame.pack(fill="x", padx=16)
        tk.Label(type_frame, text="Action:", font=FB,
                 bg=BG2, fg=T2, width=10, anchor="w").pack(side="left")
        self._type = tk.StringVar(value="open_url")
        combo = ttk.Combobox(type_frame, textvariable=self._type,
                             values=list(STEP_LABELS.keys()),
                             state="readonly", font=FB, width=28)
        combo.pack(side="left")
        combo.bind("<<ComboboxSelected>>", lambda _: self._update())

        self._desc = tk.Label(self, text="", font=FS, bg=BG, fg=T2,
                              wraplength=460, justify="left")
        self._desc.pack(padx=16, anchor="w", pady=4)

        self._fields_frame = tk.Frame(self, bg=BG2, padx=14, pady=10)
        self._fields_frame.pack(fill="x", padx=16)
        self._fields: dict[str, tk.StringVar] = {}
        self._update()

        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(pady=14)
        tk.Button(btn_row, text="  OK  ",
                  font=("Segoe UI", 11, "bold"),
                  bg=GREEN, fg=BG, relief="flat",
                  cursor="hand2", command=self._ok,
                  padx=16, pady=8).pack(side="left", padx=8)
        tk.Button(btn_row, text="Cancel", font=FB,
                  bg=BG3, fg=T2, relief="flat",
                  cursor="hand2", command=self.destroy,
                  padx=10, pady=6).pack(side="left")

    def _update(self):
        for w in self._fields_frame.winfo_children():
            w.destroy()
        self._fields.clear()
        t = self._type.get()
        self._desc.config(text=STEP_LABELS.get(t, ""))

        def field(label, key, default="", width=32):
            row = tk.Frame(self._fields_frame, bg=BG2)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=label, font=FB, bg=BG2, fg=T2,
                     width=18, anchor="w").pack(side="left")
            var = tk.StringVar(value=str(default))
            tk.Entry(row, textvariable=var, font=FM, bg=BG, fg=T1,
                     insertbackground=T1, relief="flat",
                     width=width).pack(side="left")
            self._fields[key] = var

        def tip(text):
            tk.Label(self._fields_frame, text=text, font=FS,
                     bg=BG2, fg=T3, justify="left").pack(anchor="w", pady=1)

        if t == "open_url":
            field("Website URL:", "url", "https://example.com", 36)
        elif t == "tap":
            field("X (left ↔ right):", "x", 540)
            field("Y (top ↕ bottom):", "y", 960)
            tip("Screen is 1080×2400. Center = 540, 1200")
        elif t == "wait":
            field("Seconds:", "seconds", 5)
        elif t == "swipe":
            field("Start X:", "x1", 540)
            field("Start Y:", "y1", 1600)
            field("End X:",   "x2", 540)
            field("End Y:",   "y2", 400)
            field("Speed ms:", "ms", 600)
            tip("Swipe up: high Y → low Y")
        elif t == "keyevent":
            field("Key:", "key", "BACK")
            tip("Options: BACK   HOME   ENTER   APP_SWITCH   VOLUME_UP")
        elif t == "close_app":
            field("Package:", "package", "com.android.chrome")
            tip("Chrome = com.android.chrome")
        elif t == "type_text":
            field("Text:", "text", "hello world")
        elif t == "rotate_identity":
            tip("Rotates Android ID. Use with Tor steps to also change IP.")

    def _ok(self):
        t    = self._type.get()
        step = {"type": t}
        for key, var in self._fields.items():
            v = var.get().strip()
            try:
                step[key] = int(v)
            except ValueError:
                step[key] = v
        self.result = step
        self.destroy()


# ─── page 7: launch & control ─────────────────────────────────────────────────

_running_groups: dict[str, bool] = {}


class LaunchPage(PageBase):
    def __init__(self, parent):
        super().__init__(parent)
        self._server_proc = None
        self.header(
            "Launch & Control 🚀",
            "Everything from here. No browser needed — the wizard is the control panel."
        )

        # Summary
        summary_outer = tk.Frame(self, bg=BG2, padx=12, pady=10)
        summary_outer.pack(fill="x", pady=(0, 10))
        tk.Label(summary_outer, text="Your groups:",
                 font=("Segoe UI", 10, "bold"), bg=BG2, fg=T1,
                 anchor="w").pack(fill="x")
        self._summary = tk.Text(summary_outer, height=4, font=FM, bg=BG2, fg=T1,
                                relief="flat", state="disabled", wrap="word")
        self._summary.pack(fill="x")

        # Server
        srv = tk.Frame(self, bg=BG3, padx=14, pady=12)
        srv.pack(fill="x", pady=(0, 8))
        tk.Label(srv, text="A — Start the CPharm Server",
                 font=("Segoe UI", 11, "bold"), bg=BG3,
                 fg=ACCENT, anchor="w").pack(fill="x")
        tk.Label(srv,
                 text="Starts the background automation server. Keep this running while groups are active.",
                 font=FS, bg=BG3, fg=T2, anchor="w", wraplength=640).pack(
                     fill="x", pady=(2, 8))
        srv_row = tk.Frame(srv, bg=BG3)
        srv_row.pack(fill="x")
        self._btn_srv_start = tk.Button(srv_row, text="▶  Start Server",
                                        font=("Segoe UI", 10, "bold"),
                                        bg=GREEN, fg=BG, relief="flat",
                                        cursor="hand2", padx=14, pady=7,
                                        command=self._start_server)
        self._btn_srv_start.pack(side="left", padx=(0, 8))
        self._btn_srv_stop = tk.Button(srv_row, text="■  Stop Server",
                                       font=("Segoe UI", 10, "bold"),
                                       bg=RED, fg=BG, relief="flat",
                                       cursor="hand2", padx=14, pady=7,
                                       state="disabled",
                                       command=self._stop_server)
        self._btn_srv_stop.pack(side="left")
        self._srv_lbl = tk.Label(srv_row, text="Server not running",
                                  font=FS, bg=BG3, fg=T2)
        self._srv_lbl.pack(side="left", padx=10)

        # Groups
        grp = tk.Frame(self, bg=BG3, padx=14, pady=12)
        grp.pack(fill="x", pady=(0, 8))
        tk.Label(grp, text="B — Run Groups",
                 font=("Segoe UI", 11, "bold"), bg=BG3,
                 fg=GREEN, anchor="w").pack(fill="x")
        tk.Label(grp,
                 text="Starts all groups at the same time. Each group runs on its assigned phones.",
                 font=FS, bg=BG3, fg=T2, anchor="w").pack(fill="x", pady=(2, 8))
        grp_row = tk.Frame(grp, bg=BG3)
        grp_row.pack(fill="x")
        self._btn_run = tk.Button(grp_row, text="▶  Run All Groups",
                                  font=("Segoe UI", 10, "bold"),
                                  bg=GREEN, fg=BG, relief="flat",
                                  cursor="hand2", padx=14, pady=7,
                                  state="disabled",
                                  command=self._run_groups)
        self._btn_run.pack(side="left", padx=(0, 8))
        self._btn_stop_grp = tk.Button(grp_row, text="■  Stop All Groups",
                                       font=("Segoe UI", 10, "bold"),
                                       bg=RED, fg=BG, relief="flat",
                                       cursor="hand2", padx=14, pady=7,
                                       state="disabled",
                                       command=self._stop_groups)
        self._btn_stop_grp.pack(side="left")
        self._run_lbl = tk.Label(grp_row, text="", font=FS, bg=BG3, fg=T2)
        self._run_lbl.pack(side="left", padx=10)

        # Log
        tk.Label(self, text="Live log:", font=("Segoe UI", 9, "bold"),
                 bg=BG, fg=T2, anchor="w").pack(fill="x", pady=(4, 2))
        log_fr = tk.Frame(self, bg=BG2)
        log_fr.pack(fill="both", expand=True)
        self._log_box = tk.Text(log_fr, height=7, font=FM, bg=BG2, fg=T1,
                                relief="flat", state="disabled", wrap="word")
        log_sb = tk.Scrollbar(log_fr, orient="vertical",
                              command=self._log_box.yview)
        self._log_box.configure(yscrollcommand=log_sb.set)
        self._log_box.pack(side="left", fill="both", expand=True)
        log_sb.pack(side="right", fill="y")

        misc = tk.Frame(self, bg=BG)
        misc.pack(fill="x", pady=6)
        tk.Button(misc, text="Open Dashboard in Browser",
                  font=FS, bg=BG3, fg=T1, relief="flat", cursor="hand2",
                  padx=10, pady=5,
                  command=lambda: webbrowser.open(
                      f"http://localhost:{DASHBOARD_PORT}")).pack(side="left", padx=(0, 8))
        tk.Button(misc, text="💾 Save Config",
                  font=FS, bg=BG3, fg=T1, relief="flat", cursor="hand2",
                  padx=10, pady=5, command=self._save).pack(side="left")

    def on_enter(self):
        self._refresh_summary()
        self._save()

    def _refresh_summary(self):
        lines = []
        for i, g in enumerate(state["groups"], 1):
            names = []
            for s in g["phones"]:
                m = next((p for p in state["phones"] if p["serial"] == s), None)
                names.append(m["name"] if m else s)
            rep = "forever" if g["repeat_forever"] else f"{g['repeat']}×"
            lines.append(
                f"  {g['name']}  |  {len(names)} phone(s)  |  "
                f"{len(g['steps'])} steps  |  stagger {g['stagger_secs']}s  |  {rep}")
        self._summary.config(state="normal")
        self._summary.delete("1.0", "end")
        self._summary.insert("end", "\n".join(lines) or "  (no groups)")
        self._summary.config(state="disabled")

    def _log(self, text):
        self._log_box.config(state="normal")
        self._log_box.insert("end", text + "\n")
        self._log_box.see("end")
        self._log_box.config(state="disabled")

    def _save(self):
        d = state.get("cpharm_dir", "")
        if not d:
            return None
        rec = Path(d) / "automation" / "recordings"
        rec.mkdir(parents=True, exist_ok=True)
        out = rec / "groups_config.json"
        out.write_text(json.dumps({"groups": state["groups"]}, indent=2))
        return str(out)

    def _start_server(self):
        d = state.get("cpharm_dir", "")
        if not d:
            d = str(Path(__file__).parent.parent)
            state["cpharm_dir"] = d

        self._save()
        dashboard = Path(d) / "automation" / "dashboard.py"
        if not dashboard.exists():
            messagebox.showerror("Not found",
                                 f"dashboard.py not found at:\n{dashboard}\n\n"
                                 "Make sure CPharm is cloned correctly.")
            return

        try:
            flags = subprocess.CREATE_NEW_CONSOLE if IS_WIN else 0
            self._server_proc = subprocess.Popen(
                [state.get("python_cmd", "python"), str(dashboard)],
                cwd=str(dashboard.parent),
                creationflags=flags,
            )
            self._log("Server starting…")
            self._btn_srv_start.config(state="disabled")
            self._btn_srv_stop.config(state="normal")
            self._srv_lbl.config(text="⏳ Starting…", fg=YELLOW)

            def check():
                time.sleep(3)
                if self._server_proc.poll() is None:
                    self._srv_lbl.config(text="✅ Server running", fg=GREEN)
                    self._btn_run.config(state="normal")
                    self._log("Server is up! Click Run All Groups to start.")
                else:
                    self._srv_lbl.config(text="❌ Crashed — check terminal", fg=RED)
                    self._btn_srv_start.config(state="normal")
                    self._btn_srv_stop.config(state="disabled")

            threading.Thread(target=check, daemon=True).start()
        except Exception as e:
            messagebox.showerror("Failed", str(e))

    def _stop_server(self):
        self._stop_groups()
        if self._server_proc:
            self._server_proc.terminate()
            self._server_proc = None
        self._srv_lbl.config(text="Stopped", fg=T2)
        self._btn_srv_start.config(state="normal")
        self._btn_srv_stop.config(state="disabled")
        self._btn_run.config(state="disabled")
        self._log("Server stopped.")

    def _api(self, path, body):
        import urllib.request
        url  = f"http://localhost:{DASHBOARD_PORT}{path}"
        data = json.dumps(body).encode()
        req  = urllib.request.Request(url, data=data,
                                      headers={"Content-Type": "application/json"},
                                      method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read())
        except Exception as e:
            return {"error": str(e)}

    def _run_groups(self):
        self._log("Starting all groups…")
        self._btn_run.config(state="disabled")
        self._btn_stop_grp.config(state="normal")

        def go():
            result = self._api("/api/groups/run", {"groups": state["groups"]})
            if "error" in result:
                self._log(f"❌  {result['error']}")
                self._btn_run.config(state="normal")
                self._btn_stop_grp.config(state="disabled")
            else:
                n = result.get("groups", len(state["groups"]))
                self._log(f"✅  {n} group(s) running in parallel!")
                self._run_lbl.config(text=f"{n} running", fg=GREEN)

        threading.Thread(target=go, daemon=True).start()

    def _stop_groups(self):
        def go():
            self._api("/api/groups/stop", {})
            self._log("All groups stopped.")
            self._run_lbl.config(text="", fg=T2)
            self._btn_run.config(state="normal")
            self._btn_stop_grp.config(state="disabled")

        threading.Thread(target=go, daemon=True).start()


# ─── main wizard ──────────────────────────────────────────────────────────────

class CPharmWizard(tk.Tk):
    PAGES = [
        WelcomePage,
        AndroidStudioPage,
        PhoneFarmPage,
        BootPage,
        PlayStorePage,
        GroupsPage,
        LaunchPage,
    ]
    PAGE_NAMES = [
        "Welcome",
        "Android Studio",
        "Create Phones",
        "Start Phones",
        "Play Store",
        "Groups",
        "Launch!",
    ]

    def __init__(self):
        super().__init__()
        self.title("CPharm Phone Farm Setup")
        self.geometry("760x700")
        self.minsize(720, 600)
        self.config(bg=BG)
        self.resizable(True, True)

        self._build_header()
        self._content = tk.Frame(self, bg=BG)
        self._content.pack(fill="both", expand=True, padx=24, pady=14)
        self._build_footer()

        self._pages = [P(self._content) for P in self.PAGES]
        for p in self._pages:
            p.place(relwidth=1, relheight=1)

        self._current = 0
        self._show(0)

        # Try to auto-detect CPharm directory
        guesses = [
            Path(__file__).parent.parent,
            Path.home() / "CPharm",
            Path("C:/CPharm"),
        ]
        for g in guesses:
            if (g / "automation" / "dashboard.py").exists():
                state["cpharm_dir"] = str(g)
                break

    def _build_header(self):
        hdr = tk.Frame(self, bg=BG2, height=58)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="CPharm Phone Farm",
                 font=("Segoe UI", 13, "bold"),
                 bg=BG2, fg=ACCENT).pack(side="left", padx=16, pady=10)
        dot_row = tk.Frame(hdr, bg=BG2)
        dot_row.pack(side="right", padx=16)
        self._dots = []
        for i in range(len(self.PAGES)):
            d = tk.Label(dot_row, text="●", font=("Segoe UI", 9),
                         bg=BG2, fg=T3)
            d.pack(side="left", padx=3)
            self._dots.append(d)
        self._step_lbl = tk.Label(hdr, text="", font=FS, bg=BG2, fg=T2)
        self._step_lbl.pack(side="right", padx=(0, 10))

    def _build_footer(self):
        ftr = tk.Frame(self, bg=BG2, height=56)
        ftr.pack(fill="x")
        ftr.pack_propagate(False)
        self._next_btn = tk.Button(ftr, text="Next  →",
                                   font=("Segoe UI", 11, "bold"),
                                   bg=ACCENT, fg=BG, relief="flat",
                                   cursor="hand2", command=self._next,
                                   padx=20, pady=8)
        self._next_btn.pack(side="right", padx=16, pady=8)
        self._back_btn = tk.Button(ftr, text="← Back",
                                   font=("Segoe UI", 11),
                                   bg=BG3, fg=T2, relief="flat",
                                   cursor="hand2", command=self._back,
                                   padx=16, pady=8)
        self._back_btn.pack(side="right", padx=(0, 8), pady=8)

    def _show(self, idx):
        for i, p in enumerate(self._pages):
            if i == idx: p.lift()
            self._dots[i].config(
                fg=ACCENT if i == idx else (T2 if i < idx else T3))
        total = len(self.PAGES)
        self._step_lbl.config(
            text=f"Step {idx + 1} of {total}  —  {self.PAGE_NAMES[idx]}")
        self._back_btn.config(state="normal" if idx > 0 else "disabled")
        self._next_btn.config(
            text="Finish ✓" if idx == total - 1 else "Next  →")
        self._pages[idx].on_enter()

    def _next(self):
        if self._pages[self._current].can_advance():
            if self._current < len(self._pages) - 1:
                self._current += 1
                self._show(self._current)

    def _back(self):
        if self._current > 0:
            self._current -= 1
            self._show(self._current)


if __name__ == "__main__":
    app = CPharmWizard()
    app.mainloop()
