"""
CPharm Setup Wizard
Run this on Windows. It installs everything and lets you build a tap sequence
on your master phone, then blast it across all your connected phones.

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
from urllib.request import urlopen, urlretrieve
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

REPO_URL    = "https://github.com/twoskoops707/cpharm.git"
DASHBOARD_PORT = 8080
IS_WIN      = platform.system() == "Windows"

BG      = "#0d1117"
BG2     = "#161b22"
BG3     = "#21262d"
BORDER  = "#30363d"
ACCENT  = "#58a6ff"
GREEN   = "#3fb950"
RED     = "#f85149"
YELLOW  = "#d29922"
T1      = "#e6edf3"
T2      = "#8b949e"

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
    "cpharm_dir":   "",
    "python_cmd":   "python",
    "phones":       [],
    "steps":        [],
    "clone_count":  4,
    "stagger_secs": 60,
    "repeat":       1,
    "repeat_forever": False,
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
        return f"{icon}  Open → {step.get('url', '')}"
    if t == "tap":
        return f"{icon}  Tap  → ({step.get('x', 0)}, {step.get('y', 0)})"
    if t == "wait":
        return f"{icon}  Wait → {step.get('seconds', 1)} seconds"
    if t == "swipe":
        return f"{icon}  Swipe → ({step.get('x1',0)},{step.get('y1',0)}) to ({step.get('x2',0)},{step.get('y2',0)})"
    if t == "keyevent":
        return f"{icon}  Key  → {step.get('key', 'BACK')}"
    if t == "close_app":
        return f"{icon}  Close → {step.get('package', 'Chrome')}"
    if t == "clear_cookies":
        return f"{icon}  Clear cookies"
    if t == "rotate_identity":
        return f"{icon}  Rotate IP + identity"
    if t == "type_text":
        return f"{icon}  Type → \"{step.get('text', '')}\""
    return f"• {t}"


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
                     justify="left", anchor="w", wraplength=620).pack(fill="x", pady=(0, 16))

    def btn(self, parent, text, cmd, color=ACCENT, width=None):
        kw = dict(text=text, command=cmd, bg=color, fg=BG,
                  font=("Segoe UI", 10, "bold"), relief="flat",
                  cursor="hand2", pady=7, padx=14, bd=0)
        if width:
            kw["width"] = width
        b = tk.Button(parent, **kw)
        b.pack(side="left", padx=(0, 8))
        return b

    def status_row(self, parent, label):
        row = tk.Frame(parent, bg=BG2)
        row.pack(fill="x", pady=2)
        tk.Label(row, text=label, font=FONT_BODY, bg=BG2, fg=T1,
                 width=30, anchor="w").pack(side="left", padx=8, pady=6)
        lbl = tk.Label(row, text="⬜  Checking…", font=FONT_MONO, bg=BG2, fg=T2, anchor="w")
        lbl.pack(side="left", padx=4)
        return lbl, row


class WelcomePage(PageBase):
    def __init__(self, parent):
        super().__init__(parent)

        tk.Label(self, text="👋", font=("Segoe UI", 48), bg=BG).pack(pady=(30, 10))
        tk.Label(self, text="Welcome to CPharm Setup", font=("Segoe UI", 22, "bold"),
                 bg=BG, fg=T1).pack()
        tk.Label(self,
                 text="This wizard will get everything installed on your Windows PC\n"
                      "and help you build a tap sequence to run on all your phones at once.\n\n"
                      "You do NOT need to know anything about computers.\n"
                      "Just click Next and follow the instructions.",
                 font=FONT_BIG, bg=BG, fg=T2, justify="center").pack(pady=20)

        box = tk.Frame(self, bg=BG3, padx=20, pady=14)
        box.pack(padx=40, fill="x")
        tk.Label(box, text="Before you start, make sure you have:", font=("Segoe UI", 10, "bold"),
                 bg=BG3, fg=YELLOW, anchor="w").pack(fill="x")
        for item in [
            "✅  A Windows 10 or 11 computer",
            "✅  An internet connection",
            "✅  At least one Android phone (or emulator) plugged in or on WiFi",
            "✅  About 10 minutes of free time",
        ]:
            tk.Label(box, text=item, font=FONT_BODY, bg=BG3, fg=T1, anchor="w").pack(fill="x", pady=2)


class SoftwarePage(PageBase):
    def __init__(self, parent):
        super().__init__(parent)
        self.header(
            "Step 1 — Install the Software",
            "These are free programs that CPharm needs to work. Click the green button next to "
            "anything that shows ❌ to install it. Then click 'Check Again'."
        )

        self._info = tk.Frame(self, bg=BG3, padx=16, pady=12)
        self._info.pack(fill="x", pady=(0, 12))
        tk.Label(self._info,
                 text="💡  HOW TO OPEN A TERMINAL:\n"
                      "   Press the Windows key on your keyboard → type  cmd  → press Enter.\n"
                      "   A black window opens. That is the Terminal. Copy and paste the commands below.",
                 font=FONT_BODY, bg=BG3, fg=T2, justify="left", anchor="w").pack(fill="x")

        checks = tk.Frame(self, bg=BG)
        checks.pack(fill="x", pady=(0, 10))

        self._rows = {}
        items = [
            ("python",  "Python 3.11+",
             "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe",
             "Download & run the Python installer.\n"
             "IMPORTANT: on the first screen, tick the box that says\n"
             "\"Add Python to PATH\" before clicking Install Now."),
            ("git",     "Git for Windows",
             "https://git-scm.com/download/win",
             "Download & run the Git installer. Click Next on every screen."),
            ("adb",     "Android Debug Bridge (ADB)",
             "https://dl.google.com/android/repository/platform-tools-latest-windows.zip",
             "Download the zip → unzip it → put the 'platform-tools' folder somewhere easy\n"
             "like  C:\\platform-tools.  Then add it to PATH:\n"
             "  Windows key → type 'environment variables' → Edit → Path → New → paste the folder path."),
        ]

        for key, label, url, tip in items:
            frame = tk.Frame(checks, bg=BG2, pady=4, padx=10)
            frame.pack(fill="x", pady=3)
            status = tk.Label(frame, text="…", font=FONT_MONO, bg=BG2, fg=T2, width=3)
            status.pack(side="left")
            tk.Label(frame, text=label, font=FONT_BODY, bg=BG2, fg=T1, width=22, anchor="w").pack(side="left")
            inst_btn = tk.Button(frame, text="Open Download Page", font=FONT_SUB,
                                 bg=ACCENT, fg=BG, relief="flat", cursor="hand2",
                                 command=lambda u=url: webbrowser.open(u), padx=10)
            inst_btn.pack(side="left", padx=6)
            tip_btn = tk.Button(frame, text="How?", font=FONT_SUB,
                                bg=BG3, fg=T2, relief="flat", cursor="hand2",
                                command=lambda t=tip, l=label: messagebox.showinfo(
                                    f"How to install {l}", t))
            tip_btn.pack(side="left")
            self._rows[key] = status

        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(fill="x", pady=8)
        self.btn(btn_row, "Check Again", self._check)
        self._result_lbl = tk.Label(btn_row, text="", font=FONT_SUB, bg=BG, fg=T2)
        self._result_lbl.pack(side="left", padx=10)

    def on_enter(self):
        self._check()

    def _check(self):
        checks = {
            "python": self._has_python(),
            "git":    self._has_cmd("git", "--version"),
            "adb":    self._has_cmd("adb", "version"),
        }
        all_ok = True
        for key, ok in checks.items():
            lbl = self._rows[key]
            lbl.config(text="✅" if ok else "❌", fg=GREEN if ok else RED)
            if not ok:
                all_ok = False
        if all_ok:
            self._result_lbl.config(text="All software found! Click Next →", fg=GREEN)
        else:
            self._result_lbl.config(
                text="Install anything showing ❌ then click Check Again", fg=YELLOW)
        return all_ok

    def _has_python(self):
        for cmd in ["python", "python3", "py"]:
            ok, out = run_cmd([cmd, "--version"])
            if ok and "Python 3" in out:
                state["python_cmd"] = cmd
                return True
        return False

    def _has_cmd(self, *args):
        ok, _ = run_cmd(list(args))
        return ok

    def can_advance(self):
        if not self._check():
            messagebox.showerror(
                "Missing software",
                "Please install everything showing ❌ before continuing.\n\n"
                "After installing, close and reopen your Terminal, then click Check Again."
            )
            return False
        return True


class RepoPage(PageBase):
    def __init__(self, parent):
        super().__init__(parent)
        self.header(
            "Step 2 — Get CPharm",
            "We need to download the CPharm files onto your computer. "
            "You only need to do this once."
        )

        steps_box = tk.Frame(self, bg=BG3, padx=16, pady=14)
        steps_box.pack(fill="x", pady=(0, 14))
        tk.Label(steps_box,
                 text="📋  STEP-BY-STEP:\n\n"
                      "1.  Open Terminal  (Windows key → type cmd → Enter)\n\n"
                      "2.  Copy this line and paste it into the Terminal, then press Enter:\n",
                 font=FONT_BODY, bg=BG3, fg=T2, justify="left", anchor="w").pack(fill="x")

        cmd_frame = tk.Frame(steps_box, bg="#000", padx=10, pady=8)
        cmd_frame.pack(fill="x")
        cmd_text = tk.Label(cmd_frame,
                            text=f"git clone {REPO_URL}  C:\\CPharm",
                            font=FONT_MONO, bg="#000", fg=GREEN, cursor="hand2", anchor="w")
        cmd_text.pack(fill="x")

        tk.Label(steps_box,
                 text="\n3.  Wait for it to finish (you'll see a folder called C:\\CPharm appear).\n\n"
                      "4.  Then come back here and click the folder button below so I know where it is.",
                 font=FONT_BODY, bg=BG3, fg=T2, justify="left", anchor="w").pack(fill="x")

        sep = tk.Frame(steps_box, bg=BG3, pady=6)
        sep.pack(fill="x")
        tk.Label(sep, text="— OR —", font=FONT_SUB, bg=BG3, fg=T2).pack()

        tk.Label(steps_box,
                 text="If you already have the CPharm folder somewhere, just click the button below.",
                 font=FONT_BODY, bg=BG3, fg=T2, anchor="w").pack(fill="x", pady=(6, 0))

        folder_row = tk.Frame(self, bg=BG)
        folder_row.pack(fill="x", pady=8)
        self.btn(folder_row, "📂  Browse for CPharm Folder", self._pick_folder)
        self._folder_lbl = tk.Label(folder_row, text="No folder selected yet",
                                    font=FONT_MONO, bg=BG, fg=T2)
        self._folder_lbl.pack(side="left", padx=10)

        self._deps_frame = tk.Frame(self, bg=BG)
        self._deps_frame.pack(fill="x", pady=4)
        self._deps_lbl = tk.Label(self._deps_frame, text="", font=FONT_SUB, bg=BG, fg=T2)
        self._deps_lbl.pack(side="left")

    def on_enter(self):
        if Path("C:/CPharm/automation/dashboard.py").exists():
            state["cpharm_dir"] = "C:/CPharm"
            self._folder_lbl.config(text="C:/CPharm  ✅", fg=GREEN)

    def _pick_folder(self):
        d = filedialog.askdirectory(title="Select the CPharm folder",
                                    initialdir="C:/")
        if d:
            if not Path(d).joinpath("automation", "dashboard.py").exists():
                messagebox.showerror(
                    "Wrong folder",
                    "That doesn't look like the CPharm folder.\n"
                    "The right folder should contain an 'automation' sub-folder.\n\n"
                    "Try selecting the folder named 'CPharm' or 'cpharm'."
                )
                return
            state["cpharm_dir"] = d
            self._folder_lbl.config(text=f"{d}  ✅", fg=GREEN)

    def can_advance(self):
        if not state["cpharm_dir"]:
            messagebox.showerror(
                "No folder selected",
                "Please select the CPharm folder before continuing.\n\n"
                "If you haven't downloaded it yet:\n"
                "1. Open Terminal\n"
                f"2. Type: git clone {REPO_URL} C:\\CPharm\n"
                "3. Press Enter and wait\n"
                "4. Come back and click the folder button"
            )
            return False
        return True


class DepsPage(PageBase):
    def __init__(self, parent):
        super().__init__(parent)
        self.header(
            "Step 3 — Install Python Packages",
            "CPharm needs a few extra Python pieces. Click the big button and wait — "
            "this only takes about 30 seconds."
        )

        info = tk.Frame(self, bg=BG3, padx=16, pady=14)
        info.pack(fill="x", pady=(0, 14))
        tk.Label(info,
                 text="What's happening when you click Install:\n"
                      "  Python will download two small helper packages called websockets and psutil.\n"
                      "  They're free and safe. You need them for the dashboard to work.",
                 font=FONT_BODY, bg=BG3, fg=T2, justify="left", anchor="w").pack(fill="x")

        self.btn(self, "  Install Python Packages  ", self._install, color=GREEN, width=28)

        self._out = tk.Text(self, height=10, font=FONT_MONO, bg=BG2, fg=T1,
                            insertbackground=T1, relief="flat", state="disabled",
                            wrap="word")
        self._out.pack(fill="x", pady=12)

        self._done = False

    def _install(self):
        d = state.get("cpharm_dir", "")
        if not d:
            messagebox.showerror("Error", "Go back and select the CPharm folder first.")
            return
        req = Path(d) / "requirements.txt"
        cmd = [state["python_cmd"], "-m", "pip", "install", "-r", str(req), "--upgrade"]

        def run():
            self._log("Installing packages…\n")
            ok, out = run_cmd(cmd, timeout=180)
            self._log(out)
            if ok:
                self._log("\n✅  All done! Click Next →\n")
                self._done = True
            else:
                self._log("\n❌  Something went wrong. Read the red text above.\n"
                          "If you see 'not recognized', Python isn't in PATH.\n"
                          "Close this wizard and reinstall Python with the PATH checkbox ticked.\n")

        threading.Thread(target=run, daemon=True).start()

    def _log(self, text):
        self._out.config(state="normal")
        self._out.insert("end", text)
        self._out.see("end")
        self._out.config(state="disabled")

    def can_advance(self):
        if not self._done:
            messagebox.showinfo(
                "Not installed yet",
                "Click 'Install Python Packages' and wait for it to finish,\n"
                "then click Next."
            )
            return False
        return True


class PhonesPage(PageBase):
    def __init__(self, parent):
        super().__init__(parent)
        self.header(
            "Step 4 — Connect Your Phones",
            "Plug in your Android phones with a USB cable, or connect them over WiFi. "
            "Then click Scan."
        )

        info = tk.Frame(self, bg=BG3, padx=16, pady=14)
        info.pack(fill="x", pady=(0, 14))
        tk.Label(info,
                 text="📱  USB PHONE:\n"
                      "   1. Plug the phone into your computer with a USB cable.\n"
                      "   2. On the phone: Settings → Developer Options → turn on USB Debugging.\n"
                      "   3. A popup appears on the phone — tap 'Allow'.\n\n"
                      "📡  WIFI PHONE / EMULATOR:\n"
                      "   Type the phone's IP address and port below and click Connect.\n"
                      "   BlueStacks default: 127.0.0.1:5555   |   Waydroid: 192.168.250.1:5555",
                 font=FONT_BODY, bg=BG3, fg=T2, justify="left", anchor="w").pack(fill="x")

        ctrl_row = tk.Frame(self, bg=BG)
        ctrl_row.pack(fill="x", pady=6)
        self.btn(ctrl_row, "🔍  Scan for Phones", self._scan)
        tk.Label(ctrl_row, text="or connect WiFi:", font=FONT_SUB, bg=BG, fg=T2).pack(side="left", padx=(12, 6))
        self._ip_var = tk.StringVar(value="127.0.0.1:5555")
        tk.Entry(ctrl_row, textvariable=self._ip_var, font=FONT_MONO, bg=BG2, fg=T1,
                 insertbackground=T1, relief="flat", width=22).pack(side="left")
        self.btn(ctrl_row, "Connect", self._connect_wifi)

        self._list = tk.Listbox(self, font=FONT_MONO, bg=BG2, fg=T1,
                                selectbackground=ACCENT, relief="flat",
                                height=6, activestyle="none")
        self._list.pack(fill="x", pady=8)

        self._status = tk.Label(self, text="Click Scan to find phones.", font=FONT_SUB, bg=BG, fg=T2)
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
            self._status.config(text=f"Found {len(devs)} phone(s). Nice!", fg=GREEN)
        else:
            self._status.config(
                text="No phones found. Plug in a phone and make sure USB Debugging is on.", fg=YELLOW)

    def _connect_wifi(self):
        addr = self._ip_var.get().strip()
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
                "No phones were found. You need at least one phone connected.\n\n"
                "Try:\n"
                "• USB: Make sure USB Debugging is ON in Developer Options\n"
                "• Emulator: Open BlueStacks or Genymotion first, then click Scan\n"
                "• WiFi: Type the phone's IP:port and click Connect"
            )
            return False
        return True


class SequencePage(PageBase):
    def __init__(self, parent):
        super().__init__(parent)
        self.header(
            "Step 5 — Build Your Sequence",
            "Tell the phones what to do. Add steps one by one. "
            "Example: Open website → Wait 30s → Tap → Wait → Close."
        )

        help_box = tk.Frame(self, bg=BG3, padx=14, pady=10)
        help_box.pack(fill="x", pady=(0, 10))
        tk.Label(help_box,
                 text="Think of this like giving your phone a to-do list.\n"
                      "Click '+ Add Step', pick what you want, fill in the details, and click OK.\n"
                      "Repeat until your full routine is listed below.",
                 font=FONT_BODY, bg=BG3, fg=T2, justify="left").pack(fill="x")

        ctrl = tk.Frame(self, bg=BG)
        ctrl.pack(fill="x", pady=6)
        self.btn(ctrl, "+ Add Step", self._add_step, color=GREEN)
        self.btn(ctrl, "Remove Selected", self._remove_step, color=RED)
        self.btn(ctrl, "Move Up", self._move_up, color=BG3)
        self.btn(ctrl, "Move Down", self._move_dn, color=BG3)

        list_frame = tk.Frame(self, bg=BG2)
        list_frame.pack(fill="both", expand=True, pady=6)
        self._steps_list = tk.Listbox(list_frame, font=FONT_MONO, bg=BG2, fg=T1,
                                      selectbackground=ACCENT, relief="flat",
                                      height=10, activestyle="none")
        sb = tk.Scrollbar(list_frame, orient="vertical",
                          command=self._steps_list.yview)
        self._steps_list.configure(yscrollcommand=sb.set)
        self._steps_list.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        bottom_row = tk.Frame(self, bg=BG)
        bottom_row.pack(fill="x", pady=4)

        if state["phones"]:
            self.btn(bottom_row, "▶  Test on Master Phone", self._test_run, color=YELLOW)
        self._test_lbl = tk.Label(bottom_row, text="", font=FONT_SUB, bg=BG, fg=T2)
        self._test_lbl.pack(side="left", padx=8)

    def on_enter(self):
        self._refresh_list()

    def _refresh_list(self):
        self._steps_list.delete(0, "end")
        for i, step in enumerate(state["steps"], 1):
            self._steps_list.insert("end", f"  {i:>2}.  {describe_step(step)}")

    def _add_step(self):
        dlg = AddStepDialog(self.app)
        self.app.wait_window(dlg)
        if dlg.result:
            state["steps"].append(dlg.result)
            self._refresh_list()

    def _remove_step(self):
        sel = self._steps_list.curselection()
        if not sel:
            return
        del state["steps"][sel[0]]
        self._refresh_list()

    def _move_up(self):
        sel = self._steps_list.curselection()
        if not sel or sel[0] == 0:
            return
        i = sel[0]
        state["steps"][i-1], state["steps"][i] = state["steps"][i], state["steps"][i-1]
        self._refresh_list()
        self._steps_list.selection_set(i-1)

    def _move_dn(self):
        sel = self._steps_list.curselection()
        if not sel or sel[0] >= len(state["steps"]) - 1:
            return
        i = sel[0]
        state["steps"][i], state["steps"][i+1] = state["steps"][i+1], state["steps"][i]
        self._refresh_list()
        self._steps_list.selection_set(i+1)

    def _test_run(self):
        if not state["phones"]:
            messagebox.showerror("No phones", "No phones connected.")
            return
        serial = state["phones"][0]["serial"]
        self._test_lbl.config(text="Running on master phone…", fg=YELLOW)

        def run():
            _execute_steps(state["steps"], serial)
            self._test_lbl.config(text="Done! Check your phone.", fg=GREEN)

        threading.Thread(target=run, daemon=True).start()

    def can_advance(self):
        if not state["steps"]:
            if not messagebox.askyesno(
                    "No steps yet",
                    "You haven't added any steps yet.\n\n"
                    "Do you want to continue anyway? (You can add steps later from the dashboard.)"):
                return False
        return True


class SettingsPage(PageBase):
    def __init__(self, parent):
        super().__init__(parent)
        self.header(
            "Step 6 — Set Up the Run",
            "How many phones should run at the same time? How long between each one starting? "
            "How many times should the whole thing loop?"
        )

        clone_box = tk.Frame(self, bg=BG3, padx=16, pady=14)
        clone_box.pack(fill="x", pady=(0, 12))
        tk.Label(clone_box, text="How many phones should run this?",
                 font=("Segoe UI", 12, "bold"), bg=BG3, fg=T1).pack(anchor="w")
        tk.Label(clone_box,
                 text="Each phone will do the same sequence. Pick how many run at once.",
                 font=FONT_SUB, bg=BG3, fg=T2).pack(anchor="w", pady=(2, 8))
        self._clone_var = tk.IntVar(value=state["clone_count"])
        clone_row = tk.Frame(clone_box, bg=BG3)
        clone_row.pack(anchor="w")
        tk.Spinbox(clone_row, from_=1, to=20, textvariable=self._clone_var,
                   font=("Segoe UI", 14, "bold"), width=5, bg=BG2, fg=T1,
                   relief="flat", buttonbackground=BG3).pack(side="left")
        tk.Label(clone_row, text=" phones", font=FONT_BODY, bg=BG3, fg=T2).pack(side="left")

        stagger_box = tk.Frame(self, bg=BG3, padx=16, pady=14)
        stagger_box.pack(fill="x", pady=(0, 12))
        tk.Label(stagger_box, text="How long between each phone starting?",
                 font=("Segoe UI", 12, "bold"), bg=BG3, fg=T1).pack(anchor="w")
        tk.Label(stagger_box,
                 text="If you pick 60 seconds: phone 1 starts now, phone 2 starts 1 minute later, etc.\n"
                      "This looks more natural and human-like.",
                 font=FONT_SUB, bg=BG3, fg=T2).pack(anchor="w", pady=(2, 8))
        self._stagger_var = tk.IntVar(value=0)
        stagger_opts = [
            ("All at the same time", 0),
            ("30 seconds apart", 30),
            ("1 minute apart", 60),
            ("5 minutes apart", 300),
        ]
        for label, val in stagger_opts:
            tk.Radiobutton(stagger_box, text=label, variable=self._stagger_var, value=val,
                           font=FONT_BODY, bg=BG3, fg=T1, selectcolor=BG2,
                           activebackground=BG3).pack(anchor="w")
        custom_row = tk.Frame(stagger_box, bg=BG3)
        custom_row.pack(anchor="w", pady=4)
        self._custom_stagger = tk.IntVar(value=120)
        tk.Radiobutton(custom_row, text="Custom:", variable=self._stagger_var, value=-1,
                       font=FONT_BODY, bg=BG3, fg=T1, selectcolor=BG2,
                       activebackground=BG3).pack(side="left")
        tk.Spinbox(custom_row, from_=1, to=3600, textvariable=self._custom_stagger,
                   width=6, font=FONT_MONO, bg=BG2, fg=T1, relief="flat").pack(side="left")
        tk.Label(custom_row, text=" seconds", font=FONT_BODY, bg=BG3, fg=T2).pack(side="left")

        repeat_box = tk.Frame(self, bg=BG3, padx=16, pady=14)
        repeat_box.pack(fill="x", pady=(0, 12))
        tk.Label(repeat_box, text="How many times should it repeat?",
                 font=("Segoe UI", 12, "bold"), bg=BG3, fg=T1).pack(anchor="w")
        self._repeat_forever = tk.BooleanVar(value=False)
        self._repeat_count   = tk.IntVar(value=1)
        tk.Checkbutton(repeat_box, text="Run forever (loop until I stop it)",
                       variable=self._repeat_forever, font=FONT_BODY,
                       bg=BG3, fg=T1, selectcolor=BG2,
                       activebackground=BG3).pack(anchor="w", pady=(4, 4))
        once_row = tk.Frame(repeat_box, bg=BG3)
        once_row.pack(anchor="w")
        tk.Label(once_row, text="OR run", font=FONT_BODY, bg=BG3, fg=T2).pack(side="left")
        tk.Spinbox(once_row, from_=1, to=999, textvariable=self._repeat_count,
                   width=5, font=FONT_MONO, bg=BG2, fg=T1, relief="flat").pack(side="left", padx=6)
        tk.Label(once_row, text="time(s)", font=FONT_BODY, bg=BG3, fg=T2).pack(side="left")

    def can_advance(self):
        state["clone_count"]   = self._clone_var.get()
        stag = self._stagger_var.get()
        state["stagger_secs"]  = self._custom_stagger.get() if stag == -1 else stag
        state["repeat_forever"] = self._repeat_forever.get()
        state["repeat"]        = self._repeat_count.get()
        return True


class LaunchPage(PageBase):
    def __init__(self, parent):
        super().__init__(parent)
        self.header("Step 7 — You're Ready! 🎉")

        self._summary = tk.Text(self, height=8, font=FONT_MONO, bg=BG2, fg=T1,
                                relief="flat", state="disabled", wrap="word")
        self._summary.pack(fill="x", pady=(0, 14))

        how_box = tk.Frame(self, bg=BG3, padx=16, pady=12)
        how_box.pack(fill="x", pady=(0, 14))
        tk.Label(how_box,
                 text="What happens when you click Launch:\n"
                      "  1.  The CPharm dashboard opens in this window.\n"
                      "  2.  Your browser opens to  http://localhost:8080  — that's the control panel.\n"
                      "  3.  From there, click 'Start All' and your phones will start running.\n\n"
                      "Leave the black Terminal window open while CPharm is running.\n"
                      "Close the Terminal to shut everything down.",
                 font=FONT_BODY, bg=BG3, fg=T2, justify="left").pack(fill="x")

        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(fill="x")
        self.btn(btn_row, "💾  Save Sequence", self._save_seq, color=BG3)
        self.btn(btn_row, "🚀  Launch CPharm Dashboard", self._launch, color=GREEN, width=28)
        self._launch_lbl = tk.Label(btn_row, text="", font=FONT_SUB, bg=BG, fg=T2)
        self._launch_lbl.pack(side="left", padx=10)

    def on_enter(self):
        summary = (
            f"Phones connected : {len(state['phones'])}\n"
            f"Steps in sequence: {len(state['steps'])}\n"
            f"Clone count      : {state['clone_count']} phones\n"
            f"Stagger timing   : {state['stagger_secs']} seconds between phones\n"
            f"Repeat           : {'Forever' if state['repeat_forever'] else str(state['repeat']) + ' time(s)'}\n"
            f"CPharm folder    : {state['cpharm_dir']}\n"
        )
        self._summary.config(state="normal")
        self._summary.delete("1.0", "end")
        self._summary.insert("end", summary)
        self._summary.config(state="disabled")

    def _save_seq(self):
        d = state.get("cpharm_dir", "")
        if not d:
            messagebox.showerror("Error", "CPharm folder not set. Go back to Step 2.")
            return
        rec_dir = Path(d) / "automation" / "recordings"
        rec_dir.mkdir(parents=True, exist_ok=True)
        cfg = {
            "steps":          state["steps"],
            "clone_count":    state["clone_count"],
            "stagger_secs":   state["stagger_secs"],
            "repeat":         state["repeat"],
            "repeat_forever": state["repeat_forever"],
        }
        out_file = rec_dir / "wizard_sequence.json"
        out_file.write_text(json.dumps(cfg, indent=2))
        messagebox.showinfo("Saved!", f"Sequence saved to:\n{out_file}")

    def _launch(self):
        d = state.get("cpharm_dir", "")
        if not d:
            messagebox.showerror("Error", "CPharm folder not set. Go back to Step 2.")
            return
        self._save_seq()
        dashboard = Path(d) / "automation" / "dashboard.py"
        try:
            subprocess.Popen(
                [state["python_cmd"], str(dashboard)],
                cwd=str(Path(d) / "automation"),
                creationflags=subprocess.CREATE_NEW_CONSOLE if IS_WIN else 0,
            )
            time.sleep(2)
            webbrowser.open(f"http://localhost:{DASHBOARD_PORT}")
            self._launch_lbl.config(
                text="Dashboard is starting! Check your browser.", fg=GREEN)
        except Exception as e:
            messagebox.showerror("Launch failed", str(e))


class AddStepDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.result = None
        self.title("Add a Step")
        self.config(bg=BG)
        self.resizable(False, False)
        self.grab_set()

        self.geometry("480x460")
        self.transient(parent)

        tk.Label(self, text="What should the phone do?",
                 font=("Segoe UI", 12, "bold"), bg=BG, fg=T1).pack(pady=(16, 8))

        self._type_var = tk.StringVar(value="open_url")
        type_frame = tk.Frame(self, bg=BG2, padx=10, pady=6)
        type_frame.pack(fill="x", padx=16)
        tk.Label(type_frame, text="Action:", font=FONT_BODY, bg=BG2, fg=T2,
                 width=12, anchor="w").pack(side="left")
        type_menu = ttk.Combobox(type_frame, textvariable=self._type_var,
                                 values=list(STEP_LABELS.keys()),
                                 state="readonly", font=FONT_BODY, width=24)
        type_menu.pack(side="left")
        type_menu.bind("<<ComboboxSelected>>", lambda _: self._update_fields())

        self._desc_lbl = tk.Label(self, text="", font=FONT_SUB, bg=BG, fg=T2,
                                  wraplength=440, justify="left")
        self._desc_lbl.pack(padx=16, anchor="w", pady=4)

        self._fields_frame = tk.Frame(self, bg=BG2, padx=10, pady=10)
        self._fields_frame.pack(fill="x", padx=16)

        self._fields: dict = {}
        self._update_fields()

        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(pady=14)
        tk.Button(btn_row, text="  OK  ", font=("Segoe UI", 11, "bold"),
                  bg=GREEN, fg=BG, relief="flat", cursor="hand2",
                  command=self._ok, padx=16, pady=8).pack(side="left", padx=8)
        tk.Button(btn_row, text="Cancel", font=FONT_BODY,
                  bg=BG3, fg=T2, relief="flat", cursor="hand2",
                  command=self.destroy, padx=10, pady=6).pack(side="left")

    def _clear_fields(self):
        for w in self._fields_frame.winfo_children():
            w.destroy()
        self._fields = {}

    def _field(self, label, key, default="", width=30):
        row = tk.Frame(self._fields_frame, bg=BG2)
        row.pack(fill="x", pady=4)
        tk.Label(row, text=label, font=FONT_BODY, bg=BG2, fg=T2,
                 width=16, anchor="w").pack(side="left")
        var = tk.StringVar(value=str(default))
        tk.Entry(row, textvariable=var, font=FONT_MONO, bg=BG, fg=T1,
                 insertbackground=T1, relief="flat", width=width).pack(side="left")
        self._fields[key] = var
        return var

    def _update_fields(self):
        self._clear_fields()
        t = self._type_var.get()

        nice = STEP_LABELS.get(t, t)
        self._desc_lbl.config(text=nice)

        if t == "open_url":
            self._field("Website URL:", "url", "https://example.com", 34)
        elif t == "tap":
            self._field("X position:", "x", 540)
            self._field("Y position:", "y", 960)
            tip = tk.Label(self._fields_frame,
                           text="Tip: Use the ADB screenshot to find coordinates.\n"
                                "Or: adb shell getevent -l  then tap the screen to see X/Y.",
                           font=FONT_SUB, bg=BG2, fg=T2, justify="left")
            tip.pack(anchor="w", pady=4)
        elif t == "wait":
            self._field("Seconds to wait:", "seconds", 30)
        elif t == "swipe":
            self._field("Start X:", "x1", 540)
            self._field("Start Y:", "y1", 800)
            self._field("End X:", "x2", 540)
            self._field("End Y:", "y2", 200)
            self._field("Speed (ms):", "ms", 500)
        elif t == "keyevent":
            self._field("Key name:", "key", "BACK")
            tip = tk.Label(self._fields_frame,
                           text="Options: BACK   HOME   ENTER   APP_SWITCH   VOLUME_UP   POWER",
                           font=FONT_SUB, bg=BG2, fg=T2)
            tip.pack(anchor="w", pady=4)
        elif t == "close_app":
            self._field("Package name:", "package", "com.android.chrome", 30)
        elif t == "type_text":
            self._field("Text to type:", "text", "hello", 30)

    def _ok(self):
        t = self._type_var.get()
        step = {"type": t}
        for key, var in self._fields.items():
            v = var.get().strip()
            try:
                step[key] = int(v)
            except ValueError:
                step[key] = v
        self.result = step
        self.destroy()


def _execute_steps(steps, serial):
    for step in steps:
        t = step.get("type", "")
        if t == "open_url":
            adb("shell", "am", "start", "-a", "android.intent.action.VIEW",
                "-d", step.get("url", ""), serial=serial)
        elif t == "tap":
            adb("shell", "input", "tap", str(step.get("x", 0)), str(step.get("y", 0)), serial=serial)
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
            adb("shell", "am", "force-stop", step.get("package", "com.android.chrome"), serial=serial)
        elif t == "clear_cookies":
            adb("shell", "pm", "clear", "com.android.chrome", serial=serial)
        elif t == "rotate_identity":
            pass
        elif t == "type_text":
            text = step.get("text", "").replace(" ", "%s").replace("'", "")
            adb("shell", "input", "text", text, serial=serial)
        time.sleep(0.3)


class CPharmWizard(tk.Tk):
    PAGES = [
        WelcomePage,
        SoftwarePage,
        RepoPage,
        DepsPage,
        PhonesPage,
        SequencePage,
        SettingsPage,
        LaunchPage,
    ]
    PAGE_NAMES = [
        "Welcome",
        "Software",
        "Get CPharm",
        "Install Deps",
        "Phones",
        "Sequence",
        "Settings",
        "Launch!",
    ]

    def __init__(self):
        super().__init__()
        self.title("CPharm Setup Wizard")
        self.geometry("720x640")
        self.minsize(720, 600)
        self.config(bg=BG)
        self.resizable(True, True)

        try:
            self.iconbitmap(default="")
        except Exception:
            pass

        self._build_header()
        self._build_content()
        self._build_footer()

        self._pages = []
        for PageCls in self.PAGES:
            p = PageCls(self._content)
            p.place(relwidth=1, relheight=1)
            self._pages.append(p)

        self._current = 0
        self._show(0)

    def _build_header(self):
        hdr = tk.Frame(self, bg=BG2, height=60)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        tk.Label(hdr, text="CPharm", font=("Segoe UI", 14, "bold"),
                 bg=BG2, fg=ACCENT).pack(side="left", padx=16, pady=10)

        dot_row = tk.Frame(hdr, bg=BG2)
        dot_row.pack(side="right", padx=16, pady=16)
        self._dots = []
        for i in range(len(self.PAGES)):
            d = tk.Label(dot_row, text="●", font=("Segoe UI", 8),
                         bg=BG2, fg=T3, cursor="hand2")
            d.pack(side="left", padx=3)
            d.bind("<Button-1>", lambda e, idx=i: None)
            self._dots.append(d)

        self._step_lbl = tk.Label(hdr, text="", font=FONT_SUB, bg=BG2, fg=T2)
        self._step_lbl.pack(side="right", padx=(0, 12))

    def _build_content(self):
        self._content = tk.Frame(self, bg=BG)
        self._content.pack(fill="both", expand=True, padx=24, pady=16)

    def _build_footer(self):
        ftr = tk.Frame(self, bg=BG2, height=56)
        ftr.pack(fill="x")
        ftr.pack_propagate(False)

        self._next_btn = tk.Button(ftr, text="Next  →", font=("Segoe UI", 11, "bold"),
                                   bg=ACCENT, fg=BG, relief="flat", cursor="hand2",
                                   command=self._next, padx=20, pady=8)
        self._next_btn.pack(side="right", padx=16, pady=10)

        self._back_btn = tk.Button(ftr, text="← Back", font=("Segoe UI", 11),
                                   bg=BG3, fg=T2, relief="flat", cursor="hand2",
                                   command=self._back, padx=16, pady=8)
        self._back_btn.pack(side="right", padx=(0, 8), pady=10)

    def _show(self, idx):
        for i, p in enumerate(self._pages):
            if i == idx:
                p.lift()
            self._dots[i].config(fg=ACCENT if i == idx else (T2 if i < idx else T3))

        self._step_lbl.config(
            text=f"Step {idx + 1} of {len(self.PAGES)}  —  {self.PAGE_NAMES[idx]}")
        self._back_btn.config(state="normal" if idx > 0 else "disabled")
        self._next_btn.config(text="Finish ✓" if idx == len(self.PAGES) - 1 else "Next  →")
        self._pages[idx].on_enter()

    def _next(self):
        page = self._pages[self._current]
        if not page.can_advance():
            return
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
