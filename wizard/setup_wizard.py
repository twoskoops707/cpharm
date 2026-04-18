"""
CPharm Setup Wizard
Run this on Windows. It installs everything, lets you split phones into
groups, build a different sequence for each group, then run them all at once.

Build to .exe (run build.bat on Windows):
    pip install pyinstaller pillow
    pyinstaller --onefile --windowed --name CPharmSetup setup_wizard.py
"""

import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path
from urllib.request import urlretrieve
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
T1     = "#e6edf3"
T2     = "#8b949e"
T3     = "#6e7681"

FONT_HEAD = ("Segoe UI", 20, "bold")
FONT_SUB  = ("Segoe UI", 10)
FONT_BODY = ("Segoe UI", 11)
FONT_MONO = ("Consolas", 10)
FONT_BIG  = ("Segoe UI", 13)

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
    "wait":            "Wait (pause for N seconds)",
    "swipe":           "Swipe up or down",
    "keyevent":        "Press a button (Back / Home / Enter)",
    "close_app":       "Close the app / browser",
    "clear_cookies":   "Clear browser cookies",
    "rotate_identity": "Change IP + identity (needs Tor setup)",
    "type_text":       "Type some text",
}

state = {
    "cpharm_dir":  "",
    "python_cmd":  "python",
    "phones":      [],
    "groups": [
        {
            "name":           "Group 1",
            "phones":         [],
            "steps":          [],
            "stagger_secs":   60,
            "repeat":         1,
            "repeat_forever": False,
        }
    ],
}


def run_cmd(cmd, cwd=None, timeout=120):
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=cwd, timeout=timeout,
            shell=isinstance(cmd, str)
        )
        return r.returncode == 0, r.stdout + r.stderr
    except Exception as e:
        return False, str(e)


def adb(*args, serial=None, timeout=15):
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


def describe_step(step):
    t = step.get("type", "")
    icon = STEP_ICONS.get(t, "•")
    if t == "open_url":
        return f"{icon}  Open  → {step.get('url', '')}"
    if t == "tap":
        return f"{icon}  Tap   → ({step.get('x', 0)}, {step.get('y', 0)})"
    if t == "wait":
        return f"{icon}  Wait  → {step.get('seconds', 1)} seconds"
    if t == "swipe":
        return f"{icon}  Swipe → ({step.get('x1',0)},{step.get('y1',0)}) to ({step.get('x2',0)},{step.get('y2',0)})"
    if t == "keyevent":
        return f"{icon}  Key   → {step.get('key', 'BACK')}"
    if t == "close_app":
        return f"{icon}  Close → {step.get('package', 'Chrome')}"
    if t == "clear_cookies":
        return f"{icon}  Clear cookies"
    if t == "rotate_identity":
        return f"{icon}  Rotate IP + identity"
    if t == "type_text":
        return f"{icon}  Type  → \"{step.get('text', '')}\""
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
            time.sleep(int(step.get("seconds", 1)))
        elif t == "swipe":
            adb("shell", "input", "swipe",
                str(step.get("x1", 0)), str(step.get("y1", 0)),
                str(step.get("x2", 0)), str(step.get("y2", 0)),
                str(step.get("ms", 400)), serial=serial)
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
        time.sleep(0.3)


class PageBase(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=BG)
        self.app = parent

    def on_enter(self): pass
    def can_advance(self): return True

    def header(self, title, subtitle=""):
        tk.Label(self, text=title, font=FONT_HEAD, bg=BG, fg=T1,
                 justify="left", anchor="w").pack(fill="x", pady=(0, 4))
        if subtitle:
            tk.Label(self, text=subtitle, font=FONT_SUB, bg=BG, fg=T2,
                     justify="left", anchor="w", wraplength=640).pack(fill="x", pady=(0, 14))

    def btn(self, parent, text, cmd, color=ACCENT, width=None, side="left"):
        kw = dict(text=text, command=cmd, bg=color, fg=BG if color not in (BG3, T3) else T1,
                  font=("Segoe UI", 10, "bold"), relief="flat",
                  cursor="hand2", pady=7, padx=14, bd=0)
        if width:
            kw["width"] = width
        b = tk.Button(parent, **kw)
        b.pack(side=side, padx=(0, 8))
        return b


class WelcomePage(PageBase):
    def __init__(self, parent):
        super().__init__(parent)
        tk.Label(self, text="👋", font=("Segoe UI", 48), bg=BG).pack(pady=(24, 8))
        tk.Label(self, text="Welcome to CPharm Setup",
                 font=("Segoe UI", 22, "bold"), bg=BG, fg=T1).pack()
        tk.Label(self,
                 text="This wizard will get everything installed on your Windows PC\n"
                      "and help you run different tasks on different groups of phones at the same time.\n\n"
                      "You do NOT need to know anything about computers.\n"
                      "Just click Next and follow along.",
                 font=FONT_BIG, bg=BG, fg=T2, justify="center").pack(pady=16)

        box = tk.Frame(self, bg=BG3, padx=20, pady=14)
        box.pack(padx=40, fill="x")
        tk.Label(box, text="Before you start, make sure you have:",
                 font=("Segoe UI", 10, "bold"), bg=BG3, fg=YELLOW, anchor="w").pack(fill="x")
        for item in [
            "✅  A Windows 10 or 11 computer",
            "✅  An internet connection",
            "✅  At least one Android phone plugged in or on WiFi",
            "✅  About 10 minutes of free time",
        ]:
            tk.Label(box, text=item, font=FONT_BODY, bg=BG3, fg=T1,
                     anchor="w").pack(fill="x", pady=2)


class SoftwarePage(PageBase):
    def __init__(self, parent):
        super().__init__(parent)
        self.header(
            "Step 1 — Install the Software",
            "These three free programs are required. Click the button next to anything "
            "showing ❌ to download it, then click Check Again."
        )

        terminal_box = tk.Frame(self, bg=BG3, padx=14, pady=12)
        terminal_box.pack(fill="x", pady=(0, 12))
        tk.Label(terminal_box,
                 text="💡  HOW TO OPEN A TERMINAL (you'll need this a lot):\n\n"
                      "   1.  Press the  Windows  key on your keyboard\n"
                      "   2.  Type:  cmd\n"
                      "   3.  Press  Enter\n\n"
                      "   A black window opens. That is the Terminal.\n"
                      "   You type commands in there and press Enter to run them.",
                 font=FONT_BODY, bg=BG3, fg=T2, justify="left", anchor="w").pack(fill="x")

        self._rows = {}
        items = [
            ("python",
             "Python 3.11+",
             "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe",
             "HOW TO INSTALL PYTHON:\n\n"
             "1. Click 'Open Download Page'\n"
             "2. Download the file (python-3.11.9-amd64.exe)\n"
             "3. Double-click the downloaded file to run it\n"
             "4. VERY IMPORTANT: Before you click anything else, look for a checkbox\n"
             "   at the bottom that says 'Add Python to PATH' — TICK THAT BOX\n"
             "5. Click 'Install Now'\n"
             "6. Wait for it to finish\n"
             "7. Come back here and click 'Check Again'"),
            ("git",
             "Git for Windows",
             "https://git-scm.com/download/win",
             "HOW TO INSTALL GIT:\n\n"
             "1. Click 'Open Download Page'\n"
             "2. Download the installer (Git-X.X.X-64-bit.exe)\n"
             "3. Double-click it\n"
             "4. Click Next on EVERY screen — all the defaults are fine\n"
             "5. Click Finish when done\n"
             "6. Come back here and click 'Check Again'"),
            ("adb",
             "ADB (Android Debug Bridge)",
             "https://dl.google.com/android/repository/platform-tools-latest-windows.zip",
             "HOW TO INSTALL ADB:\n\n"
             "1. Click 'Open Download Page' — it downloads a .zip file automatically\n"
             "2. Open your Downloads folder\n"
             "3. Right-click the zip file → Extract All → Extract\n"
             "4. Move the 'platform-tools' folder somewhere easy, like  C:\\platform-tools\n"
             "5. Now add it to Windows PATH:\n"
             "   a. Press Windows key, type: environment variables, press Enter\n"
             "   b. Click 'Environment Variables'\n"
             "   c. Under 'System variables', click 'Path', then 'Edit'\n"
             "   d. Click 'New'\n"
             "   e. Type:  C:\\platform-tools  (or wherever you put the folder)\n"
             "   f. Click OK on all windows\n"
             "6. Close and reopen Terminal\n"
             "7. Come back here and click 'Check Again'"),
        ]

        for key, label, url, tip in items:
            row = tk.Frame(self, bg=BG2, pady=4, padx=12)
            row.pack(fill="x", pady=3)
            status = tk.Label(row, text="…", font=FONT_MONO, bg=BG2, fg=T2, width=3)
            status.pack(side="left")
            tk.Label(row, text=label, font=FONT_BODY, bg=BG2, fg=T1,
                     width=24, anchor="w").pack(side="left")
            tk.Button(row, text="Open Download Page", font=FONT_SUB,
                      bg=ACCENT, fg=BG, relief="flat", cursor="hand2", padx=10,
                      command=lambda u=url: webbrowser.open(u)).pack(side="left", padx=6)
            tk.Button(row, text="How to install?", font=FONT_SUB,
                      bg=BG3, fg=T2, relief="flat", cursor="hand2", padx=8,
                      command=lambda t=tip, l=label: messagebox.showinfo(
                          f"How to install {l}", t)).pack(side="left")
            self._rows[key] = status

        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(fill="x", pady=10)
        self.btn(btn_row, "🔄  Check Again", self._check)
        self._result_lbl = tk.Label(btn_row, text="", font=FONT_SUB, bg=BG, fg=T2)
        self._result_lbl.pack(side="left", padx=10)

    def on_enter(self):
        self._check()

    def _has_python(self):
        for cmd in ["python", "python3", "py"]:
            ok, out = run_cmd([cmd, "--version"])
            if ok and "Python 3" in out:
                state["python_cmd"] = cmd
                return True
        return False

    def _has_cmd(self, *args):
        return run_cmd(list(args))[0]

    def _check(self):
        results = {
            "python": self._has_python(),
            "git":    self._has_cmd("git", "--version"),
            "adb":    self._has_cmd("adb", "version"),
        }
        all_ok = True
        for key, ok in results.items():
            self._rows[key].config(text="✅" if ok else "❌", fg=GREEN if ok else RED)
            if not ok:
                all_ok = False
        if all_ok:
            self._result_lbl.config(text="All three installed! Click Next →", fg=GREEN)
        else:
            self._result_lbl.config(
                text="Install anything showing ❌, then click Check Again", fg=YELLOW)
        return all_ok

    def can_advance(self):
        if not self._check():
            messagebox.showerror(
                "Missing software",
                "Please install everything showing ❌ before moving on.\n\n"
                "Click 'How to install?' next to each one for step-by-step instructions.\n\n"
                "After installing, close and reopen the black Terminal window, "
                "then click Check Again."
            )
            return False
        return True


class RepoPage(PageBase):
    def __init__(self, parent):
        super().__init__(parent)
        self.header(
            "Step 2 — Get CPharm",
            "Download the CPharm files to your computer. You only do this once."
        )

        box = tk.Frame(self, bg=BG3, padx=16, pady=16)
        box.pack(fill="x", pady=(0, 14))
        tk.Label(box,
                 text="📋  DO THIS:\n\n"
                      "1.  Open Terminal\n"
                      "    (Windows key → type  cmd  → Enter)\n\n"
                      "2.  Copy this line below and paste it into the Terminal (Ctrl+V), then press Enter:\n",
                 font=FONT_BODY, bg=BG3, fg=T2, justify="left", anchor="w").pack(fill="x")

        cmd_frame = tk.Frame(box, bg="#000", padx=12, pady=10)
        cmd_frame.pack(fill="x")
        cmd_lbl = tk.Label(cmd_frame,
                           text=f"git clone {REPO_URL}  C:\\CPharm",
                           font=FONT_MONO, bg="#000", fg=GREEN, anchor="w")
        cmd_lbl.pack(fill="x")

        tk.Label(box,
                 text="\n3.  Wait for it to finish. You'll see a bunch of text scroll by.\n"
                      "    When it stops and you see a new line starting with  C:\\>,  it's done.\n\n"
                      "4.  A folder called  C:\\CPharm  now exists on your computer.\n\n"
                      "5.  Click the button below to tell me where you put it:",
                 font=FONT_BODY, bg=BG3, fg=T2, justify="left", anchor="w").pack(fill="x")

        sep = tk.Frame(box, bg=BORDER, height=1)
        sep.pack(fill="x", pady=10)
        tk.Label(box,
                 text="Already downloaded CPharm? Click below to find the folder instead.",
                 font=FONT_SUB, bg=BG3, fg=T3).pack(anchor="w")

        folder_row = tk.Frame(self, bg=BG)
        folder_row.pack(fill="x", pady=10)
        self.btn(folder_row, "📂  Select the CPharm Folder", self._pick)
        self._lbl = tk.Label(folder_row, text="No folder selected yet",
                             font=FONT_MONO, bg=BG, fg=T2)
        self._lbl.pack(side="left", padx=10)

    def on_enter(self):
        if Path("C:/CPharm/automation/dashboard.py").exists():
            state["cpharm_dir"] = "C:/CPharm"
            self._lbl.config(text="C:/CPharm  ✅", fg=GREEN)

    def _pick(self):
        d = filedialog.askdirectory(title="Select the CPharm folder", initialdir="C:/")
        if not d:
            return
        if not Path(d).joinpath("automation", "dashboard.py").exists():
            messagebox.showerror(
                "Wrong folder",
                "That doesn't look like the CPharm folder.\n"
                "The correct folder contains a sub-folder called 'automation'.\n\n"
                "Try selecting the folder named CPharm or cpharm."
            )
            return
        state["cpharm_dir"] = d
        self._lbl.config(text=f"{d}  ✅", fg=GREEN)

    def can_advance(self):
        if not state["cpharm_dir"]:
            messagebox.showerror(
                "No folder selected",
                "Please select the CPharm folder.\n\n"
                "If you haven't downloaded it yet:\n"
                "1. Open Terminal\n"
                f"2. Type:  git clone {REPO_URL} C:\\CPharm\n"
                "3. Press Enter and wait\n"
                "4. Click 'Select the CPharm Folder' above"
            )
            return False
        return True


class DepsPage(PageBase):
    def __init__(self, parent):
        super().__init__(parent)
        self.header(
            "Step 3 — Install Python Packages",
            "CPharm needs two small helper packages. Click the button and wait about 30 seconds."
        )

        box = tk.Frame(self, bg=BG3, padx=14, pady=12)
        box.pack(fill="x", pady=(0, 14))
        tk.Label(box,
                 text="What's about to happen:\n\n"
                      "Python will download two helper files called  websockets  and  psutil.\n"
                      "They're free, safe, and tiny. You need them for the dashboard to work.\n"
                      "This is the computer version of 'installing an app'.",
                 font=FONT_BODY, bg=BG3, fg=T2, justify="left", anchor="w").pack(fill="x")

        self.btn(self, "  ▶  Install Now  ", self._install, color=GREEN, width=20)

        self._out = tk.Text(self, height=9, font=FONT_MONO, bg=BG2, fg=T1,
                            insertbackground=T1, relief="flat", state="disabled", wrap="word")
        self._out.pack(fill="x", pady=12)
        self._done = False

    def _install(self):
        d = state.get("cpharm_dir", "")
        if not d:
            messagebox.showerror("Error", "Go back and select the CPharm folder first.")
            return
        req  = Path(d) / "requirements.txt"
        cmd  = [state["python_cmd"], "-m", "pip", "install", "-r", str(req), "--upgrade"]

        def run():
            self._log("Installing packages — please wait…\n\n")
            ok, out = run_cmd(cmd, timeout=180)
            self._log(out)
            if ok:
                self._log("\n✅  Done! Click Next →\n")
                self._done = True
            else:
                self._log(
                    "\n❌  Something went wrong.\n\n"
                    "Common fixes:\n"
                    "• No internet? Connect and try again.\n"
                    "• Python not found? Reinstall Python with the 'Add to PATH' checkbox ticked.\n"
                )

        threading.Thread(target=run, daemon=True).start()

    def _log(self, text):
        self._out.config(state="normal")
        self._out.insert("end", text)
        self._out.see("end")
        self._out.config(state="disabled")

    def can_advance(self):
        if not self._done:
            messagebox.showinfo(
                "Not done yet",
                "Click 'Install Now' and wait for it to finish, then click Next."
            )
            return False
        return True


class PhonesPage(PageBase):
    def __init__(self, parent):
        super().__init__(parent)
        self.header(
            "Step 4 — Connect Your Phones",
            "Plug in your phones with a USB cable, or connect them over WiFi. Then click Scan."
        )

        info = tk.Frame(self, bg=BG3, padx=14, pady=14)
        info.pack(fill="x", pady=(0, 14))
        tk.Label(info,
                 text="📱  USB PHONE:\n"
                      "   1. Plug the phone into your computer with a USB cable\n"
                      "   2. On the phone: Settings → Developer Options → turn on USB Debugging\n"
                      "      (If you don't see Developer Options: Settings → About Phone → tap\n"
                      "       'Build Number' 7 times fast. A message says 'You are a developer!')\n"
                      "   3. A popup appears on the phone asking to 'Allow USB Debugging' → tap Allow\n\n"
                      "📡  EMULATOR or WIFI PHONE:\n"
                      "   Type the IP address and port in the box below and click Connect.\n"
                      "   BlueStacks: 127.0.0.1:5555   |   Waydroid: 192.168.250.1:5555   |   MEmu: 127.0.0.1:21503",
                 font=FONT_BODY, bg=BG3, fg=T2, justify="left", anchor="w").pack(fill="x")

        ctrl = tk.Frame(self, bg=BG)
        ctrl.pack(fill="x", pady=6)
        self.btn(ctrl, "🔍  Scan for Phones", self._scan)
        tk.Label(ctrl, text="  WiFi address:", font=FONT_SUB, bg=BG, fg=T2).pack(side="left")
        self._ip = tk.StringVar(value="127.0.0.1:5555")
        tk.Entry(ctrl, textvariable=self._ip, font=FONT_MONO, bg=BG2, fg=T1,
                 insertbackground=T1, relief="flat", width=22).pack(side="left", padx=4)
        self.btn(ctrl, "Connect", self._connect)

        self._list = tk.Listbox(self, font=FONT_MONO, bg=BG2, fg=T1,
                                selectbackground=ACCENT, relief="flat",
                                height=6, activestyle="none")
        self._list.pack(fill="x", pady=8)

        self._status = tk.Label(self, text="Click Scan to find phones.",
                                font=FONT_SUB, bg=BG, fg=T2)
        self._status.pack(anchor="w")

    def on_enter(self):
        self._scan()

    def _scan(self):
        self._list.delete(0, "end")
        devs = list_adb_devices()
        state["phones"] = devs
        if devs:
            for d in devs:
                self._list.insert("end", f"  {d['name']}   ({d['serial']})")
            self._status.config(
                text=f"Found {len(devs)} phone(s). You can split them into groups on the next page.",
                fg=GREEN)
        else:
            self._status.config(
                text="No phones found. Check USB Debugging is on, then click Scan again.",
                fg=YELLOW)

    def _connect(self):
        addr = self._ip.get().strip()
        if not re.match(r"^[a-zA-Z0-9._-]+:\d{2,5}$", addr):
            messagebox.showerror("Bad address", "Enter an address like  127.0.0.1:5555")
            return
        adb("connect", addr, timeout=10)
        time.sleep(1)
        self._scan()

    def can_advance(self):
        if not state["phones"]:
            messagebox.showwarning(
                "No phones",
                "No phones were found. You need at least one.\n\n"
                "Try:\n"
                "• USB: Make sure USB Debugging is ON in Developer Options\n"
                "• Emulator: Open BlueStacks first, then click Scan\n"
                "• WiFi: Type the IP:port and click Connect"
            )
            return False
        return True


class GroupsPage(PageBase):
    """
    Split phones into groups. Each group runs a different sequence at the same time.
    Example: 5 phones browse a website, 5 phones test the app, 5 phones leave reviews.
    """
    def __init__(self, parent):
        super().__init__(parent)
        self.header(
            "Step 5 — Groups & Sequences",
            "Split your phones into groups. Each group does something different at the same time.\n"
            "Example: Group 1 = browse website · Group 2 = test the app · Group 3 = leave reviews"
        )

        ctrl = tk.Frame(self, bg=BG)
        ctrl.pack(fill="x", pady=(0, 8))
        self.btn(ctrl, "+ Add a New Group", self._add_group, color=GREEN)
        self._count_lbl = tk.Label(ctrl, text="", font=FONT_SUB, bg=BG, fg=T2)
        self._count_lbl.pack(side="left", padx=10)

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
                         lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>",
                          lambda e: self._canvas.itemconfig(self._win_id, width=e.width))

        self._cards: list[tk.Frame] = []

    def on_enter(self):
        if not state["phones"]:
            return
        for g in state["groups"]:
            if not g["phones"]:
                g["phones"] = [p["serial"] for p in state["phones"]]
        self._rebuild()

    def _add_group(self):
        n = len(state["groups"]) + 1
        state["groups"].append({
            "name": f"Group {n}", "phones": [], "steps": [],
            "stagger_secs": 60, "repeat": 1, "repeat_forever": False,
        })
        self._rebuild()

    def _remove_group(self, idx):
        if len(state["groups"]) <= 1:
            messagebox.showinfo("Can't remove", "You need at least one group.")
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
            text=f"{n} group{'s' if n != 1 else ''} — all run at the same time")

    def _build_card(self, idx, group):
        card = tk.Frame(self._inner, bg=BG2, padx=14, pady=12,
                        highlightthickness=1, highlightbackground=BORDER)

        hdr = tk.Frame(card, bg=BG2)
        hdr.pack(fill="x")

        color_strip = tk.Frame(hdr, bg=[ACCENT, GREEN, YELLOW, RED, T2][idx % 5], width=4)
        color_strip.pack(side="left", fill="y", padx=(0, 10))

        name_var = tk.StringVar(value=group["name"])
        name_e = tk.Entry(hdr, textvariable=name_var, font=("Segoe UI", 13, "bold"),
                          bg=BG3, fg=T1, relief="flat", width=18, insertbackground=T1)
        name_e.pack(side="left")
        name_var.trace("w", lambda *_: group.update({"name": name_var.get()}))

        if len(state["groups"]) > 1:
            tk.Button(hdr, text="✕ Remove group", font=FONT_SUB,
                      bg=RED, fg=BG, relief="flat", cursor="hand2", padx=8, pady=3,
                      command=lambda i=idx: self._remove_group(i)).pack(side="right")

        tk.Frame(card, bg=BORDER, height=1).pack(fill="x", pady=8)

        tk.Label(card, text="Which phones are in this group?",
                 font=("Segoe UI", 10, "bold"), bg=BG2, fg=T1, anchor="w").pack(fill="x")
        tk.Label(card, text="Tick the phones you want this group to control.",
                 font=FONT_SUB, bg=BG2, fg=T2, anchor="w").pack(fill="x", pady=(2, 6))

        phones_frame = tk.Frame(card, bg=BG2)
        phones_frame.pack(fill="x")

        if not state["phones"]:
            tk.Label(phones_frame, text="Go back to Step 4 and connect phones first.",
                     font=FONT_SUB, bg=BG2, fg=RED).pack(anchor="w")
        else:
            for phone in state["phones"]:
                var = tk.BooleanVar(value=phone["serial"] in group["phones"])

                def toggle(v=var, s=phone["serial"], g=group):
                    if v.get():
                        if s not in g["phones"]:
                            g["phones"].append(s)
                    else:
                        g["phones"] = [x for x in g["phones"] if x != s]

                tk.Checkbutton(phones_frame,
                               text=f"  {phone['name']}   ({phone['serial']})",
                               variable=var, onvalue=True, offvalue=False,
                               command=toggle, font=FONT_BODY,
                               bg=BG2, fg=T1, selectcolor=BG3,
                               activebackground=BG2).pack(anchor="w")

        tk.Frame(card, bg=BORDER, height=1).pack(fill="x", pady=8)

        seq_row = tk.Frame(card, bg=BG2)
        seq_row.pack(fill="x")
        tk.Label(seq_row, text="What should these phones do?",
                 font=("Segoe UI", 10, "bold"), bg=BG2, fg=T1, anchor="w").pack(fill="x")
        tk.Label(seq_row, text="A sequence is a list of steps: open website, tap, wait, close, etc.",
                 font=FONT_SUB, bg=BG2, fg=T2, anchor="w").pack(fill="x", pady=(2, 6))

        seq_ctrl = tk.Frame(card, bg=BG2)
        seq_ctrl.pack(fill="x")

        step_lbl = tk.Label(seq_ctrl,
                            text=f"{len(group['steps'])} steps in this sequence",
                            font=FONT_BODY, bg=BG2,
                            fg=GREEN if group["steps"] else YELLOW)
        step_lbl.pack(side="left", padx=(0, 10))

        def edit_seq(i=idx, lbl=step_lbl):
            win = SequenceEditorWindow(self.app, state["groups"][i])
            self.app.wait_window(win)
            count = len(state["groups"][i]["steps"])
            lbl.config(text=f"{count} steps in this sequence",
                       fg=GREEN if count else YELLOW)

        tk.Button(seq_ctrl, text="✏  Edit Sequence", font=FONT_SUB,
                  bg=ACCENT, fg=BG, relief="flat", cursor="hand2", padx=10, pady=4,
                  command=edit_seq).pack(side="left")

        tk.Frame(card, bg=BORDER, height=1).pack(fill="x", pady=8)

        settings_lbl = tk.Label(card, text="Timing & Repeat Settings",
                                font=("Segoe UI", 10, "bold"), bg=BG2, fg=T1, anchor="w")
        settings_lbl.pack(fill="x")

        settings = tk.Frame(card, bg=BG2)
        settings.pack(fill="x", pady=(4, 0))

        def lbl(text): return tk.Label(settings, text=text, font=FONT_SUB, bg=BG2, fg=T2)
        def spin(var, lo, hi, w=5):
            return tk.Spinbox(settings, from_=lo, to=hi, textvariable=var,
                              width=w, font=FONT_MONO, bg=BG3, fg=T1, relief="flat")

        stag_var = tk.IntVar(value=group["stagger_secs"])
        lbl("How long between phones starting:").pack(side="left")
        spin(stag_var, 0, 3600).pack(side="left", padx=4)
        lbl("seconds  |  Repeat:").pack(side="left", padx=(4, 0))
        rep_var = tk.IntVar(value=group["repeat"])
        spin(rep_var, 1, 999).pack(side="left", padx=4)
        lbl("times  |").pack(side="left")
        forever_var = tk.BooleanVar(value=group["repeat_forever"])
        tk.Checkbutton(settings, text="Forever", variable=forever_var,
                       font=FONT_SUB, bg=BG2, fg=T1, selectcolor=BG3,
                       activebackground=BG2).pack(side="left", padx=4)

        def save(*_):
            group.update({
                "stagger_secs":   stag_var.get(),
                "repeat":         rep_var.get(),
                "repeat_forever": forever_var.get(),
            })

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
                    "Tick at least one phone for each group, or remove the empty group."
                )
                return False
        return True


class SequenceEditorWindow(tk.Toplevel):
    def __init__(self, parent, group):
        super().__init__(parent)
        self.group = group
        self.title(f"Edit Sequence — {group['name']}")
        self.config(bg=BG)
        self.geometry("600x540")
        self.resizable(True, True)
        self.grab_set()
        self.transient(parent)

        tk.Label(self, text=f"Sequence for: {group['name']}",
                 font=("Segoe UI", 14, "bold"), bg=BG, fg=T1).pack(pady=(14, 4), padx=16, anchor="w")
        tk.Label(self,
                 text="Add steps one at a time. The phone will do them in order, top to bottom.",
                 font=FONT_SUB, bg=BG, fg=T2).pack(padx=16, anchor="w", pady=(0, 10))

        ctrl = tk.Frame(self, bg=BG)
        ctrl.pack(fill="x", padx=16, pady=4)
        for text, cmd in [
            ("+ Add Step", self._add),
            ("Remove", self._remove),
            ("▲ Up", self._up),
            ("▼ Down", self._dn),
        ]:
            color = GREEN if "Add" in text else (RED if "Remove" in text else BG3)
            fg    = BG if color != BG3 else T1
            tk.Button(ctrl, text=text, command=cmd, bg=color, fg=fg,
                      font=("Segoe UI", 10, "bold"), relief="flat",
                      cursor="hand2", pady=6, padx=12).pack(side="left", padx=(0, 6))

        frame = tk.Frame(self, bg=BG2)
        frame.pack(fill="both", expand=True, padx=16, pady=8)
        self._lb = tk.Listbox(frame, font=FONT_MONO, bg=BG2, fg=T1,
                              selectbackground=ACCENT, relief="flat",
                              height=12, activestyle="none")
        sb = tk.Scrollbar(frame, orient="vertical", command=self._lb.yview)
        self._lb.configure(yscrollcommand=sb.set)
        self._lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        bottom = tk.Frame(self, bg=BG)
        bottom.pack(fill="x", padx=16, pady=10)
        if state["phones"]:
            tk.Button(bottom, text="▶ Test on first phone", font=FONT_SUB,
                      bg=YELLOW, fg=BG, relief="flat", cursor="hand2",
                      command=self._test, padx=10, pady=6).pack(side="left")
        tk.Button(bottom, text="Done ✓", font=("Segoe UI", 11, "bold"),
                  bg=GREEN, fg=BG, relief="flat", cursor="hand2",
                  command=self.destroy, padx=16, pady=8).pack(side="right")

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
        if not sel or sel[0] == 0:
            return
        i = sel[0]
        self.group["steps"][i-1], self.group["steps"][i] = \
            self.group["steps"][i], self.group["steps"][i-1]
        self._refresh()
        self._lb.selection_set(i-1)

    def _dn(self):
        sel = self._lb.curselection()
        if not sel or sel[0] >= len(self.group["steps"]) - 1:
            return
        i = sel[0]
        self.group["steps"][i], self.group["steps"][i+1] = \
            self.group["steps"][i+1], self.group["steps"][i]
        self._refresh()
        self._lb.selection_set(i+1)

    def _test(self):
        serial = state["phones"][0]["serial"]
        steps  = list(self.group["steps"])
        threading.Thread(
            target=lambda: execute_steps(steps, serial), daemon=True).start()


class AddStepDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.result = None
        self.title("Add a Step")
        self.config(bg=BG)
        self.geometry("480x420")
        self.resizable(False, False)
        self.grab_set()
        self.transient(parent)

        tk.Label(self, text="What should the phone do?",
                 font=("Segoe UI", 12, "bold"), bg=BG, fg=T1).pack(pady=(16, 8))

        type_frame = tk.Frame(self, bg=BG2, padx=10, pady=8)
        type_frame.pack(fill="x", padx=16)
        tk.Label(type_frame, text="Action:", font=FONT_BODY, bg=BG2, fg=T2,
                 width=10, anchor="w").pack(side="left")
        self._type = tk.StringVar(value="open_url")
        self._combo = ttk.Combobox(type_frame, textvariable=self._type,
                                   values=list(STEP_LABELS.keys()),
                                   state="readonly", font=FONT_BODY, width=26)
        self._combo.pack(side="left")
        self._combo.bind("<<ComboboxSelected>>", lambda _: self._update())

        self._desc = tk.Label(self, text="", font=FONT_SUB, bg=BG, fg=T2,
                              wraplength=440, justify="left")
        self._desc.pack(padx=16, anchor="w", pady=4)

        self._fields_frame = tk.Frame(self, bg=BG2, padx=12, pady=10)
        self._fields_frame.pack(fill="x", padx=16)
        self._fields: dict[str, tk.StringVar] = {}
        self._update()

        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(pady=14)
        tk.Button(btn_row, text="  OK  ", font=("Segoe UI", 11, "bold"),
                  bg=GREEN, fg=BG, relief="flat", cursor="hand2",
                  command=self._ok, padx=16, pady=8).pack(side="left", padx=8)
        tk.Button(btn_row, text="Cancel", font=FONT_BODY,
                  bg=BG3, fg=T2, relief="flat", cursor="hand2",
                  command=self.destroy, padx=10, pady=6).pack(side="left")

    def _update(self):
        for w in self._fields_frame.winfo_children():
            w.destroy()
        self._fields.clear()
        t = self._type.get()
        self._desc.config(text=STEP_LABELS.get(t, ""))

        def field(label, key, default="", width=30):
            row = tk.Frame(self._fields_frame, bg=BG2)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=label, font=FONT_BODY, bg=BG2, fg=T2,
                     width=16, anchor="w").pack(side="left")
            var = tk.StringVar(value=str(default))
            tk.Entry(row, textvariable=var, font=FONT_MONO, bg=BG, fg=T1,
                     insertbackground=T1, relief="flat", width=width).pack(side="left")
            self._fields[key] = var

        def tip(text):
            tk.Label(self._fields_frame, text=text,
                     font=FONT_SUB, bg=BG2, fg=T3, justify="left").pack(anchor="w", pady=2)

        if t == "open_url":
            field("Website URL:", "url", "https://example.com", 34)
        elif t == "tap":
            field("X position (left←→right):", "x", 540)
            field("Y position (top↑↓bottom):", "y", 960)
            tip("Tip: adb shell getevent -l  then tap the phone to see X/Y numbers")
        elif t == "wait":
            field("Seconds to wait:", "seconds", 30)
            tip("How long to pause before the next step")
        elif t == "swipe":
            field("Start X:", "x1", 540)
            field("Start Y:", "y1", 800)
            field("End X:", "x2", 540)
            field("End Y:", "y2", 200)
            field("Speed in ms:", "ms", 500)
        elif t == "keyevent":
            field("Key name:", "key", "BACK")
            tip("Options: BACK   HOME   ENTER   APP_SWITCH   VOLUME_UP   POWER")
        elif t == "close_app":
            field("Package name:", "package", "com.android.chrome")
            tip("Chrome = com.android.chrome   |   your app = com.yourname.appname")
        elif t == "type_text":
            field("Text to type:", "text", "hello world")

    def _ok(self):
        t = self._type.get()
        step = {"type": t}
        for key, var in self._fields.items():
            v = var.get().strip()
            try:
                step[key] = int(v)
            except ValueError:
                step[key] = v
        self.result = step
        self.destroy()


class LaunchPage(PageBase):
    """
    Full control center inside the wizard.
    - Start/stop the dashboard server
    - Run/stop all groups
    - See live log output
    Everything stays inside the wizard — no need to touch the browser.
    """
    def __init__(self, parent):
        super().__init__(parent)
        self._server_proc = None
        self._running     = False
        self.header("Launch & Control 🚀",
                    "Everything runs from here. No need to open the browser or touch anything else.")

        summary_outer = tk.Frame(self, bg=BG2, padx=12, pady=10)
        summary_outer.pack(fill="x", pady=(0, 10))
        tk.Label(summary_outer, text="Your groups:", font=("Segoe UI", 10, "bold"),
                 bg=BG2, fg=T1, anchor="w").pack(fill="x")
        self._summary = tk.Text(summary_outer, height=6, font=FONT_MONO, bg=BG2, fg=T1,
                                relief="flat", state="disabled", wrap="word")
        self._summary.pack(fill="x")

        step1 = tk.Frame(self, bg=BG3, padx=14, pady=12)
        step1.pack(fill="x", pady=(0, 8))
        tk.Label(step1, text="Step A — Start the server",
                 font=("Segoe UI", 11, "bold"), bg=BG3, fg=ACCENT, anchor="w").pack(fill="x")
        tk.Label(step1,
                 text="This starts the CPharm background server. A black Terminal window will open — "
                      "leave it open the whole time.",
                 font=FONT_SUB, bg=BG3, fg=T2, anchor="w", wraplength=620).pack(fill="x", pady=(2, 8))
        srv_row = tk.Frame(step1, bg=BG3)
        srv_row.pack(fill="x")
        self._btn_start_srv = tk.Button(srv_row, text="▶  Start Server",
                                        font=("Segoe UI", 10, "bold"),
                                        bg=GREEN, fg=BG, relief="flat", cursor="hand2",
                                        command=self._start_server, padx=14, pady=7)
        self._btn_start_srv.pack(side="left", padx=(0, 8))
        self._btn_stop_srv  = tk.Button(srv_row, text="■  Stop Server",
                                        font=("Segoe UI", 10, "bold"),
                                        bg=RED, fg=BG, relief="flat", cursor="hand2",
                                        command=self._stop_server, padx=14, pady=7,
                                        state="disabled")
        self._btn_stop_srv.pack(side="left")
        self._srv_lbl = tk.Label(srv_row, text="Server not running",
                                 font=FONT_SUB, bg=BG3, fg=T2)
        self._srv_lbl.pack(side="left", padx=12)

        step2 = tk.Frame(self, bg=BG3, padx=14, pady=12)
        step2.pack(fill="x", pady=(0, 8))
        tk.Label(step2, text="Step B — Run your groups",
                 font=("Segoe UI", 11, "bold"), bg=BG3, fg=GREEN, anchor="w").pack(fill="x")
        tk.Label(step2,
                 text="Starts all groups at the same time. Each group runs on its assigned phones.",
                 font=FONT_SUB, bg=BG3, fg=T2, anchor="w").pack(fill="x", pady=(2, 8))
        run_row = tk.Frame(step2, bg=BG3)
        run_row.pack(fill="x")
        self._btn_run = tk.Button(run_row, text="▶  Run All Groups",
                                  font=("Segoe UI", 10, "bold"),
                                  bg=GREEN, fg=BG, relief="flat", cursor="hand2",
                                  command=self._run_groups, padx=14, pady=7, state="disabled")
        self._btn_run.pack(side="left", padx=(0, 8))
        self._btn_stop_grp = tk.Button(run_row, text="■  Stop All",
                                       font=("Segoe UI", 10, "bold"),
                                       bg=RED, fg=BG, relief="flat", cursor="hand2",
                                       command=self._stop_groups, padx=14, pady=7, state="disabled")
        self._btn_stop_grp.pack(side="left")
        self._run_lbl = tk.Label(run_row, text="", font=FONT_SUB, bg=BG3, fg=T2)
        self._run_lbl.pack(side="left", padx=12)

        tk.Label(self, text="Live log:", font=("Segoe UI", 9, "bold"),
                 bg=BG, fg=T2, anchor="w").pack(fill="x", pady=(6, 2))
        log_frame = tk.Frame(self, bg=BG2)
        log_frame.pack(fill="both", expand=True)
        self._log_box = tk.Text(log_frame, height=8, font=FONT_MONO, bg=BG2, fg=T1,
                                relief="flat", state="disabled", wrap="word")
        log_sb = tk.Scrollbar(log_frame, orient="vertical", command=self._log_box.yview)
        self._log_box.configure(yscrollcommand=log_sb.set)
        self._log_box.pack(side="left", fill="both", expand=True)
        log_sb.pack(side="right", fill="y")

        misc_row = tk.Frame(self, bg=BG)
        misc_row.pack(fill="x", pady=6)
        tk.Button(misc_row, text="Open Browser Dashboard",
                  font=FONT_SUB, bg=BG3, fg=T1, relief="flat", cursor="hand2",
                  command=lambda: webbrowser.open(f"http://localhost:{DASHBOARD_PORT}"),
                  padx=10, pady=5).pack(side="left", padx=(0, 8))
        tk.Button(misc_row, text="💾 Save Config",
                  font=FONT_SUB, bg=BG3, fg=T1, relief="flat", cursor="hand2",
                  command=self._save_silent, padx=10, pady=5).pack(side="left")

    def on_enter(self):
        self._refresh_summary()
        self._save_silent()

    def _refresh_summary(self):
        lines = []
        for i, g in enumerate(state["groups"], 1):
            names = []
            for s in g["phones"]:
                m = next((p for p in state["phones"] if p["serial"] == s), None)
                names.append(m["name"] if m else s)
            rep = "forever" if g["repeat_forever"] else f"{g['repeat']}×"
            lines.append(
                f"  Group {i}: {g['name']!r}  |  "
                f"{len(names)} phone(s)  |  {len(g['steps'])} steps  |  "
                f"stagger {g['stagger_secs']}s  |  repeat {rep}"
            )
        self._summary.config(state="normal")
        self._summary.delete("1.0", "end")
        self._summary.insert("end", "\n".join(lines) or "  (no groups configured)")
        self._summary.config(state="disabled")

    def _log(self, text, color=T1):
        self._log_box.config(state="normal")
        self._log_box.insert("end", text + "\n")
        self._log_box.see("end")
        self._log_box.config(state="disabled")

    def _save_silent(self):
        d = state.get("cpharm_dir", "")
        if not d:
            return None
        rec_dir = Path(d) / "automation" / "recordings"
        rec_dir.mkdir(parents=True, exist_ok=True)
        out = rec_dir / "groups_config.json"
        out.write_text(json.dumps({"groups": state["groups"]}, indent=2))
        return str(out)

    def _start_server(self):
        d = state.get("cpharm_dir", "")
        if not d:
            messagebox.showerror("Error", "CPharm folder not set. Go back to Step 2.")
            return
        saved = self._save_silent()
        if not saved:
            return
        dashboard = Path(d) / "automation" / "dashboard.py"
        try:
            self._server_proc = subprocess.Popen(
                [state["python_cmd"], str(dashboard)],
                cwd=str(Path(d) / "automation"),
                creationflags=subprocess.CREATE_NEW_CONSOLE if IS_WIN else 0,
            )
            self._log("Server starting… waiting 3 seconds…")
            self._btn_start_srv.config(state="disabled")
            self._btn_stop_srv.config(state="normal")
            self._srv_lbl.config(text="⏳ Starting…", fg=YELLOW)

            def wait_and_enable():
                time.sleep(3)
                if self._server_proc.poll() is None:
                    self._srv_lbl.config(text="✅ Server running", fg=GREEN)
                    self._btn_run.config(state="normal")
                    self._log("Server is up! Click 'Run All Groups' to start.")
                else:
                    self._srv_lbl.config(text="❌ Server crashed — check Terminal", fg=RED)
                    self._btn_start_srv.config(state="normal")
                    self._btn_stop_srv.config(state="disabled")

            threading.Thread(target=wait_and_enable, daemon=True).start()
        except Exception as e:
            messagebox.showerror("Failed to start", str(e))

    def _stop_server(self):
        self._stop_groups()
        if self._server_proc:
            self._server_proc.terminate()
            self._server_proc = None
        self._srv_lbl.config(text="Server stopped", fg=T2)
        self._btn_start_srv.config(state="normal")
        self._btn_stop_srv.config(state="disabled")
        self._btn_run.config(state="disabled")
        self._log("Server stopped.")

    def _api_call(self, path: str, body: dict):
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
            result = self._api_call("/api/groups/run", {"groups": state["groups"]})
            if "error" in result:
                self._log(f"❌  Error: {result['error']}")
                self._btn_run.config(state="normal")
                self._btn_stop_grp.config(state="disabled")
            else:
                n = result.get("groups", len(state["groups"]))
                self._log(f"✅  {n} group(s) running in parallel!")
                self._run_lbl.config(text=f"{n} group(s) running", fg=GREEN)

        threading.Thread(target=go, daemon=True).start()

    def _stop_groups(self):
        def go():
            self._api_call("/api/groups/stop", {})
            self._log("All groups stopped.")
            self._run_lbl.config(text="", fg=T2)
            self._btn_run.config(state="normal")
            self._btn_stop_grp.config(state="disabled")

        threading.Thread(target=go, daemon=True).start()


class CPharmWizard(tk.Tk):
    PAGES = [
        WelcomePage,
        SoftwarePage,
        RepoPage,
        DepsPage,
        PhonesPage,
        GroupsPage,
        LaunchPage,
    ]
    PAGE_NAMES = [
        "Welcome",
        "Software",
        "Get CPharm",
        "Install Deps",
        "Phones",
        "Groups",
        "Launch!",
    ]

    def __init__(self):
        super().__init__()
        self.title("CPharm Setup Wizard")
        self.geometry("740x660")
        self.minsize(700, 580)
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

    def _build_header(self):
        hdr = tk.Frame(self, bg=BG2, height=56)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="CPharm Setup", font=("Segoe UI", 13, "bold"),
                 bg=BG2, fg=ACCENT).pack(side="left", padx=16, pady=10)
        dot_row = tk.Frame(hdr, bg=BG2)
        dot_row.pack(side="right", padx=16)
        self._dots = []
        for i in range(len(self.PAGES)):
            d = tk.Label(dot_row, text="●", font=("Segoe UI", 9), bg=BG2, fg=T3)
            d.pack(side="left", padx=3)
            self._dots.append(d)
        self._step_lbl = tk.Label(hdr, text="", font=FONT_SUB, bg=BG2, fg=T2)
        self._step_lbl.pack(side="right", padx=(0, 10))

    def _build_footer(self):
        ftr = tk.Frame(self, bg=BG2, height=54)
        ftr.pack(fill="x")
        ftr.pack_propagate(False)
        self._next_btn = tk.Button(ftr, text="Next  →",
                                   font=("Segoe UI", 11, "bold"),
                                   bg=ACCENT, fg=BG, relief="flat", cursor="hand2",
                                   command=self._next, padx=20, pady=8)
        self._next_btn.pack(side="right", padx=16, pady=8)
        self._back_btn = tk.Button(ftr, text="← Back", font=("Segoe UI", 11),
                                   bg=BG3, fg=T2, relief="flat", cursor="hand2",
                                   command=self._back, padx=16, pady=8)
        self._back_btn.pack(side="right", padx=(0, 8), pady=8)

    def _show(self, idx):
        for i, p in enumerate(self._pages):
            if i == idx:
                p.lift()
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
