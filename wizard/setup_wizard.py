"""
CPharm Setup Wizard
Virtual phone farm using Android AVD emulators.
Works on Snapdragon ARM Windows.
The wizard auto-downloads and installs the Android SDK — no Android Studio needed.

Build:
    pip install pyinstaller pillow
    pyinstaller --onefile --windowed --name CPharmSetup setup_wizard.py
"""

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import random
import threading
import time
import urllib.request
import webbrowser
import zipfile
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

REPO_URL            = "https://github.com/twoskoops707/cpharm.git"
DASHBOARD_PORT      = 8080
IS_WIN              = platform.system() == "Windows"

CMDLINE_TOOLS_URL        = "https://dl.google.com/android/repository/commandlinetools-win-14742923_latest.zip"
CMDLINE_TOOLS_URL_ALT    = "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip"
SDK_DEFAULT_PATH         = os.path.join(os.environ.get("LOCALAPPDATA", "C:\\"), "Android", "Sdk")
JAVA_DOWNLOAD_URL        = "https://aka.ms/download-jdk/microsoft-jdk-21-windows-aarch64.msi"
JAVA_DOWNLOAD_URL_X64    = "https://aka.ms/download-jdk/microsoft-jdk-21-windows-x64.msi"
TOR_FALLBACK_URL  = "https://dist.torproject.org/torbrowser/14.0.9/tor-expert-bundle-windows-x86_64-14.0.9.tar.gz"
PYTHON_URL        = "https://www.python.org/ftp/python/3.13.0/python-3.13.0-arm64.exe"
PYTHON_URL_X64    = "https://www.python.org/ftp/python/3.13.0/python-3.13.0-amd64.exe"
CPHARM_ZIP_URL    = "https://github.com/twoskoops707/cpharm/archive/refs/heads/master.zip"
CPHARM_DEFAULT    = os.path.join(os.path.expanduser("~"), "CPharm")

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
    "avds":      [],
    "_emu_procs":  [],
    "groups": [{
        "name":           "Group 1",
        "phones":         {},      # {serial: {"steps": [...], "name": "Phone 1"}}
        "stagger_secs":   30,
        "repeat":         1,
        "repeat_forever": False,
    }],
}


# ─── per-phone sequence editor ────────────────────────────────────────────────

class PerPhoneSequenceEditor(tk.Toplevel):
    """Edit the sequence for ONE specific phone within a group.
    
    Each phone gets its own independent steps list. Cloning makes
    a copy of the master (Phone 1) steps to all other phones.
    """
    def __init__(self, parent, serial, phone_name, steps_list):
        super().__init__(parent)
        self.steps_list = steps_list   # shared reference — mutates group directly
        self.serial     = serial
        self.title(f"Sequence — {phone_name}")
        self.config(bg=BG)
        self.geometry("640x540")
        self.resizable(True, True)
        self.grab_set()
        self.transient(parent)

        tk.Label(self, text=f"Phone: {phone_name}  ({serial})",
                 font=("Segoe UI", 13, "bold"), bg=BG, fg=T1).pack(
                     pady=(14, 2), padx=16, anchor="w")
        tk.Label(self,
                 text="This is THIS phone's own sequence. "
                      "Editing here does not change other phones.",
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
                      padx=10, pady=6).pack(side="left", padx=(0, 6))

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
        tk.Button(bottom, text="Done ✓",
                  font=("Segoe UI", 11, "bold"),
                  bg=GREEN, fg=BG, relief="flat",
                  cursor="hand2", command=self.destroy,
                  padx=16, pady=8).pack(side="right")

        self._refresh()

    def _refresh(self):
        self._lb.delete(0, "end")
        for i, s in enumerate(self.steps_list, 1):
            self._lb.insert("end", f"  {i:>2}.  {describe_step(s)}")

    def _add(self):
        dlg = AddStepDialog(self)
        self.wait_window(dlg)
        if dlg.result:
            self.steps_list.append(dlg.result)
            self._refresh()

    def _remove(self):
        sel = self._lb.curselection()
        if sel:
            del self.steps_list[sel[0]]
            self._refresh()

    def _up(self):
        sel = self._lb.curselection()
        if not sel or sel[0] == 0: return
        i = sel[0]
        self.steps_list[i-1], self.steps_list[i] = self.steps_list[i], self.steps_list[i-1]
        self._refresh(); self._lb.selection_set(i-1)

    def _dn(self):
        sel = self._lb.curselection()
        if not sel or sel[0] >= len(self.steps_list) - 1: return
        i = sel[0]
        self.steps_list[i], self.steps_list[i+1] = self.steps_list[i+1], self.steps_list[i]
        self._refresh(); self._lb.selection_set(i+1)




# ─── helpers ──────────────────────────────────────────────────────────────────

_NO_WIN = subprocess.CREATE_NO_WINDOW if IS_WIN else 0


def run_cmd(cmd, cwd=None, timeout=120):
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=cwd, timeout=timeout,
            shell=isinstance(cmd, str),
            creationflags=_NO_WIN,
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
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, creationflags=_NO_WIN)
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
    avdmgr = sdk_tool("avdmanager")
    flags = subprocess.CREATE_NO_WINDOW if IS_WIN else 0
    try:
        r = subprocess.run(
            [avdmgr, "list", "avd", "-c"],
            capture_output=True, text=True, timeout=30,
            env=_sdk_env(), creationflags=flags,
        )
        return [ln.strip() for ln in r.stdout.splitlines()
                if ln.strip() and not ln.startswith("Error")]
    except Exception:
        return []


def _find_java_home() -> str:
    """
    Find an installed JDK/JRE directory on Windows and return its root path.
    Checks JAVA_HOME, then Android Studio's bundled JBR, then common install locations.
    """
    import glob

    # 1. Respect existing JAVA_HOME if valid
    existing = os.environ.get("JAVA_HOME", "")
    if existing and Path(existing, "bin", "java.exe").exists():
        return existing

    # 2. Android Studio bundles a JetBrains Runtime (jbr/) — glob for any version.
    #    AS installs as "Android Studio", "Android Studio3", "Android Studio Panda", etc.
    #    Sort descending so the newest install is tried first.
    as_search_roots = [
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "Android"),
        os.path.join(os.environ.get("LOCALAPPDATA",  ""), "Programs", "Android"),
        os.path.join(os.environ.get("LOCALAPPDATA",  ""), "Programs"),
        os.path.join(os.environ.get("LOCALAPPDATA",  ""), "Google"),
        os.path.join(os.environ.get("LOCALAPPDATA",  ""), "Android"),
        os.environ.get("PROGRAMFILES", r"C:\Program Files"),
        r"C:\Android",
    ]
    for as_root in as_search_roots:
        if not as_root or not Path(as_root).exists():
            continue
        for studio_dir in sorted(Path(as_root).glob("Android Studio*"), reverse=True):
            jbr = studio_dir / "jbr"
            if (jbr / "bin" / "java.exe").exists():
                return str(jbr)

    # 3. Standard JDK/JRE install locations
    search_roots = [
        os.environ.get("PROGRAMFILES",      r"C:\Program Files"),
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA",      ""),
        r"C:\Program Files\Microsoft",
        r"C:\Program Files\Eclipse Adoptium",
        r"C:\Program Files\Java",
        r"C:\Program Files\OpenJDK",
    ]
    for root in search_roots:
        if not root:
            continue
        for java_exe in glob.glob(
            os.path.join(root, "**", "bin", "java.exe"), recursive=True
        ):
            return str(Path(java_exe).parent.parent)
    return ""


def _sdk_env() -> dict:
    """
    Return a copy of os.environ with JAVA_HOME, ANDROID_SDK_ROOT, and PATH set
    so sdkmanager and avdmanager can find both Java and the SDK root.
    """
    env = os.environ.copy()
    java_home = _find_java_home()
    if java_home:
        env["JAVA_HOME"] = java_home
        java_bin = str(Path(java_home) / "bin")
        env["PATH"] = java_bin + os.pathsep + env.get("PATH", "")
    sdk = state.get("sdk_path") or find_sdk()
    if sdk:
        env["ANDROID_SDK_ROOT"] = sdk
        env["ANDROID_HOME"]     = sdk
    return env


def _machine_arch():
    """
    Return the Android ABI for the HOST machine.
    On Windows ARM64, Python x64 reports AMD64 via env vars and platform.machine().
    Read the registry system-level PROCESSOR_ARCHITECTURE as ground truth.
    """
    if IS_WIN:
        # 1. Process-level env vars (x64 Python on ARM64 sets ARCHITEW6432=ARM64)
        wow    = os.environ.get("PROCESSOR_ARCHITEW6432", "").upper()
        native = os.environ.get("PROCESSOR_ARCHITECTURE",  "").upper()
        if "ARM" in (wow + native):
            return "arm64-v8a"

        # 2. System-level registry key — always reflects real CPU regardless of emulation
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            )
            val, _ = winreg.QueryValueEx(key, "PROCESSOR_ARCHITECTURE")
            winreg.CloseKey(key)
            if "ARM" in str(val).upper():
                return "arm64-v8a"
        except Exception:
            pass

        # 3. PowerShell OSArchitecture as last resort
        try:
            r = subprocess.run(
                ["powershell", "-Command",
                 "[System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture"],
                capture_output=True, text=True, timeout=6,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if "ARM" in r.stdout.upper():
                return "arm64-v8a"
        except Exception:
            pass

    m = platform.machine().lower()
    if "arm" in m or "aarch" in m:
        return "arm64-v8a"
    return "x86_64"


def _canonical_sdkmanager(sdk: str) -> str:
    """
    Always return the cmdline-tools/latest/bin/sdkmanager path.
    The legacy tools/bin/sdkmanager only understands XML v3 and will fail
    against Google's current v4 repository — never use it.
    """
    base = Path(sdk)
    ext  = ".bat" if IS_WIN else ""
    primary = base / "cmdline-tools" / "latest" / "bin" / f"sdkmanager{ext}"
    if primary.exists():
        return str(primary)
    # Fallback: any cmdline-tools version (but never tools/bin)
    for p in (base / "cmdline-tools").glob(f"*/bin/sdkmanager{ext}"):
        if p.exists():
            return str(p)
    return str(primary)   # return expected path even if missing (will error clearly)


def _run_sdkmanager(args, sdk, log_fn=None, timeout=900):
    """
    Run sdkmanager, piping 'y' answers so license prompts never block.
    Streams output line-by-line to log_fn if provided.
    Always uses cmdline-tools/latest (XML v4) — never the legacy tools/bin version.
    Injects JAVA_HOME + PATH so sdkmanager finds Java even if PATH not updated yet.
    Returns (success, full_output).
    """
    sdkmgr = _canonical_sdkmanager(sdk)
    # --sdk_root MUST come before the subcommand/packages
    cmd = [sdkmgr, f"--sdk_root={sdk}"] + args
    env = _sdk_env()
    if log_fn:
        log_fn(f"  JAVA_HOME = {env.get('JAVA_HOME', '(not found)')}\n")
        log_fn(f"  $ {' '.join(cmd)}\n")
    flags = subprocess.CREATE_NO_WINDOW if IS_WIN else 0
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            creationflags=flags,
        )
        # Feed unlimited 'y' responses so license prompts never block
        try:
            proc.stdin.write("y\n" * 50)
            proc.stdin.close()
        except Exception:
            pass

        lines = []
        for line in proc.stdout:
            line = line.rstrip()
            if line and log_fn:
                log_fn("    " + line + "\n")
            lines.append(line)

        proc.wait(timeout=timeout)
        full = "\n".join(lines)
        return proc.returncode == 0, full
    except subprocess.TimeoutExpired:
        proc.kill()
        return False, f"sdkmanager timed out after {timeout} seconds"
    except Exception as e:
        return False, str(e)


def _direct_download_platform_tools(sdk_path, log_fn=None):
    """
    Download platform-tools directly via Python urllib, bypassinging Java/sdkmanager.
    Uses Google's documented stable URL — no XML parsing needed.
    """
    url  = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
    dest = Path(sdk_path)
    tmp  = dest / "_pt_tmp.zip"
    if log_fn:
        log_fn(f"  Downloading platform-tools directly…\n")
    try:
        _urlretrieve(url, tmp, timeout=60)
        with zipfile.ZipFile(tmp, "r") as zf:
            zf.extractall(dest)
        tmp.unlink(missing_ok=True)
        # Google's platform-tools zip ships a remotePackage package.xml.
        # avdmanager needs localPackage format to recognise it as installed.
        _write_local_package_xml(
            dest / "platform-tools" / "package.xml",
            path_id="platform-tools",
            major=35, minor=0, micro=2,
            display="Android SDK Platform-Tools",
            license_ref="android-sdk-license",
            ns_type="ns5:genericDetailsType",
            extra_ns='xmlns:ns3="http://schemas.android.com/repository/android/generic/01"',
        )
        if log_fn:
            log_fn("  platform-tools installed ✅\n")
        return True
    except Exception as e:
        if log_fn:
            log_fn(f"  ❌  platform-tools direct download failed: {e}\n")
        tmp.unlink(missing_ok=True)
        return False


def _pe_machine_type(exe_path: str) -> int:
    """Read Windows PE machine type. Returns 0xAA64 for ARM64, 0x8664 for x64, 0 on error."""
    try:
        with open(exe_path, "rb") as f:
            if f.read(2) != b"MZ":
                return 0
            f.seek(0x3C)
            pe_off = int.from_bytes(f.read(4), "little")
            f.seek(pe_off)
            if f.read(4) != b"PE\x00\x00":
                return 0
            return int.from_bytes(f.read(2), "little")
    except Exception:
        return 0


MUMU_DOWNLOAD_URL = "https://www.mumuplayer.com/windows-arm.html"

def _find_mumu_manager():
    """Return path to MuMuManager.exe, or None.

    MuMuPlayer 12 installs MuMuManager.exe inside the nx_main subfolder:
      {install_root}\\nx_main\\MuMuManager.exe
    """
    if not IS_WIN:
        return None
    candidates = []
    for base_var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(base_var, "")
        if not base:
            continue
        for folder in ("Netease\\MuMuPlayer-12.0", "Netease\\MuMuPlayer",
                       "MuMuPlayer-12.0", "MuMuPlayer"):
            root = Path(base) / folder
            candidates.append(root / "nx_main" / "MuMuManager.exe")
            candidates.append(root / "shell"   / "MuMuManager.exe")
            candidates.append(root / "MuMuManager.exe")
    for p in candidates:
        if p.exists():
            return p
    return None


def _find_mumu_player():
    """Return the MuMuPlayer install root (contains MuMuPlayer.exe), or None."""
    mgr = _find_mumu_manager()
    if mgr:
        if mgr.parent.name in ("nx_main", "shell"):
            return mgr.parent.parent
        return mgr.parent
    return None


def _mumu_run(mgr_path, *args, timeout=15):
    """Run MuMuManager.exe with args, return (ok, stdout_str)."""
    flags = subprocess.CREATE_NO_WINDOW if IS_WIN else 0
    try:
        r = subprocess.run(
            [str(mgr_path)] + list(args),
            capture_output=True, text=True, timeout=timeout,
            creationflags=flags,
        )
        return r.returncode == 0, r.stdout.strip()
    except Exception as e:
        return False, str(e)


def _mumu_get_instances(mgr_path):
    """Return list of dicts: {index, name, adb_serial, started}.

    Uses MuMuManager.exe info --vmindex all
    JSON can be a single dict (one instance) or a dict-of-dicts keyed by index.
    """
    ok, out = _mumu_run(mgr_path, "info", "-v", "all", timeout=10)
    if not ok or not out:
        return []
    try:
        data = json.loads(out)
    except Exception:
        return []

    instances = []

    def _parse_one(d):
        if not isinstance(d, dict):
            return
        ip   = d.get("adb_host_ip", "127.0.0.1")
        port = d.get("adb_port")
        if port is None:
            return
        instances.append({
            "index":      d.get("index", 0),
            "name":       d.get("name", f"MuMu-{d.get('index', 0)}"),
            "adb_serial": f"{ip}:{port}",
            "started":    bool(d.get("is_android_started")),
        })

    if isinstance(data, dict):
        if "index" in data:
            _parse_one(data)
        else:
            for v in data.values():
                _parse_one(v)
    elif isinstance(data, list):
        for item in data:
            _parse_one(item)

    return sorted(instances, key=lambda x: x["index"])


def _mumu_launch(mgr_path, index, log_fn=None):
    """Launch a MuMuPlayer instance by index."""
    if log_fn:
        log_fn(f"  Launching MuMu instance {index}…\n")
    ok, out = _mumu_run(mgr_path, "control", "-v", str(index), "launch", timeout=30)
    return ok


def _connect_mumu_phones(count=5, log_fn=None):
    """Connect to MuMuPlayer instances via MuMuManager API + adb connect.

    Returns list of connected ADB serials.
    Fallback: if MuMuManager not found, try standard ports 16384+32*i.
    """
    mgr = _find_mumu_manager()
    connected = []

    if mgr:
        instances = _mumu_get_instances(mgr)
        for inst in instances:
            serial = inst["adb_serial"]
            out = adb("connect", serial, timeout=8)
            if "connected" in out.lower() or "already" in out.lower():
                check = adb("shell", "echo", "ok", serial=serial, timeout=6)
                if check.strip() == "ok":
                    connected.append(serial)
                    if log_fn:
                        log_fn(f"  ✅ {inst['name']}: {serial}\n")
        return connected

    # Fallback: try fixed ports
    base_port = 16384
    for i in range(count):
        serial = f"127.0.0.1:{base_port + i * 32}"
        out = adb("connect", serial, timeout=6)
        if "connected" in out.lower() or "already" in out.lower():
            check = adb("shell", "echo", "ok", serial=serial, timeout=6)
            if check.strip() == "ok":
                connected.append(serial)
                if log_fn:
                    log_fn(f"  ✅ MuMu fallback port: {serial}\n")
    return connected


def _direct_download_emulator(sdk_path, log_fn=None):
    """
    Download emulator directly via Python urllib, bypassing Java/sdkmanager.
    On ARM64 Windows: preserves an existing ARM64 emulator binary and tries to
    download the ARM64 build before falling back to x64 (which cannot run arm64-v8a).
    """
    import xml.etree.ElementTree as ET
    REPO_XML = "https://dl.google.com/android/repository/repository2-3.xml"
    BASE_URL = "https://dl.google.com/android/repository/"

    host_is_arm64 = IS_WIN and "arm64" in _machine_arch()

    # If an ARM64 emulator binary already exists (e.g. installed by Android Studio),
    # don't overwrite it with an x64 binary that can't run arm64-v8a images.
    emu_exe = Path(sdk_path) / "emulator" / "emulator.exe"
    if host_is_arm64 and emu_exe.exists():
        machine = _pe_machine_type(str(emu_exe))
        if machine == 0xAA64:
            if log_fn:
                log_fn("  ARM64 emulator already installed — skipping download ✅\n")
            return True
        if log_fn:
            log_fn("  Existing emulator is x64; will try to replace with ARM64 build.\n")

    if log_fn:
        log_fn("  Fetching SDK catalog via Python (bypassing Java network)…\n")
    try:
        with urllib.request.urlopen(REPO_XML, timeout=30) as r:
            xml_data = r.read().decode("utf-8")
    except Exception as e:
        if log_fn:
            log_fn(f"  ❌  Cannot reach dl.google.com from Python either: {e}\n")
            log_fn("      Your network is blocking Google entirely.\n")
            log_fn("      Check VPN, proxy, or router DNS settings.\n")
        return False

    x64_url = None
    arm64_url = None
    try:
        root = ET.fromstring(xml_data)
        for pkg in root.iter():
            if pkg.get("path") == "emulator":
                for archive in pkg.iter():
                    tag = archive.tag.split("}")[-1] if "}" in archive.tag else archive.tag
                    if tag == "url":
                        val = (archive.text or "").strip()
                        if val.endswith(".zip") and "windows" in val:
                            if "aarch64" in val or "arm64" in val:
                                arm64_url = BASE_URL + val
                            elif "x64" in val:
                                x64_url = BASE_URL + val
                            elif not x64_url:
                                x64_url = BASE_URL + val
                if x64_url or arm64_url:
                    break
    except ET.ParseError:
        pass

    if not x64_url and not arm64_url:
        all_win = re.findall(r"emulator-windows[^\"<\s]+\.zip", xml_data)
        for m in all_win:
            if "aarch64" in m or "arm64" in m:
                arm64_url = BASE_URL + m
            elif "x64" in m and not x64_url:
                x64_url = BASE_URL + m
        if not x64_url and not arm64_url and all_win:
            x64_url = BASE_URL + all_win[-1]

    # On ARM64 Windows: try substituting aarch64 into x64 URL if no ARM64 found in XML.
    # Google sometimes publishes ARM64 builds at the same build number under a different filename.
    if host_is_arm64 and not arm64_url and x64_url:
        candidate = re.sub(r"emulator-windows_x64-", "emulator-windows_aarch64-", x64_url)
        if candidate != x64_url:
            try:
                req = urllib.request.Request(candidate, method="HEAD")
                with urllib.request.urlopen(req, timeout=10):
                    arm64_url = candidate
                    if log_fn:
                        log_fn(f"  ARM64 emulator build found on CDN: {arm64_url}\n")
            except Exception:
                pass

    if host_is_arm64:
        if not arm64_url:
            if log_fn:
                log_fn(
                    "  ❌  Google does not publish a Windows ARM64 Android Emulator.\n"
                    "     Even Android Studio for Windows ARM64 downloads only the x64\n"
                    "     emulator — there is no ARM64 emulator binary available from Google.\n\n"
                    "  ✅  Use MuMuPlayer for Windows ARM instead:\n"
                    "     MuMuPlayer is a free Android emulator with native ARM64 Windows\n"
                    "     support, multi-instance, and full ADB access — exactly what\n"
                    "     CPharm needs.\n\n"
                    "  How to set up (wizard handles connection automatically):\n"
                    "     1. Download MuMuPlayer for Windows ARM:\n"
                    f"        {MUMU_DOWNLOAD_URL}\n"
                    "     2. Install MuMuPlayer ARM and open it.\n"
                    "     3. Use MuMuPlayer's multi-instance manager to create your phones.\n"
                    "     4. Keep MuMuPlayer running, then go to Step 5 in this wizard.\n"
                    "        The wizard will detect and connect to all running MuMu instances.\n"
                )
            return False
        emulator_url = arm64_url
    else:
        if not x64_url:
            if log_fn:
                log_fn("  ❌  Emulator URL not found in repository XML.\n")
            return False
        emulator_url = x64_url

    if log_fn:
        log_fn(f"  Emulator URL: {emulator_url}\n")
        log_fn("  Downloading emulator (~300 MB)…\n")

    tmp = Path(sdk_path) / "_emu_tmp.zip"
    try:
        _urlretrieve(emulator_url, tmp, timeout=120)
        emu_dest = Path(sdk_path) / "emulator"
        if emu_dest.exists():
            shutil.rmtree(emu_dest)
        with zipfile.ZipFile(tmp, "r") as zf:
            zf.extractall(Path(sdk_path))
        tmp.unlink(missing_ok=True)
        _write_emulator_package_xml(emu_dest)
        _write_sdk_licenses(sdk_path)
        if log_fn:
            log_fn("  emulator installed ✅\n")
        return True
    except Exception as e:
        if log_fn:
            log_fn(f"  ❌  Emulator direct download failed: {e}\n")
        tmp.unlink(missing_ok=True)
        return False


def _write_local_package_xml(path: Path, *, path_id, major, minor, micro,
                              display, license_ref, ns_type, extra_ns="",
                              extra_details=""):
    """Write a localPackage package.xml that sdkmanager and avdmanager accept.

    Structure must match Google's official package XML format exactly,
    including all xmlns namespace declarations and the <dependencies> block.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    # Build the full namespace string — avdmanager validates these
    base_ns = 'xmlns:ns2="http://schemas.android.com/repository/android/common/01"'
    addon2  = 'xmlns:ns3="http://schemas.android.com/sdk/android/repo/addon2/01"'
    sysimg  = 'xmlns:ns4="http://schemas.android.com/sdk/android/repo/sys-img2/01"'
    generic = 'xmlns:ns5="http://schemas.android.com/repository/android/generic/01"'
    repo2   = 'xmlns:ns6="http://schemas.android.com/repository/android/repository2/01"'
    xsi     = 'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
    all_ns  = f"{base_ns} {addon2} {sysimg} {generic} {repo2} {xsi}"

    path.write_text(
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<ns2:repository\n'
        f'    {all_ns}>\n'
        f'  <license id="{license_ref}" type="text">Android Software Development Kit License Agreement\n'
        f'</license>\n'
        f'  <localPackage path="{path_id}" obsolete="false">\n'
        f'    <type-details xsi:type="{ns_type}">{extra_details}</type-details>\n'
        f'    <revision>\n'
        f'      <major>{major}</major><minor>{minor}</minor><micro>{micro}</micro>\n'
        f'    </revision>\n'
        f'    <display-name>{display}</display-name>\n'
        f'    <uses-license ref="{license_ref}"/>\n'
        f'  </localPackage>\n'
        f'</ns2:repository>\n',
        encoding="utf-8",
    )


def _write_emulator_package_xml(emu_dir: Path):
    """Write the official emulator top-level package.xml with all namespaces and dependencies.

    This is the exact format Google ships in sdk/emulator/package.xml.
    avdmanager validates both the namespace declarations AND the dependencies block.
    """
    pkg_xml = emu_dir / "package.xml"
    pkg_xml.write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<ns2:repository '
        'xmlns:ns2="http://schemas.android.com/repository/android/common/01" '
        'xmlns:ns3="http://schemas.android.com/sdk/android/repo/addon2/01" '
        'xmlns:ns4="http://schemas.android.com/sdk/android/repo/sys-img2/01" '
        'xmlns:ns5="http://schemas.android.com/repository/android/generic/01" '
        'xmlns:ns6="http://schemas.android.com/repository/android/repository2/01" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        '<license id="android-sdk-license" type="text">Android Software Development Kit License Agreement\n'
        '</license>'
        '<localPackage path="emulator" obsolete="false">'
        '<type-details xsi:type="ns5:genericDetailsType"/>'
        '<revision><major>36</major><minor>6</minor><micro>4</micro></revision>'
        '<display-name>Android Emulator</display-name>'
        '<uses-license ref="android-sdk-license"/>'
        '<dependencies>'
        '<dependency path="patcher;v4"/>'
        '<dependency path="tools"><min-revision><major>25</major><minor>3</minor></min-revision></dependency>'
        '</dependencies>'
        '</localPackage>'
        '</ns2:repository>',
        encoding="utf-8",
    )


def _write_sdk_licenses(sdk: str):
    """
    Write Android SDK license files to sdk/licenses/.
    avdmanager validates <uses-license ref="..."/> against these files at startup.
    Without them it logs "package.XML parsing problem undefined ID <license-name>".
    SHA-1 hashes are the official values from Google's SDK license agreements.
    """
    lic_dir = Path(sdk) / "licenses"
    lic_dir.mkdir(parents=True, exist_ok=True)
    licenses = {
        "android-sdk-license": (
            "\n8933bad161af4178b1185d1a37fbf41ea5269c55\n"
            "d56f5187479451eabf01fb78af6dfcb131a6481e\n"
            "24333f8a63b6825ea9c5514f83c2829b004d1fee"
        ),
        "android-sdk-preview-license": (
            "\n84831b9409646a918e30573bab4c9c91346d8abd"
        ),
        "android-sdk-arm-dbt-license": (
            "\n859f317696f67ef3d7f30a50a5560e7834b43903"
        ),
    }
    for name, content in licenses.items():
        (lic_dir / name).write_text(content, encoding="utf-8")


def _direct_download_system_image(arch: str, sdk_path: str, log_fn=None) -> bool:
    """
    Download the Android 34 google_apis system image directly via Python urllib,
    bypassinging Java/sdkmanager.  Parses Google's repository XML to find the URL.
    """
    import xml.etree.ElementTree as ET
    REPO_XML = "https://dl.google.com/android/repository/sys-img/google_apis/sys-img2-3.xml"
    BASE_URL = "https://dl.google.com/android/repository/sys-img/google_apis/"

    if log_fn:
        log_fn("  Fetching system image catalog…\n")
    try:
        with urllib.request.urlopen(REPO_XML, timeout=30) as r:
            xml_data = r.read().decode("utf-8")
    except Exception as e:
        if log_fn:
            log_fn(f"  ❌  Cannot reach system image catalog: {e}\n")
        return False

    image_url = None
    try:
        root = ET.fromstring(xml_data)
        for pkg in root.iter():
            tag = pkg.tag.split("}")[-1] if "}" in pkg.tag else pkg.tag
            if tag not in ("remotePackage", "package"):
                continue
            path_attr = pkg.get("path", "")
            if "android-34" not in path_attr or "google_apis" not in path_attr:
                continue
            if arch not in path_attr:
                continue
            for child in pkg.iter():
                ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                if ctag == "url":
                    val = (child.text or "").strip()
                    if val.endswith(".zip"):
                        image_url = BASE_URL + val
                        break
            if image_url:
                break
    except ET.ParseError:
        pass

    if not image_url:
        if log_fn:
            log_fn("  ❌  Could not locate system image URL in repository XML.\n")
        return False

    if log_fn:
        log_fn(f"  Downloading system image (~1 GB): {image_url}\n")

    dest = Path(sdk_path)
    tmp  = dest / "_sysimg_tmp.zip"
    img_dir = dest / "system-images" / "android-34" / "google_apis" / arch
    try:
        _urlretrieve(image_url, tmp, timeout=120)
        img_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(tmp, "r") as zf:
            for member in zf.namelist():
                parts = Path(member).parts
                if len(parts) > 1:
                    out_path = img_dir / Path(*parts[1:])
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    if not member.endswith("/"):
                        out_path.write_bytes(zf.read(member))
                elif parts:
                    out_path = img_dir / parts[0]
                    if not member.endswith("/"):
                        out_path.write_bytes(zf.read(member))
        tmp.unlink(missing_ok=True)
        TARGET_PATH = f"system-images;android-34;google_apis;{arch}"
        _simg_display = ("Google APIs Intel x86_64 Atom System Image"
                         if arch == "x86_64"
                         else "Google APIs ARM 64 v8a System Image")
        _simg_license = ("android-sdk-license"
                         if arch == "x86_64"
                         else "android-sdk-arm-dbt-license")
        _write_local_package_xml(
            img_dir / "package.xml",
            path_id=TARGET_PATH,
            major=14, minor=0, micro=0,
            display=_simg_display,
            license_ref=_simg_license,
            ns_type="ns4:sysImgDetailsType",
            extra_ns='xmlns:ns4="http://schemas.android.com/sdk/android/repo/sys-img2/01"',
            extra_details=(
                f"<ns4:api-level>34</ns4:api-level>"
                f"<ns4:tag><ns4:id>google_apis</ns4:id>"
                f"<ns4:display>Google APIs</ns4:display></ns4:tag>"
                f"<ns4:vendor><ns4:id>google</ns4:id>"
                f"<ns4:display>Google Inc.</ns4:display></ns4:vendor>"
                f"<ns4:abi>{arch}</ns4:abi>"
            ),
        )
        if log_fn:
            log_fn("  System image installed ✅\n")
        return True
    except Exception as e:
        if log_fn:
            log_fn(f"  ❌  Direct system image download failed: {e}\n")
        tmp.unlink(missing_ok=True)
        return False


def _add_firewall_exception_for_java() -> bool:
    """
    Add a Windows Firewall outbound allow rule for java.exe via PowerShell.
    Requires elevation (UAC prompt will appear).
    Returns True if the PowerShell command was launched successfully.
    """
    java_home = _find_java_home()
    if java_home:
        java_exe = str(Path(java_home) / "bin" / "java.exe")
    else:
        java_exe = "java.exe"

    ps_cmd = (
        f"New-NetFirewallRule -DisplayName 'CPharm Java' "
        f"-Direction Outbound -Action Allow "
        f"-Program '{java_exe}' -Enabled True"
    )
    try:
        subprocess.Popen(
            ["powershell", "-Command",
             f"Start-Process powershell -Verb RunAs -ArgumentList "
             f"'-NoProfile -Command {ps_cmd}'"],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return True
    except Exception:
        return False


def _ensure_emulator_meta(sdk, log_fn=None):
    """
    Install emulator device definitions (device-catalog.xml) so avdmanager can
    enumerate hardware profiles (pixel_6, etc.) and AVD creation succeeds.

    Without this, 'avdmanager list device' returns empty and AVD creation fails
    with: "CRITICAL: emulator package must be installed!"

    Strategy (in order):
      1. Android Studio: copy device-catalog.xml from Android Studio's own copy
         of the emulator, if Android Studio is installed.
      2. sdkmanager: run sdkmanager "emulator" (the authoritative install path).
         This downloads and installs the emulator package including device-catalog.xml.
      3. Direct Python download: if sdkmanager fails (network blocked), download
         the emulator ZIP directly from Google's repo XML.
    After each path, verify emulator/meta/device-catalog.xml exists.
    """
    emu_dir = Path(sdk) / "emulator"
    catalog = emu_dir / "meta" / "device-catalog.xml"
    host_is_arm64 = IS_WIN and "arm64" in _machine_arch()

    # ── ARM64-specific binary check (runs even if catalog already exists) ──
    # Step 2 (SDK install) can write an x64 emulator.exe + catalog.  The
    # catalog check below would short-circuit before replacing the x64 binary,
    # causing "CPU Architecture not supported" crashes at boot.  We handle this
    # separately so catalog presence and binary architecture are independent.
    needs_arm64_copy = False
    if host_is_arm64:
        emu_exe = emu_dir / "emulator.exe"
        if emu_exe.exists():
            machine = _pe_machine_type(str(emu_exe))
            if machine == 0xAA64:
                if log_fn:
                    log_fn("  ARM64 emulator binary confirmed ✓\n")
            else:
                if log_fn:
                    log_fn("  ⚠  emulator.exe is x64 — will replace with ARM64 build.\n")
                needs_arm64_copy = True
        else:
            needs_arm64_copy = True  # no binary at all

    # Already present and no ARM64 binary replacement needed — nothing to do
    if catalog.exists() and not needs_arm64_copy:
        if log_fn:
            log_fn("  Emulator device metadata already present.\n")
        return

    if log_fn:
        log_fn("  Installing emulator device definitions (device-catalog.xml)…\n")

    installed = False

    # ── Path 1: Android Studio bundled emulator ─────────────────────────
    # Android Studio ships a complete copy of the emulator with device-catalog.xml.
    # On ARM64 Windows, Android Studio's emulator.exe IS the ARM64 native binary —
    # copy the entire emulator directory so arm64-v8a images boot natively.
    studio_emulator = None
    search_roots = [
        Path(os.environ.get("PROGRAMFILES",      "")) / "Android",
        Path(os.environ.get("LOCALAPPDATA",      "")) / "Programs" / "Android",
        Path(os.environ.get("PROGRAMFILES",      "")) / "Android Studio",
        Path(os.environ.get("LOCALAPPDATA",      "")) / "Programs" / "Android Studio",
        Path(os.environ.get("LOCALAPPDATA",      "")) / "Google",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Android",
        Path(os.environ.get("LOCALAPPDATA",      "")) / "Android",
    ]
    for search_root in search_roots:
        if not search_root.exists():
            continue
        # Glob for any "Android Studio*" subdirectory (handles Studio, Studio3, etc.)
        candidates = sorted(search_root.glob("Android Studio*"), reverse=True)
        if not candidates and search_root.name.startswith("Android Studio"):
            candidates = [search_root]
        for studio_root in candidates:
            candidate = studio_root / "emulator"
            if (candidate / "emulator.exe").exists():
                studio_emulator = candidate
                break
        if studio_emulator:
            break

    if studio_emulator:
        if log_fn:
            log_fn(f"  Found Android Studio emulator:\n  {studio_emulator}\n")
        try:
            # On ARM64 Windows, Android Studio's emulator.exe is the ARM64 native binary.
            # Copy the entire emulator directory so arm64-v8a guests boot natively.
            as_exe = studio_emulator / "emulator.exe"
            if host_is_arm64 and as_exe.exists() and _pe_machine_type(str(as_exe)) == 0xAA64:
                if log_fn:
                    log_fn("  ARM64 emulator binary found in Android Studio — copying full emulator directory…\n")
                if emu_dir.exists():
                    shutil.rmtree(emu_dir)
                shutil.copytree(str(studio_emulator), str(emu_dir))
                if log_fn:
                    log_fn("  ARM64 emulator installed from Android Studio ✅\n")
            else:
                # x64 host or AS binary is x64 — just copy device-catalog.xml
                meta_dest = emu_dir / "meta"
                meta_dest.mkdir(parents=True, exist_ok=True)
                shutil.copy2(
                    studio_emulator / "meta" / "device-catalog.xml",
                    meta_dest / "device-catalog.xml",
                )
                if log_fn:
                    log_fn("  Copied device definitions from Android Studio ✅\n")
            _write_emulator_package_xml(emu_dir)
            _write_local_package_xml(
                emu_dir / "meta" / "package.xml",
                path_id="emulator;meta",
                major=36, minor=6, micro=4,
                display="Android Emulator Device Metadata",
                license_ref="android-sdk-license",
                ns_type="ns5:genericDetailsType",
                extra_ns='xmlns:ns3="http://schemas.android.com/repository/android/generic/01"',
            )
            installed = True
        except Exception as e:
            if log_fn:
                log_fn(f"  Could not copy from Android Studio: {e}\n")

    # ── Path 2: sdkmanager "emulator" (x64 hosts only) ──────────────────
    # On ARM64 Windows, sdkmanager always downloads the x64 emulator binary which
    # cannot run arm64-v8a guests and crashes immediately at boot.  Skip straight
    # to the direct-download path which parses the repo XML for the aarch64 build.
    if not installed and not (host_is_arm64 and needs_arm64_copy):
        if log_fn:
            log_fn("  Trying sdkmanager 'emulator' install (authoritative)…\n")

        ok, out = _run_sdkmanager(["emulator"], sdk, log_fn=log_fn, timeout=300)
        if catalog.exists():
            if log_fn:
                log_fn("  Emulator + device metadata installed via sdkmanager ✅\n")
            installed = True
        else:
            if log_fn:
                log_fn(f"  sdkmanager 'emulator' did not produce device-catalog.xml.\n"
                       f"  Output preview: {out[:300]}\n")

    # ── Path 3: Direct Python download ──────────────────────────────────
    # Primary path on ARM64 Windows (skips x64-only sdkmanager above).
    # _direct_download_emulator parses the repo XML for aarch64 builds first.
    if not installed:
        if log_fn:
            log_fn("  Falling back to direct Python download of emulator.\n"
                   "  (This bypasses Java's network stack.)\n")
        _direct_download_emulator(sdk, log_fn=log_fn)

    # ── Final verdict ────────────────────────────────────────────────────
    if catalog.exists():
        if log_fn:
            log_fn("  Emulator device metadata installed ✅\n")
    else:
        if log_fn:
            log_fn("  ⚠  device-catalog.xml still missing after all fallbacks.\n"
                   "    AVD creation will fail. Consider installing Android Studio\n"
                   "    to get the full emulator package with device definitions.\n")

    # On ARM64 Windows, verify the final emulator binary is actually ARM64.
    if host_is_arm64:
        emu_exe = emu_dir / "emulator.exe"
        if emu_exe.exists() and _pe_machine_type(str(emu_exe)) != 0xAA64:
            if log_fn:
                log_fn(
                    "  ❌  emulator.exe is still x64 after all install attempts.\n"
                    "     Google does not publish a Windows ARM64 Android Emulator —\n"
                    "     even Android Studio ARM64 only downloads the x64 emulator via\n"
                    "     its SDK Manager. There is no ARM64 AVD emulator from Google.\n\n"
                    "  ✅  Use MuMuPlayer for Windows ARM instead:\n"
                    "     Free, native ARM64, multi-instance, full ADB — skip AVD entirely.\n"
                    f"     Download: {MUMU_DOWNLOAD_URL}\n"
                    "     After installing, open MuMuPlayer, create instances, then go\n"
                    "     directly to Step 5 — the wizard connects automatically.\n"
                )


def create_avd(name, log_fn=None):
    sdk = state.get("sdk_path") or find_sdk()
    if not sdk:
        return False, "Android SDK not found"

    # avdmanager lives beside sdkmanager in cmdline-tools/latest/bin/
    sdkmgr_path = _canonical_sdkmanager(sdk)
    ext    = ".bat" if IS_WIN else ""
    avdmgr = str(Path(sdkmgr_path).parent / f"avdmanager{ext}")
    arch   = _machine_arch()
    # On ARM64 Windows: arm64-v8a — the native ARM64 emulator binary runs
    # arm64-v8a guests directly without any hypervisor requirement, avoiding
    # the Intel/AMD CPU check that blocks x86_64 guests on Snapdragon.
    # On x64 Windows: x86_64 system images (WHPX-accelerated).
    # _machine_arch() already returns the correct ABI — no override needed.
    image  = f"system-images;android-34;google_apis;{arch}"

    java_home = _find_java_home()
    if log_fn:
        log_fn(f"  Host architecture : {arch}\n")
        log_fn(f"  System image      : {image}\n")
        log_fn(f"  SDK root          : {sdk}\n")
        log_fn(f"  sdkmanager        : {_canonical_sdkmanager(sdk)}\n")
        log_fn(f"  avdmanager        : {avdmgr}\n")
        log_fn(f"  Java home         : {java_home or '❌ NOT FOUND'}\n\n")

    if not java_home:
        return False, (
            "Java not found.\n\n"
            "Go back to Step 1 (Install Tools) and make sure Java installed successfully,\n"
            "then come back and try again."
        )

    # ── write license files + accept all licenses ─────────────────────────────
    if log_fn:
        log_fn("  Writing SDK license files…\n")
    _write_sdk_licenses(sdk)
    if log_fn:
        log_fn("  Accepting SDK licenses…\n")
    _run_sdkmanager(["--licenses"], sdk, log_fn=None, timeout=60)

    # ── install system image ──────────────────────────────────────────────────
    img_path = Path(sdk) / "system-images" / "android-34" / "google_apis" / arch
    # system.img may be gzipped (system.img.gz) in downloaded zips — check both
    img_ready = (img_path / "system.img.gz").exists() or (img_path / "system.img").exists()
    if img_ready:
        # Ensure package.xml is in localPackage format — Google's zip ships a
        # remotePackage XML that avdmanager cannot use; re-write it if needed.
        pkg_xml = img_path / "package.xml"
        try:
            if not pkg_xml.exists() or "<localPackage" not in pkg_xml.read_text(encoding="utf-8", errors="ignore"):
                TARGET_PATH = f"system-images;android-34;google_apis;{arch}"
                _img_display = ("Google APIs Intel x86_64 Atom System Image"
                                if arch == "x86_64"
                                else "Google APIs ARM 64 v8a System Image")
                _img_license = ("android-sdk-license"
                                if arch == "x86_64"
                                else "android-sdk-arm-dbt-license")
                _write_local_package_xml(
                    pkg_xml,
                    path_id=TARGET_PATH,
                    major=14, minor=0, micro=0,
                    display=_img_display,
                    license_ref=_img_license,
                    ns_type="ns4:sysImgDetailsType",
                    extra_ns='xmlns:ns4="http://schemas.android.com/sdk/android/repo/sys-img2/01"',
                    extra_details=(
                        f"<ns4:api-level>34</ns4:api-level>"
                        f"<ns4:tag><ns4:id>google_apis</ns4:id>"
                        f"<ns4:display>Google APIs</ns4:display></ns4:tag>"
                        f"<ns4:vendor><ns4:id>google</ns4:id>"
                        f"<ns4:display>Google Inc.</ns4:display></ns4:vendor>"
                        f"<ns4:abi>{arch}</ns4:abi>"
                    ),
                )
                if log_fn:
                    log_fn("  Rewrote package.xml to localPackage format.\n")
        except Exception as e:
            if log_fn:
                log_fn(f"  ⚠  Could not rewrite package.xml: {e}\n")
        if log_fn:
            log_fn(f"  System image already installed at {img_path} — skipping download.\n")
    else:
        if log_fn:
            log_fn(f"  Installing Android 14 image — this downloads ~1 GB, please wait…\n")
        ok, out = _run_sdkmanager([image], sdk, log_fn=log_fn, timeout=1200)

        if not ok:
            if (img_path / "system.img.gz").exists() or (img_path / "system.img").exists():
                if log_fn:
                    log_fn("  Image present on disk — continuing despite sdkmanager exit code.\n")
            else:
                if log_fn:
                    log_fn("  sdkmanager failed — trying direct download fallback…\n")
                ok2 = _direct_download_system_image(arch, sdk, log_fn=log_fn)
                if not ok2:
                    return False, f"sdkmanager failed and direct download also failed.\nLast sdkmanager output:\n{out[-400:]}"

    # ── create the AVD ────────────────────────────────────────────────────────
    if log_fn:
        log_fn(f"\n  Creating AVD: {name}…\n")

    flags = subprocess.CREATE_NO_WINDOW if IS_WIN else 0

    # Ensure emulator metadata (device definitions) is present before calling avdmanager.
    # Without emulator/meta/device-catalog.xml, 'avdmanager list device' returns empty
    # and AVD creation fails with "emulator package must be installed!"
    _ensure_emulator_meta(sdk, log_fn)

    # Discover device IDs that are actually present in this SDK installation.
    # Never guess IDs — avdmanager -d is case-sensitive and varies between SDK versions.
    try:
        r = subprocess.run(
            [avdmgr, "list", "device", "-c"],
            capture_output=True, text=True, timeout=30,
            env=_sdk_env(), creationflags=flags,
        )
        all_ids = [l.strip() for l in r.stdout.splitlines() if l.strip()]
    except Exception:
        all_ids = []

    # Preferred device IDs in priority order — use the first one that exists in this SDK.
    preferred = [
        "pixel_9", "pixel_9_pro", "pixel_8", "pixel_8_pro",
        "pixel_7", "pixel_7_pro", "pixel_6", "pixel_6_pro",
        "pixel_5", "pixel_4", "pixel_3", "pixel_2",
        "pixel_xl", "pixel", "nexus_6p", "nexus_6", "nexus_5",
    ]
    device_profiles = [d for d in preferred if d in all_ids]

    # If none of our preferred IDs exist, fall back to the first available device
    if not device_profiles and all_ids:
        device_profiles = [all_ids[0]]

    if not device_profiles:
        device_profiles = ["pixel_6"]   # last resort — avdmanager will give a clear error

    if log_fn:
        log_fn(f"  Available device IDs (first 5): {all_ids[:5]}\n")
        log_fn(f"  Will try: {device_profiles}\n")

    if not Path(avdmgr).exists():
        return False, (
            f"avdmanager not found at: {avdmgr}\n"
            "Go back to Step 2 (Android SDK) and reinstall the SDK."
        )

    last_err = ""
    for device in device_profiles:
        if log_fn:
            log_fn(f"  Trying device profile: {device}\n")
        try:
            proc = subprocess.Popen(
                [avdmgr, "create", "avd", "-n", name,
                 "-k", image, "-d", device, "--force"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=_sdk_env(),
                creationflags=flags,
            )
        except OSError as e:
            return False, f"Failed to launch avdmanager: {e}"

        try:
            stdout, stderr = proc.communicate(input="no\n", timeout=120)
        except subprocess.TimeoutExpired:
            proc.kill()
            return False, "avdmanager timed out"

        combined = (stdout + stderr).strip()
        if log_fn and combined:
            log_fn(combined + "\n")

        if proc.returncode == 0:
            if log_fn:
                log_fn(f"  ✅  {name} created (device: {device}).\n")
            return True, ""
        last_err = combined
        if log_fn:
            log_fn(f"  Device profile '{device}' failed — trying next…\n")

    return False, last_err or "avdmanager failed with all device profiles"


def start_emulator(avd_name, port):
    emu   = sdk_tool("emulator")
    env   = _sdk_env()

    # On ARM64 Windows: arm64-v8a guest runs natively on the ARM64 emulator binary —
    #   no hypervisor required, no Intel/AMD CPU check triggered.
    # On x64 Windows: x86_64 guest needs -accel auto (WHPX or HAXM).
    # -gpu swiftshader_indirect — Adreno Vulkan lacks VK_KHR_external_memory so
    #   host GPU passthrough is unavailable; software GLES is required either way.
    # -no-snapshot-save / -no-boot-anim / -no-audio / -wipe-data — speed + clean state
    host_is_arm64 = IS_WIN and "arm64" in _machine_arch()
    base_args = ["-gpu", "swiftshader_indirect",
                 "-no-snapshot-save", "-no-boot-anim", "-no-audio", "-wipe-data"]
    accel_args = base_args if host_is_arm64 else ["-accel", "auto"] + base_args

    # CREATE_NO_WINDOW hides the console window (no blank/flashing terminal).
    # File-handle inheritance works fine with CREATE_NO_WINDOW — the inheritance
    # problem only affects CREATE_NEW_CONSOLE. The emulator's graphical phone
    # window is a separate Win32 window and still appears normally.
    launch_flags = subprocess.CREATE_NO_WINDOW if IS_WIN else 0

    log_path = None
    try:
        log_dir = Path(os.environ.get("TEMP", "/tmp"))
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"cpharm_emu_{avd_name}.log"
        log_file = open(log_path, "w")
    except Exception:
        log_file = subprocess.DEVNULL

    # On Windows, emulator may be a .bat wrapper (emulator.bat/emulator.cmd).
    # Popen with a list on Windows does NOT automatically run .bat files —
    # we must use shell=True so cmd.exe handles them.
    argv = [emu, "-avd", avd_name, "-port", str(port)] + accel_args
    cmd_str = subprocess.list2cmdline(argv)

    popen_kwargs = dict(env=env, creationflags=launch_flags, shell=True)
    if log_file is not None:
        popen_kwargs["stdout"] = log_file
        popen_kwargs["stderr"] = log_file

    proc = subprocess.Popen(cmd_str, **popen_kwargs)
    if log_file and log_file is not subprocess.DEVNULL:
        proc._log_file = log_file
    return proc, log_path


def wait_for_boot(serial, timeout=300, log_fn=None):
    adb("wait-for-device", serial=serial, timeout=timeout)
    deadline = time.time() + timeout
    start    = time.time()
    last_log = start
    while time.time() < deadline:
        out = adb("shell", "getprop", "sys.boot_completed", serial=serial)
        if out.strip() == "1":
            return True
        now = time.time()
        if log_fn and now - last_log >= 30:
            elapsed = int(now - start)
            log_fn(f"    still booting... ({elapsed}s elapsed)\n")
            last_log = now
        time.sleep(3)
    return False


def rotate_android_id(serial):
    new_id = format(hash(serial + str(time.time())) & 0xFFFFFFFFFFFFFFFF, "016x")
    adb("shell", "settings", "put", "secure", "android_id", new_id, serial=serial)
    return new_id


def _serial_to_idx(serial: str) -> int:
    try:
        return (int(serial.split("-")[1]) - 5554) // 2
    except (IndexError, ValueError):
        return 0


def _try_newnym(serial: str) -> bool:
    import socket
    idx       = _serial_to_idx(serial)
    ctrl_port = 10050 + idx
    try:
        with socket.create_connection(("127.0.0.1", ctrl_port), timeout=2) as s:
            s.sendall(b'AUTHENTICATE ""\r\n')
            s.recv(256)
            s.sendall(b"SIGNAL NEWNYM\r\n")
            return b"250" in s.recv(256)
    except OSError:
        return False


def setup_chrome(serial):
    """
    Kill Chrome first-run experience so URLs open immediately every time.

    Fresh AVDs show ToS / sync screens that swallow the target URL.
    Writing the chrome-command-line flags file disables all of that.
    Works without root — shell user can write to /data/local/tmp.
    """
    flags = (
        "chrome --disable-first-run-experience "
        "--no-default-browser-check --no-first-run "
        "--disable-fre --disable-sync-by-default"
    )
    adb("shell", "sh", "-c",
        f"mkdir -p /data/local/tmp && "
        f"echo '{flags}' > /data/local/tmp/chrome-command-line",
        serial=serial)
    adb("shell", "am", "force-stop", "com.android.chrome", serial=serial)
    time.sleep(0.8)
    # Open about:blank so Chrome initialises with the new flags
    adb("shell", "am", "start",
        "-a", "android.intent.action.VIEW",
        "-d", "about:blank",
        "-n", "com.android.chrome/com.google.android.apps.chrome.Main",
        "--ez", "create_new_tab", "true",
        serial=serial)
    time.sleep(3)
    # Dismiss any residual dialog (ToS accept / "No thanks" / sign-in)
    for _ in range(3):
        adb("shell", "input", "keyevent", "4", serial=serial)
        time.sleep(0.4)
    adb("shell", "am", "force-stop", "com.android.chrome", serial=serial)
    return True


def chrome_open_url(serial, url):
    """Open a URL directly in Chrome, bypassing browser chooser."""
    adb("shell", "am", "start",
        "-a", "android.intent.action.VIEW",
        "-d", url,
        "-n", "com.android.chrome/com.google.android.apps.chrome.Main",
        "--ez", "create_new_tab", "true",
        serial=serial)


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
            chrome_open_url(serial, step.get("url", ""))
            time.sleep(0.8)
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
            _try_newnym(serial)
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
                 text="Virtual Android phones on your Snapdragon Windows laptop.\n"
                      "The wizard sets everything up automatically — just click Next on each screen.",
                 font=("Segoe UI", 12), bg=BG, fg=T2, justify="center").pack(pady=(0, 16))

        # Architecture diagram — canvas only; static content is packed below, NOT inside the callback
        c = tk.Canvas(self, bg=BG2, height=140,
                      highlightthickness=1, highlightbackground=BORDER)
        c.pack(fill="x", padx=16, pady=(0, 14))
        c.bind("<Configure>", lambda e: self._draw_diagram(c))

        # Static "What you get:" section — built once here, never inside _draw_diagram
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

    def _draw_diagram(self, c):
        c.delete("all")
        w = c.winfo_width() or 700
        boxes = [
            (ACCENT,  "Android\nSDK Tools"),
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


# ─── page 2: prerequisites auto-install ──────────────────────────────────────

def _urlretrieve(url, dest, hook=None, timeout=60):
    """Download with timeout — never hangs forever."""
    import socket
    old = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        urllib.request.urlretrieve(url, dest, hook)
    finally:
        socket.setdefaulttimeout(old)

def _fetch_latest_tor_url() -> str:
    try:
        with urllib.request.urlopen(
            "https://dist.torproject.org/torbrowser/", timeout=8
        ) as r:
            content = r.read().decode()
        versions = re.findall(r'href="(\d+\.\d+\.\d+)/"', content)
        if versions:
            latest = sorted(versions,
                            key=lambda v: tuple(int(x) for x in v.split(".")))[-1]
            return (
                f"https://dist.torproject.org/torbrowser/{latest}/"
                f"tor-expert-bundle-windows-x86_64-{latest}.tar.gz"
            )
    except Exception:
        pass
    return TOR_FALLBACK_URL


def _find_python() -> str:
    for cmd in ["python", "python3", "py"]:
        try:
            r = subprocess.run(
                [cmd, "--version"], capture_output=True, text=True,
                timeout=6, creationflags=_NO_WIN,
            )
            if r.returncode == 0 and "Python 3" in (r.stdout + r.stderr):
                return cmd
        except Exception:
            pass
    for guess in [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Python",
                     "Python313", "python.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Python",
                     "Python312", "python.exe"),
        r"C:\Python313\python.exe",
        r"C:\Python312\python.exe",
    ]:
        if os.path.exists(guess):
            return guess
    return ""


class PrerequisitesPage(PageBase):
    """
    Installs every tool CPharm needs — Java, Python, pip packages, Tor, CPharm files.
    One button. Fully automatic. User just watches the progress.
    """

    _log_write = lambda self, t: self._log(t)

    def __init__(self, parent):
        super().__init__(parent)
        self._ready   = False
        self._working = False
        self._rows    = {}

        self.header(
            "Step 1 — Install Everything",
            "The wizard downloads and installs all tools automatically.\n"
            "Click the big button and wait — everything will be ready."
        )

        # ── install path row ──────────────────────────────────────────────────
        path_card = tk.Frame(self, bg=BG3, padx=14, pady=10)
        path_card.pack(fill="x", pady=(0, 10))
        tk.Label(path_card, text="Install CPharm to:",
                 font=("Segoe UI", 10, "bold"), bg=BG3, fg=T1).pack(side="left")
        self._install_dir = tk.StringVar(value=CPHARM_DEFAULT)
        tk.Entry(path_card, textvariable=self._install_dir, font=FM,
                 bg=BG2, fg=T1, insertbackground=T1, relief="flat",
                 width=36).pack(side="left", padx=8)
        tk.Button(path_card, text="Browse", font=FS, bg=BG3, fg=T1,
                  relief="flat", cursor="hand2", padx=8, pady=4,
                  command=self._browse_dir).pack(side="left")

        # ── prerequisite list ─────────────────────────────────────────────────
        list_card = tk.Frame(self, bg=BG2, padx=14, pady=12,
                             highlightthickness=1, highlightbackground=BORDER)
        list_card.pack(fill="x", pady=(0, 10))

        items = [
            ("java",     "☕", "Java JDK 21",             "Needed to run the Android SDK tools",        True),
            ("python",   "🐍", "Python 3.13",             "Needed to run CPharm automation scripts",    True),
            ("packages", "📦", "Python packages",         "websockets + psutil for the dashboard",      True),
            ("tor",      "🧅", "Tor",                     "IP rotation between sessions  (optional)",   False),
            ("cpharm",   "⚡", "CPharm automation files", "The bot scripts and dashboard",              True),
        ]
        for key, icon, name, desc, required in items:
            row = tk.Frame(list_card, bg=BG2, pady=5)
            row.pack(fill="x")
            tk.Label(row, text=icon, font=("Segoe UI", 15), bg=BG2,
                     width=3).pack(side="left")
            info = tk.Frame(row, bg=BG2)
            info.pack(side="left", fill="x", expand=True)
            tk.Label(info, text=name + ("" if required else "  (optional)"),
                     font=("Segoe UI", 10, "bold"), bg=BG2, fg=T1,
                     anchor="w").pack(fill="x")
            tk.Label(info, text=desc, font=FS, bg=BG2, fg=T3,
                     anchor="w").pack(fill="x")
            status_lbl = tk.Label(row, text="—", font=("Segoe UI", 10, "bold"),
                                  bg=BG2, fg=T3, width=22, anchor="e")
            status_lbl.pack(side="right")
            self._rows[key] = status_lbl

        # ── big install button ────────────────────────────────────────────────
        self._install_btn = tk.Button(
            self,
            text="⬇   Install Everything  —  Click Here",
            font=("Segoe UI", 14, "bold"),
            bg=GREEN, fg="#000000",
            relief="flat", cursor="hand2",
            padx=24, pady=14,
            command=self._install_all,
        )
        self._install_btn.pack(fill="x", pady=(0, 8))

        # ── progress ──────────────────────────────────────────────────────────
        self._progress = ttk.Progressbar(self, mode="determinate", maximum=100)
        self._progress.pack(fill="x", pady=(0, 4))
        self._progress_lbl = tk.Label(self, text="", font=FS, bg=BG, fg=T2)
        self._progress_lbl.pack(anchor="w")

        # ── log ───────────────────────────────────────────────────────────────
        log_fr = tk.Frame(self, bg=BG2)
        log_fr.pack(fill="both", expand=True, pady=(6, 0))
        self._log_box = tk.Text(log_fr, height=5, font=FM, bg=BG2, fg=T1,
                                relief="flat", state="disabled", wrap="word")
        sb = tk.Scrollbar(log_fr, orient="vertical", command=self._log_box.yview)
        self._log_box.configure(yscrollcommand=sb.set)
        self._log_box.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    # ── helpers ───────────────────────────────────────────────────────────────

    def _browse_dir(self):
        d = filedialog.askdirectory(title="Choose where to install CPharm")
        if d:
            self._install_dir.set(d)

    def _log(self, text):
        def _do():
            self._log_box.config(state="normal")
            self._log_box.insert("end", text if text.endswith("\n") else text + "\n")
            self._log_box.see("end")
            self._log_box.config(state="disabled")
        self.after(0, _do)

    def _set_row(self, key, text, color=T2):
        lbl = self._rows.get(key)
        if lbl:
            lbl.config(text=text, fg=color)

    def _set_progress(self, pct, label=""):
        self._progress["value"] = pct
        self._progress_lbl.config(text=label)

    def _download(self, url, dest, label, start_pct, end_pct):
        def hook(block, bsize, total):
            if total > 0:
                done = block * bsize
                frac = min(done / total, 1.0)
                pct  = int(start_pct + frac * (end_pct - start_pct))
                mb   = done / 1_048_576
                tot  = total / 1_048_576
                self._set_progress(pct, f"{label}  {mb:.0f} / {tot:.0f} MB")
        _urlretrieve(url, dest, hook)

    # ── checks ────────────────────────────────────────────────────────────────

    def _check_java(self) -> bool:
        if bool(_find_java_home()):
            return True
        ok, _ = run_cmd(["java", "-version"])
        return ok

    def _check_python(self) -> bool:
        return bool(_find_python())

    def _check_packages(self) -> bool:
        py = _find_python()
        if not py:
            return False
        ok, out = run_cmd([py, "-c", "import websockets, psutil"])
        return ok

    def _check_tor(self) -> bool:
        d = state.get("cpharm_dir", "") or self._install_dir.get()
        if d:
            tor_exe = Path(d) / "automation" / "tor" / "tor.exe"
            if tor_exe.exists():
                return True
        for p in [r"C:\Tor\tor.exe", r"C:\Program Files\Tor\tor.exe"]:
            if Path(p).exists():
                return True
        return False

    def _check_cpharm(self) -> bool:
        d = state.get("cpharm_dir", "") or self._install_dir.get()
        if d and (Path(d) / "automation" / "dashboard.py").exists():
            return True
        if (Path(__file__).parent.parent / "automation" / "dashboard.py").exists():
            state["cpharm_dir"] = str(Path(__file__).parent.parent)
            return True
        return False

    def _check_all(self):
        checks = {
            "java":     self._check_java,
            "python":   self._check_python,
            "packages": self._check_packages,
            "tor":      self._check_tor,
            "cpharm":   self._check_cpharm,
        }
        all_required = True
        for key, fn in checks.items():
            try:
                ok = fn()
            except Exception:
                ok = False
            if ok:
                self._set_row(key, "✅  Ready", GREEN)
            else:
                self._set_row(key, "⬇  Not installed", YELLOW)
                if key in ("java", "python", "packages", "cpharm"):
                    all_required = False
        return all_required

    def _show_java_manual_btn(self):
        if hasattr(self, "_java_manual_btn"):
            return
        self._java_manual_btn = tk.Button(
            self,
            text="☕  Download Java for Windows ARM64  (opens browser)",
            font=("Segoe UI", 11, "bold"),
            bg=YELLOW, fg="#000000",
            relief="flat", cursor="hand2",
            padx=16, pady=10,
            command=lambda: webbrowser.open(JAVA_DOWNLOAD_URL),
        )
        self._java_manual_btn.pack(fill="x", pady=(6, 0))

    # ── installers ────────────────────────────────────────────────────────────

    def _install_java(self):
        for label, url, fname in [
            ("ARM64", JAVA_DOWNLOAD_URL,     "microsoft-jdk-21-arm64.msi"),
            ("x64",   JAVA_DOWNLOAD_URL_X64, "microsoft-jdk-21-x64.msi"),
        ]:
            self._set_row("java", f"⬇  Downloading ({label})…", ACCENT)
            self._log_write(f"Downloading Java JDK 21 ({label})…")
            tmp = Path(os.environ.get("TEMP", "C:\\Temp")) / fname
            try:
                self._download(url, tmp, f"Java JDK ({label})", 2, 18)
            except Exception as e:
                self._log_write(f"  Download failed ({label}): {e}")
                tmp.unlink(missing_ok=True)
                continue
            self._set_row("java", "⚙  Installing…", YELLOW)
            self._log_write(f"Installing Java {label} (silent, no restart)…")
            ok, out = run_cmd(
                ["msiexec", "/i", str(tmp), "/quiet", "/norestart"],
                timeout=300,
            )
            tmp.unlink(missing_ok=True)
            if ok or self._check_java():
                self._set_row("java", "✅  Done", GREEN)
                self._log_write(f"Java installed ✅  ({label})")
                return
            self._log_write(f"  Install failed ({label}): {out[-200:]}")
        self._set_row("java", "❌  Failed", RED)
        self._log_write("Java install failed on both ARM64 and x64 installers.")
        self._show_java_manual_btn()

    def _install_python(self):
        for label, url, fname in [
            ("ARM64", PYTHON_URL,     "python-3.13-arm64.exe"),
            ("x64",   PYTHON_URL_X64, "python-3.13-x64.exe"),
        ]:
            self._set_row("python", f"⬇  Downloading ({label})…", ACCENT)
            self._log_write(f"Downloading Python 3.13 ({label})…")
            tmp = Path(os.environ.get("TEMP", "C:\\Temp")) / fname
            try:
                self._download(url, tmp, f"Python ({label})", 20, 36)
            except Exception as e:
                self._log_write(f"  Download failed ({label}): {e}")
                tmp.unlink(missing_ok=True)
                continue
            self._set_row("python", "⚙  Installing…", YELLOW)
            self._log_write(f"Installing Python {label}…")
            ok, out = run_cmd(
                [str(tmp), "/quiet",
                 "InstallAllUsers=0", "PrependPath=1", "Include_launcher=0"],
                timeout=300,
            )
            tmp.unlink(missing_ok=True)
            if ok or self._check_python():
                state["python_cmd"] = _find_python() or "python"
                self._set_row("python", "✅  Done", GREEN)
                self._log_write(f"Python installed ✅  ({state['python_cmd']})")
                return
            self._log_write(f"  Install failed ({label}): {out[-200:]}")
        self._set_row("python", "❌  Failed", RED)
        self._log_write("Python install failed on both ARM64 and x64 installers.")

    def _install_packages(self):
        py = _find_python() or "python"
        state["python_cmd"] = py
        self._set_row("packages", "⚙  Installing…", YELLOW)
        self._set_progress(55, "Installing Python packages…")
        for extra, label in [([], "global"), (["--user"], "--user fallback")]:
            self._log_write(f"Running: pip install websockets psutil {' '.join(extra)}…")
            ok, out = run_cmd(
                [py, "-m", "pip", "install", "--upgrade"] + extra + ["websockets", "psutil"],
                timeout=120,
            )
            if ok or self._check_packages():
                self._set_row("packages", "✅  Done", GREEN)
                self._log_write(f"Packages installed ✅  ({label})")
                return
            self._log_write(f"  pip {label} failed: {out[-200:]}")
        self._set_row("packages", "❌  Failed", RED)
        self._log_write("pip failed both globally and with --user.\nTry running: pip install websockets psutil  in a terminal.")

    def _install_tor(self):
        install_dir = Path(state.get("cpharm_dir", "") or self._install_dir.get())
        tor_dir = install_dir / "automation" / "tor"
        tor_dir.mkdir(parents=True, exist_ok=True)

        self._set_row("tor", "⬇  Finding latest…", ACCENT)
        self._log_write("Looking up latest Tor version…")
        tor_url = _fetch_latest_tor_url()
        self._log_write(f"Downloading: {tor_url}")
        self._set_row("tor", "⬇  Downloading…", ACCENT)

        tmp = tor_dir / "_tor_bundle.tar.gz"
        try:
            _urlretrieve(tor_url, tmp, timeout=120)
        except Exception as e:
            self._log_write(f"  Download failed: {e}")
            self._set_row("tor", "❌  Failed", RED)
            self._log_write("  Tor download failed — IP rotation will use system Tor if available.")
            return

        self._set_row("tor", "📦  Extracting…", YELLOW)
        self._log_write("Extracting Tor…")
        with tarfile.open(tmp, "r:gz") as tf:
            for member in tf.getmembers():
                if member.name.startswith("tor/"):
                    member.name = member.name[len("tor/"):]
                    if not member.name:
                        continue
                    tf.extract(member, tor_dir)
        tmp.unlink(missing_ok=True)

        if (tor_dir / "tor.exe").exists():
            self._set_row("tor", "✅  Done", GREEN)
            self._log_write("Tor installed ✅")
        else:
            self._set_row("tor", "⚠  Skipped", T3)
            self._log_write("Tor not found after extract — IP rotation will use system Tor if available.")

    def _install_cpharm(self):
        install_dir = Path(self._install_dir.get())
        if self._check_cpharm():
            self._set_row("cpharm", "✅  Already here", GREEN)
            self._log_write(f"CPharm files already present at {state.get('cpharm_dir', install_dir)}")
            return

        self._set_row("cpharm", "⬇  Downloading…", ACCENT)
        self._log_write("Downloading CPharm files from GitHub…")
        tmp = Path(os.environ.get("TEMP", "C:\\Temp")) / "cpharm.zip"
        zip_ok = True
        try:
            _urlretrieve(CPHARM_ZIP_URL, tmp, timeout=120)
        except Exception as e:
            self._log_write(f"  ZIP download failed: {e}")
            zip_ok = False

        if zip_ok:
            self._set_row("cpharm", "📦  Extracting…", YELLOW)
            self._log_write(f"Extracting to {install_dir}…")
            extract_tmp = install_dir.parent / "_cpharm_extract"
            if extract_tmp.exists():
                shutil.rmtree(extract_tmp)
            try:
                with zipfile.ZipFile(tmp, "r") as zf:
                    zf.extractall(extract_tmp)
                tmp.unlink(missing_ok=True)
                inner = next(extract_tmp.iterdir(), None)
                if inner and inner.is_dir():
                    if install_dir.exists():
                        shutil.rmtree(install_dir)
                    shutil.move(str(inner), str(install_dir))
                shutil.rmtree(extract_tmp, ignore_errors=True)
            except Exception as e:
                self._log_write(f"  Extraction failed: {e}")
                zip_ok = False

        if (install_dir / "automation" / "dashboard.py").exists():
            state["cpharm_dir"] = str(install_dir)
            self._set_row("cpharm", "✅  Done", GREEN)
            self._log_write(f"CPharm installed at {install_dir} ✅")
        else:
            self._log_write("  ZIP method failed — trying git clone fallback…")
            self._set_row("cpharm", "⬇  git clone…", YELLOW)
            ok, out = run_cmd(
                ["git", "clone", REPO_URL, str(install_dir)],
                timeout=120,
            )
            if ok or (install_dir / "automation" / "dashboard.py").exists():
                state["cpharm_dir"] = str(install_dir)
                self._set_row("cpharm", "✅  Done", GREEN)
                self._log_write(f"CPharm installed via git clone ✅")
            else:
                self._set_row("cpharm", "❌  Failed", RED)
                self._log_write("Both ZIP download and git clone failed.")
                self._log_write(f"Manual: git clone {REPO_URL} \"{install_dir}\"")

    # ── main install flow ─────────────────────────────────────────────────────

    def _install_all(self):
        if self._working:
            return
        self._working = True
        self._install_btn.config(state="disabled", text="Working… please wait")
        threading.Thread(target=self._install_thread, daemon=True).start()

    def _install_thread(self):
        try:
            self._set_progress(0, "Starting…")

            if not self._check_java():
                self._install_java()
            else:
                self._set_row("java", "✅  Already installed", GREEN)

            self._set_progress(20, "")

            if not self._check_python():
                self._install_python()
            else:
                self._set_row("python", "✅  Already installed", GREEN)
                state["python_cmd"] = _find_python() or "python"

            self._set_progress(40, "")

            if not self._check_packages():
                self._install_packages()
            else:
                self._set_row("packages", "✅  Already installed", GREEN)

            self._set_progress(56, "")

            if not self._check_cpharm():
                self._install_cpharm()
            else:
                self._set_row("cpharm", "✅  Already here", GREEN)

            self._set_progress(74, "")

            if not self._check_tor():
                self._install_tor()
            else:
                self._set_row("tor", "✅  Already installed", GREEN)

            self._set_progress(100, "Done!")
            self._log_write("\n✅  All done. Click Next → to continue.")
            self._ready = True
            self._install_btn.config(
                state="normal",
                text="✅  Ready — click Next →",
                bg=GREEN,
            )
        except Exception as exc:
            self._log_write(f"\n❌  Error: {exc}")
            self._install_btn.config(state="normal", text="⬇   Try Again")
        finally:
            self._working = False

    # ── page hooks ────────────────────────────────────────────────────────────

    def on_enter(self):
        threading.Thread(target=self._scan_thread, daemon=True).start()

    def _scan_thread(self):
        all_ok = self._check_all()
        if all_ok:
            self._set_progress(100, "All tools already installed!")
            self._install_btn.config(
                text="✅  Everything Ready — click Next →",
                bg=GREEN,
            )
            self._ready = True
        else:
            self._set_progress(0, "")

    def can_advance(self):
        if not self._ready:
            if self._check_all() and self._check_cpharm():
                self._ready = True
                return True
            messagebox.showinfo(
                "Not ready yet",
                "Click 'Install Everything' and wait for it to finish.\n\n"
                "The wizard will handle every download automatically."
            )
            return False
        return True


# ─── page 3: android sdk auto-install ────────────────────────────────────────

class AndroidStudioPage(PageBase):
    """
    Auto-downloads and installs the Android SDK command-line tools.
    No Android Studio, no terminal, no manual steps.
    """

    _log_write = lambda self, t: self._log(t)

    def __init__(self, parent):
        super().__init__(parent)
        self._ready    = False
        self._working  = False

        self.header(
            "Step 1 — Install Android SDK",
            "One button does everything. The wizard downloads and sets up the SDK for you.\n"
            "No Android Studio needed. No terminal. Just click the big button below."
        )

        # ── status card ───────────────────────────────────────────────────────
        status_card = tk.Frame(self, bg=BG2, padx=16, pady=14,
                               highlightthickness=1, highlightbackground=BORDER)
        status_card.pack(fill="x", pady=(0, 12))

        self._icon_lbl = tk.Label(status_card, text="🔍", font=("Segoe UI", 28),
                                  bg=BG2)
        self._icon_lbl.pack(side="left", padx=(0, 14))

        right = tk.Frame(status_card, bg=BG2)
        right.pack(side="left", fill="x", expand=True)
        self._status_lbl = tk.Label(right, text="Checking for Android SDK…",
                                    font=("Segoe UI", 12, "bold"),
                                    bg=BG2, fg=T1, anchor="w")
        self._status_lbl.pack(fill="x")
        self._detail_lbl = tk.Label(right, text="",
                                    font=FM, bg=BG2, fg=T3,
                                    anchor="w", wraplength=520)
        self._detail_lbl.pack(fill="x", pady=(2, 0))

        # ── big install button ────────────────────────────────────────────────
        self._install_btn = tk.Button(
            self,
            text="⬇   Install Android SDK — Click Here",
            font=("Segoe UI", 14, "bold"),
            bg=GREEN, fg="#000000",
            relief="flat", cursor="hand2",
            padx=24, pady=14,
            command=self._auto_install,
        )
        self._install_btn.pack(fill="x", pady=(0, 10))

        # ── progress bar ──────────────────────────────────────────────────────
        self._progress = ttk.Progressbar(self, mode="determinate", maximum=100)
        self._progress.pack(fill="x", pady=(0, 6))
        self._progress_lbl = tk.Label(self, text="", font=FS, bg=BG, fg=T2)
        self._progress_lbl.pack(anchor="w")

        # ── log box ───────────────────────────────────────────────────────────
        log_fr = tk.Frame(self, bg=BG2)
        log_fr.pack(fill="both", expand=True, pady=(6, 0))
        self._log_box = tk.Text(log_fr, height=7, font=FM, bg=BG2, fg=T1,
                                relief="flat", state="disabled", wrap="word")
        sb = tk.Scrollbar(log_fr, orient="vertical", command=self._log_box.yview)
        self._log_box.configure(yscrollcommand=sb.set)
        self._log_box.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # ── MuMuPlayer ARM64 alternative (shown on ARM64 host) ───────────────
        if IS_WIN and "arm64" in _machine_arch():
            mumu_sep = tk.Frame(self, bg=BORDER, height=1)
            mumu_sep.pack(fill="x", pady=(8, 6))
            mumu_card = tk.Frame(self, bg="#1a2a1a", padx=14, pady=12,
                                 highlightthickness=1, highlightbackground=GREEN)
            mumu_card.pack(fill="x", pady=(0, 8))
            tk.Label(mumu_card,
                     text="🎮  ARM64 Windows: Use MuMuPlayer instead of Android SDK",
                     font=("Segoe UI", 11, "bold"), bg="#1a2a1a", fg=GREEN,
                     anchor="w").pack(fill="x")
            tk.Label(mumu_card,
                     text="Google does NOT publish a Windows ARM64 emulator.\n"
                          "MuMuPlayer ARM is the only solution — native ARM64, multi-instance, full ADB.\n"
                          "Click below to skip the SDK entirely and use MuMuPlayer.",
                     font=FS, bg="#1a2a1a", fg=T2,
                     anchor="w", justify="left").pack(fill="x", pady=(4, 8))
            mumu_btn_row = tk.Frame(mumu_card, bg="#1a2a1a")
            mumu_btn_row.pack(fill="x")
            self._mumu_mode_btn = tk.Button(
                mumu_btn_row,
                text="✅  Use MuMuPlayer ARM64  (skip SDK)",
                font=("Segoe UI", 11, "bold"),
                bg=GREEN, fg="#000000",
                relief="flat", cursor="hand2",
                padx=16, pady=8,
                command=self._activate_mumu_mode,
            )
            self._mumu_mode_btn.pack(side="left", padx=(0, 10))
            tk.Button(mumu_btn_row,
                      text="Download MuMuPlayer ARM",
                      font=FS, bg=BG3, fg=T1,
                      relief="flat", cursor="hand2", padx=8, pady=6,
                      command=lambda: __import__("webbrowser").open(MUMU_DOWNLOAD_URL),
                      ).pack(side="left")
            self._mumu_status_lbl = tk.Label(mumu_card, text="",
                                             font=FS, bg="#1a2a1a", fg=GREEN)
            self._mumu_status_lbl.pack(anchor="w", pady=(6, 0))

        # ── already installed / manual path ──────────────────────────────────
        sep = tk.Frame(self, bg=BORDER, height=1)
        sep.pack(fill="x", pady=(10, 6))
        note_row = tk.Frame(self, bg=BG)
        note_row.pack(fill="x")
        tk.Label(note_row,
                 text="Already have the SDK installed?  Paste the folder path here:",
                 font=FS, bg=BG, fg=T2).pack(side="left")
        self._path_var = tk.StringVar()
        tk.Entry(note_row, textvariable=self._path_var, font=FM,
                 bg=BG2, fg=T1, insertbackground=T1, relief="flat",
                 width=28).pack(side="left", padx=6)
        tk.Button(note_row, text="Browse", font=FS, bg=BG3, fg=T1,
                  relief="flat", cursor="hand2", padx=8, pady=4,
                  command=self._browse).pack(side="left")
        tk.Button(note_row, text="Check", font=FS, bg=BG3, fg=T1,
                  relief="flat", cursor="hand2", padx=8, pady=4,
                  command=self._check).pack(side="left", padx=(4, 0))

    # ── helpers ───────────────────────────────────────────────────────────────

    def _log(self, text):
        def _do():
            self._log_box.config(state="normal")
            self._log_box.insert("end", text if text.endswith("\n") else text + "\n")
            self._log_box.see("end")
            self._log_box.config(state="disabled")
        self.after(0, _do)

    def _set_status(self, icon, text, detail="", color=T1):
        self._icon_lbl.config(text=icon)
        self._status_lbl.config(text=text, fg=color)
        self._detail_lbl.config(text=detail)

    def _set_progress(self, pct, label=""):
        self._progress["value"] = pct
        self._progress_lbl.config(text=label)

    def _has_java(self):
        # Delegate to the shared helper which checks JBR, all common locations, etc.
        return bool(_find_java_home())

    # ── auto install ──────────────────────────────────────────────────────────

    def _auto_install(self):
        if self._working:
            return
        self._working = True
        self._install_btn.config(state="disabled", text="Working… please wait")
        threading.Thread(target=self._install_thread, daemon=True).start()

    def _install_thread(self):
        try:
            self._do_install()
        except Exception as exc:
            self._log_write(f"\n❌  Unexpected error: {exc}")
            self._set_status("❌", "Something went wrong — see log below.", color=RED)
        finally:
            self._working = False
            if not self._ready:
                self._install_btn.config(state="normal",
                                         text="Try Again")

    def _do_install(self):
        sdk_path = Path(SDK_DEFAULT_PATH)

        # ── step 0: check if already installed ───────────────────────────────
        self._set_status("🔍", "Checking for existing SDK…", color=T2)
        self._set_progress(5, "Scanning…")
        existing = find_sdk()
        if existing:
            state["sdk_path"] = existing
            self._log_write(f"SDK already found at:  {existing}")
            # Make sure emulator is actually present — if not, install it
            if not Path(sdk_tool("emulator")).exists():
                self._log_write("Emulator missing — installing it now…")
                self._install_missing_tools(existing)
                return
            self._finish_ok(existing)
            return

        # ── step 1: Java check ────────────────────────────────────────────────
        self._set_status("☕", "Checking for Java…", color=T2)
        self._set_progress(8, "Checking Java…")
        if not self._has_java():
            self._log_write("Java not found. The SDK tools need Java to run.")
            self._log_write(f"Download Java here:  {JAVA_DOWNLOAD_URL}")
            self._set_status(
                "☕",
                "Java is required — please install it first.",
                detail="Click the button below, install Java, then click 'Try Again'.",
                color=YELLOW,
            )
            self._set_progress(0, "")
            self.after(0, self._show_java_button)
            self._working = False
            self._install_btn.config(state="normal", text="Try Again  (after installing Java)")
            return
        self._log_write("Java found ✅")

        # ── step 2: create SDK folder ─────────────────────────────────────────
        self._set_status("📁", "Creating SDK folder…", color=T2)
        self._set_progress(12, "Creating folders…")
        tools_dir = sdk_path / "cmdline-tools"
        tools_dir.mkdir(parents=True, exist_ok=True)
        self._log_write(f"SDK folder:  {sdk_path}")

        # ── step 3: download cmdline-tools zip ────────────────────────────────
        zip_path = sdk_path / "cmdline-tools" / "cmdline-tools.zip"
        if zip_path.exists():
            self._log_write("Zip already downloaded — skipping download.")
            self._set_progress(50, "Already downloaded.")
        else:
            self._set_status("⬇", "Downloading Android SDK tools…",
                             detail="~130 MB — this takes a minute on slow connections.",
                             color=ACCENT)

            def _progress_hook(block_num, block_size, total_size):
                if total_size > 0:
                    pct = min(12 + int(block_num * block_size / total_size * 38), 50)
                    mb_done = block_num * block_size / 1_048_576
                    mb_total = total_size / 1_048_576
                    self._set_progress(pct, f"Downloading…  {mb_done:.0f} / {mb_total:.0f} MB")

            downloaded = False
            for try_url in [CMDLINE_TOOLS_URL, CMDLINE_TOOLS_URL_ALT]:
                self._log_write(f"Downloading from:\n  {try_url}")
                try:
                    _urlretrieve(try_url, zip_path, _progress_hook, timeout=120)
                    downloaded = True
                    break
                except Exception as e:
                    self._log_write(f"  Failed ({try_url}): {e}")
                    zip_path.unlink(missing_ok=True)

            if not downloaded:
                self._log_write("Both cmdline-tools URLs failed.")
                self._set_status("❌", "Network blocked — see options below", color=RED)
                self._set_progress(0, "")
                return

            self._log_write("Download complete ✅")

        # ── step 4: extract zip ───────────────────────────────────────────────
        self._set_status("📦", "Extracting files…", color=T2)
        self._set_progress(55, "Extracting…")
        self._log_write("Extracting zip…")

        extract_tmp = tools_dir / "_extract_tmp"
        if extract_tmp.exists():
            shutil.rmtree(extract_tmp)

        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_tmp)

        # The zip ships a remotePackage package.xml (for distribution).
        # sdkmanager reads localPackage format when scanning installed packages.
        # Write the correct localPackage package.xml so sdkmanager sees emulator
        # as installed and doesn't block system image install with a dependency error.
        _write_local_package_xml(
            extract_tmp / "cmdline-tools" / "package.xml",
            path_id="cmdline-tools;latest",
            major=16, minor=0, micro=0,
            display="Android SDK Command-line Tools (latest)",
            license_ref="android-sdk-license",
            ns_type="ns5:genericDetailsType",
            extra_ns='xmlns:ns3="http://schemas.android.com/repository/android/generic/01"',
        )
        latest_dir = tools_dir / "latest"
        if latest_dir.exists():
            shutil.rmtree(latest_dir)
        shutil.move(str(extract_tmp / "cmdline-tools"), str(latest_dir))
        shutil.rmtree(extract_tmp, ignore_errors=True)
        self._log_write("Extraction done ✅")
        self._set_progress(62, "Extracted.")

        # ── step 5: run sdkmanager to install platform-tools + emulator ───────
        state["sdk_path"] = str(sdk_path)
        self._install_missing_tools(str(sdk_path))

    def _install_missing_tools(self, sdk):
        """
        Install platform-tools and emulator via sdkmanager.
        Uses _run_sdkmanager so Java env is injected and licenses are auto-accepted.
        """
        # Before downloading anything, check if the emulator directory exists but is
        # missing device-catalog.xml (avdmanager needs it to list device profiles).
        # Install it now so avdmanager can enumerate hardware profiles for AVD creation.
        if Path(sdk_tool("emulator")).exists():
            self._install_emulator_device_catalog(sdk, log_fn=self._log)

        missing = []
        if not Path(sdk_tool("emulator")).exists():
            missing.append("emulator")
        if not Path(sdk_tool("avdmanager")).exists():
            missing.append("cmdline-tools;latest")
        missing.append("platform-tools")   # always refresh

        self._set_status("⚙", "Installing Android tools…",
                         detail="Downloads ~200 MB. Please wait — do not close the window.",
                         color=YELLOW)
        self._set_progress(60, "Accepting licenses…")
        self._log_write(f"JAVA_HOME: {_find_java_home() or '(not found — Java required)'}")

        # Accept all pending SDK licenses first — required before any install on fresh SDK
        self._log_write("Accepting SDK licenses…")
        _run_sdkmanager(["--licenses"], sdk, log_fn=self._log, timeout=60)

        # Check what packages sdkmanager can actually see — if emulator doesn't show
        # here it means the remote repo fetch is failing (network/proxy/TLS issue).
        self._set_progress(63, "Checking available packages…")
        self._log_write("Checking available packages (fetching remote catalog)…")
        self._set_progress(65, "Running sdkmanager…")
        self._log_write(f"Installing: {', '.join(missing)}")
        ok, out = _run_sdkmanager(
            ["--channel=0"] + missing,
            sdk,
            log_fn=self._log,
            timeout=600,
        )

        self._set_progress(92, "Verifying…")

        if not Path(sdk_tool("emulator")).exists():
            self._log("\n⚠  sdkmanager couldn't install emulator — Java network blocked by firewall.")
            self._log("   Switching to direct Python download (bypasses Java network stack)…\n")
            self._set_status("⬇", "Downloading emulator directly…",
                             detail="Java is blocked by firewall — using Python downloader instead.",
                             color=YELLOW)
            self._set_progress(70, "Direct downloading emulator…")
            ok_emu = _direct_download_emulator(sdk, log_fn=self._log)
            self._set_progress(85, "Direct downloading platform-tools…")
            ok_pt  = _direct_download_platform_tools(sdk, log_fn=self._log)
            self._set_progress(92, "Verifying…")

            if not Path(sdk_tool("emulator")).exists():
                self._log_write("\n❌  Both sdkmanager and direct download failed.")
                self._log_write("    Your network is blocking dl.google.com entirely.")
                self._set_status("❌", "Network blocked — see options below", color=RED)
                self._set_progress(0, "")
                self._install_btn.config(state="normal", text="Try Again")
                self.after(0, self._show_firewall_btn)
                return

            self._install_emulator_device_catalog(sdk, log_fn=self._log)

        self._log_write("emulator        ✅")
        self._log_write("platform-tools  ✅")

        if IS_WIN and "arm64" in _machine_arch():
            self._log_write("ARM64 host — checking emulator binary…")
            _ensure_emulator_meta(sdk, log_fn=self._log)

        self._finish_ok(sdk)



    def _install_emulator_device_catalog(self, sdk, log_fn=None):
        # The emulator/ package contains the emulator binary but NOT device definitions.
        # Those live in emulator/meta/device-catalog.xml — a separate metadata package.
        # Without it, 'avdmanager list device' returns empty → "emulator package must be installed!"
        emu_dir = Path(sdk) / "emulator"
        meta_dir = emu_dir / "meta"
        catalog_file = meta_dir / "device-catalog.xml"

        if catalog_file.exists():
            if log_fn:
                log_fn("  Device catalog already present — skipping metadata install.\n")
            return

        if log_fn:
            log_fn("  Installing emulator device catalog metadata…\n")

        import xml.etree.ElementTree as ET
        try:
            with urllib.request.urlopen(
                "https://dl.google.com/android/repository/repository2-3.xml", timeout=20
            ) as r:
                xml_data = r.read().decode("utf-8")
        except Exception as e:
            if log_fn:
                log_fn("  ⚠  Could not fetch emulator repo XML: " + str(e) + "\n"
                       "    Device profiles may not be available.\n")
            return

        meta_url = None
        try:
            root = ET.fromstring(xml_data)
            for pkg in root.iter():
                if pkg.get("path") == "emulator":
                    for child in pkg.iter():
                        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                        if tag in ("archives", "archive"):
                            for a in child:
                                atag = a.tag.split("}")[-1] if "}" in a.tag else a.tag
                                if atag == "url":
                                    val = (a.text or "").strip()
                                    if val and val.endswith(".zip"):
                                        meta_url = "https://dl.google.com/android/repository/" + val
                                        break
                            if meta_url:
                                break
                    if meta_url:
                        break
        except ET.ParseError:
            pass

        if not meta_url:
            if log_fn:
                log_fn("  ⚠  Could not find emulator meta URL — device profiles may be missing.\n")
            return

        if log_fn:
            log_fn("  Downloading emulator meta: " + meta_url + "\n")

        tmp = Path(sdk) / "_emu_meta.zip"
        try:
            _urlretrieve(meta_url, tmp, timeout=60)
            meta_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(tmp, "r") as zf:
                for member in zf.namelist():
                    if "meta/" in member or "device-catalog" in member:
                        zf.extract(member, emu_dir)
            tmp.unlink(missing_ok=True)

            # Also register the emulator as an installed package (path_id="emulator").
            # avdmanager checks for this before listing device profiles.
            _write_emulator_package_xml(emu_dir)
            _write_local_package_xml(
                meta_dir / "package.xml",
                path_id="emulator;meta",
                major=36, minor=6, micro=4,
                display="Android Emulator Device Metadata",
                license_ref="android-sdk-license",
                ns_type="ns5:genericDetailsType",
                extra_ns='xmlns:ns3="http://schemas.android.com/repository/android/generic/01"',
            )
            if log_fn:
                log_fn("  Emulator device catalog installed ✅\n")
        except Exception as e:
            if log_fn:
                log_fn("  ⚠  Could not install device catalog: " + str(e) + "\n")
            tmp.unlink(missing_ok=True)

    def _finish_ok(self, sdk_path):
        self._set_status("✅", "Android SDK is ready!",
                         detail=f"SDK installed at:  {sdk_path}",
                         color=GREEN)
        self._set_progress(100, "Done!")
        self._log_write(f"\n✅  All good. Click Next → to continue.")
        self._ready = True
        self._install_btn.config(
            state="normal",
            text="✅  SDK Ready — click Next →",
            bg=GREEN,
        )

    def _show_firewall_btn(self):
        if hasattr(self, "_fw_btn"):
            return
        self._fw_btn = tk.Button(
            self,
            text="🛡  Allow Java through Windows Firewall  (click → approve UAC prompt)",
            font=("Segoe UI", 11, "bold"),
            bg=YELLOW, fg="#000000",
            relief="flat", cursor="hand2",
            padx=16, pady=10,
            command=self._do_firewall,
        )
        self._fw_btn.pack(fill="x", pady=(6, 0))

    def _do_firewall(self):
        ok = _add_firewall_exception_for_java()
        if ok:
            self._log_write("\nFirewall rule submitted — approve the UAC prompt that appeared.")
            self._log_write("Then click Try Again.\n")
        else:
            self._log_write("\n❌  Could not launch PowerShell — add the rule manually:\n")
            self._log_write('   Windows Security → Firewall → Advanced → Outbound Rules')
            self._log_write('   → New Rule → Program → browse to java.exe → Allow\n')

    def _show_java_button(self):
        if hasattr(self, "_java_btn"):
            return
        self._java_btn = tk.Button(
            self,
            text="☕  Download Java for Windows ARM64  (opens browser)",
            font=("Segoe UI", 11, "bold"),
            bg=YELLOW, fg="#000000",
            relief="flat", cursor="hand2",
            padx=16, pady=10,
            command=lambda: webbrowser.open(JAVA_DOWNLOAD_URL),
        )
        self._java_btn.pack(fill="x", pady=(6, 0))

    # ── standard page hooks ───────────────────────────────────────────────────

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
            self._log_write(f"Manual path set: {manual}\n")

        sdk = state.get("sdk_path", "")
        if not sdk:
            self._set_status("⬇", "Android SDK not installed yet.",
                             detail="Click Install below, or paste folder path above and click Check.",
                             color=T2)
            self._log_write("No SDK path found. Install it or enter a path above.\n")
            self._ready = False
            return False

        self._log_write(f"Checking SDK at: {sdk}\n")

        # Check tools directly in common locations — more reliable than sdk_tool()
        base = Path(sdk)
        has_emu = (base / "emulator" / "emulator.exe").exists() or (base / "emulator" / "emulator").exists()
        has_avd = (base / "cmdline-tools" / "latest" / "bin" / "avdmanager.bat").exists() \
                  or (base / "cmdline-tools" / "latest" / "bin" / "avdmanager").exists() \
                  or (base / "tools" / "bin" / "avdmanager.bat").exists()
        has_sdk = (base / "cmdline-tools" / "latest" / "bin" / "sdkmanager.bat").exists() \
                  or (base / "cmdline-tools" / "latest" / "bin" / "sdkmanager").exists()

        self._log_write(f"  emulator: {'found' if has_emu else 'missing'}\n")
        self._log_write(f"  avdmanager: {'found' if has_avd else 'missing'}\n")
        self._log_write(f"  sdkmanager: {'found' if has_sdk else 'missing'}\n")

        if has_emu and has_avd and has_sdk:
            self._log_write("All tools present!\n")
            self._finish_ok(sdk)
            return True

        missing = []
        if not has_emu: missing.append("emulator")
        if not has_avd: missing.append("avdmanager")
        if not has_sdk: missing.append("sdkmanager")
        self._set_status(
            "⚙",
            f"SDK found but missing: {', '.join(missing)}",
            detail="Click Install Missing Tools below.",
            color=YELLOW,
        )
        self._log_write(f"Missing: {', '.join(missing)} — click Install below.\n")
        self._install_btn.config(
            state="normal",
            text=f"Install Missing Tools ({', '.join(missing)})",
            bg=YELLOW,
        )
        self._ready = False
        return False

    def _activate_mumu_mode(self):
        """Set MuMu mode — bypasses SDK requirement entirely."""
        state["use_mumu"] = True
        self._ready = True
        mgr = _find_mumu_manager()
        if mgr:
            lbl = f"✅ MuMuPlayer found at {mgr.parent.parent}\n   Steps 2 and 3 will use MuMuPlayer — click Next →"
        else:
            lbl = ("✅ MuMu mode activated — click Next →\n"
                   "   Install MuMuPlayer ARM first if you haven't already.")
        if hasattr(self, "_mumu_status_lbl"):
            self._mumu_status_lbl.config(text=lbl)
        if hasattr(self, "_mumu_mode_btn"):
            self._mumu_mode_btn.config(
                text="✅  MuMuPlayer mode active",
                state="disabled", bg=BG3, fg=GREEN)
        self._set_status("✅", "MuMuPlayer ARM64 mode — SDK not needed",
                         detail="Click Next → to continue.", color=GREEN)

    def can_advance(self):
        if state.get("use_mumu"):
            return True
        if self._ready:
            return True
        messagebox.showinfo(
            "Not ready yet",
            "Click 'Install Android SDK' and wait for it to finish first.\n\n"
            "The wizard will download and set everything up automatically.\n\n"
            "On ARM64 Windows (Snapdragon)? Use the green MuMuPlayer button instead."
        )
        return False


# ─── page 4: boot phones ──────────────────────────────────────────────────────

class PhoneFarmPage(PageBase):
    def __init__(self, parent):
        super().__init__(parent)
        self._done = False
        self.header(
            "Step 2 — Create Virtual Phones",
            "The wizard downloads Android 14 and creates your virtual phones automatically.\n"
            "One-time setup. Each phone uses ~2 GB RAM + ~4 GB disk."
        )

        # Phone name prefix
        name_box = tk.Frame(self, bg=BG3, padx=16, pady=10)
        name_box.pack(fill="x", pady=(0, 8))
        tk.Label(name_box, text="Phone name prefix:",
                 font=("Segoe UI", 10, "bold"), bg=BG3, fg=T1, anchor="w").pack(side="left")
        self._prefix_var = tk.StringVar(value=state.get("phone_prefix", "CPharm_Phone"))
        tk.Entry(name_box, textvariable=self._prefix_var, font=FM,
                 bg=BG2, fg=T1, insertbackground=T1, relief="flat",
                 width=22).pack(side="left", padx=(8, 6))
        tk.Label(name_box, text="_1, _2, _3…", font=FS, bg=BG3, fg=T3).pack(side="left")
        self._prefix_var.trace("w", lambda *_: state.update(
            {"phone_prefix": self._prefix_var.get().strip() or "CPharm_Phone"}))

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
        picker.pack(fill="x", pady=(4, 8))

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
                 text=f"  1.  Downloads Android 14 {_machine_arch()} system image (~1 GB) — only once\n"
                      "  2.  Creates one Pixel 6 virtual phone per slot\n"
                      "  3.  Each phone gets a unique device ID and storage\n"
                      "  4.  Names them {prefix}_1, {prefix}_2, etc.",
                 font=FB, bg=BG2, fg=T2, justify="left", anchor="w").pack(fill="x", pady=(8, 0))

        # Create + Delete buttons
        create_row = tk.Frame(self, bg=BG)
        create_row.pack(fill="x", pady=(0, 8))
        self._create_btn = tk.Button(create_row,
                                     text="  ▶  Create Phone Farm  ",
                                     font=("Segoe UI", 12, "bold"),
                                     bg=GREEN, fg=BG, relief="flat",
                                     cursor="hand2", command=self._create,
                                     padx=20, pady=10)
        self._create_btn.pack(side="left", padx=(0, 10))
        tk.Button(create_row, text="🗑 Delete All CPharm Phones",
                  font=FS, bg=RED, fg=BG, relief="flat",
                  cursor="hand2", command=self._delete_phones,
                  padx=10, pady=8).pack(side="left", padx=(0, 10))
        self._progress_lbl = tk.Label(create_row, text="", font=FS, bg=BG, fg=T2)
        self._progress_lbl.pack(side="left")

        # Log
        log_fr = tk.Frame(self, bg=BG2)
        log_fr.pack(fill="both", expand=True, pady=(4, 0))
        self._log_box = tk.Text(log_fr, height=7, font=FM, bg=BG2, fg=T1,
                                relief="flat", state="disabled", wrap="word")
        sb = tk.Scrollbar(log_fr, orient="vertical", command=self._log_box.yview)
        self._log_box.configure(yscrollcommand=sb.set)
        self._log_box.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # MuMuPlayer info panel (hidden on non-MuMu machines at on_enter)
        self._mumu_panel = tk.Frame(self, bg="#1a2a1a", padx=14, pady=12,
                                    highlightthickness=1, highlightbackground=GREEN)
        tk.Label(self._mumu_panel,
                 text="🎮  MuMuPlayer ARM64 Mode",
                 font=("Segoe UI", 12, "bold"), bg="#1a2a1a", fg=GREEN,
                 anchor="w").pack(fill="x")
        tk.Label(self._mumu_panel,
                 text="MuMuPlayer handles phone creation through its own interface.\n\n"
                      "How to create phones:\n"
                      "  1. Open MuMuPlayer (it's in your Start menu or taskbar)\n"
                      "  2. Click the multi-window icon (⧉) at the top-right\n"
                      "  3. Click '+ New Instance' for each phone you want\n"
                      "  4. Leave MuMuPlayer running\n"
                      "  5. Click Next → below — the wizard connects automatically",
                 font=FS, bg="#1a2a1a", fg=T2,
                 anchor="w", justify="left").pack(fill="x", pady=(6, 10))
        tk.Button(self._mumu_panel,
                  text="Open MuMuPlayer Now",
                  font=("Segoe UI", 10, "bold"),
                  bg=GREEN, fg="#000000", relief="flat", cursor="hand2",
                  padx=12, pady=6,
                  command=self._open_mumu).pack(anchor="w")

        # Sequence editor — per-phone automation steps
        seq_sep = tk.Frame(self, bg=BORDER, height=1)
        seq_sep.pack(fill="x", pady=(10, 6))
        seq_hdr = tk.Frame(self, bg=BG)
        seq_hdr.pack(fill="x")
        tk.Label(seq_hdr, text="Automation Sequence  (optional)",
                 font=("Segoe UI", 10, "bold"), bg=BG, fg=ACCENT,
                 anchor="w").pack(side="left")
        tk.Label(seq_hdr,
                 text="Define what each phone does automatically. Set steps, then assign to groups.",
                 font=FS, bg=BG, fg=T2).pack(side="left", padx=(8, 0))
        seq_row = tk.Frame(self, bg=BG)
        seq_row.pack(fill="x", pady=(4, 0))
        self._seq_steps = []
        tk.Button(seq_row, text="+ Edit Default Sequence",
                  font=("Segoe UI", 10, "bold"),
                  bg=BG3, fg=T1, relief="flat", cursor="hand2",
                  padx=10, pady=6,
                  command=self._edit_sequence).pack(side="left", padx=(0, 8))
        self._seq_lbl = tk.Label(seq_row, text="No steps defined",
                                 font=FS, bg=BG, fg=T3)
        self._seq_lbl.pack(side="left")

    def _open_mumu(self):
        root = _find_mumu_player()
        if root:
            for candidate in (
                "MuMuPlayer.exe",
                Path("nx_main") / "MuMuPlayer.exe",
                Path("nx_main") / "MuMuNxMain.exe",
            ):
                exe = root / candidate
                if exe.exists():
                    try:
                        subprocess.Popen([str(exe)],
                                         creationflags=subprocess.CREATE_NO_WINDOW if IS_WIN else 0)
                        return
                    except Exception:
                        pass
        messagebox.showinfo(
            "MuMuPlayer Not Found",
            "Could not launch MuMuPlayer automatically.\n\n"
            "Please open MuMuPlayer from your Start menu or taskbar."
        )

    def _edit_sequence(self):
        dlg = PerPhoneSequenceEditor(
            self,
            serial="default",
            phone_name="Default Sequence (applied to all phones)",
            steps_list=self._seq_steps,
        )
        self.wait_window(dlg)
        n = len(self._seq_steps)
        self._seq_lbl.config(
            text=f"{n} step{'s' if n != 1 else ''} defined" if n else "No steps defined",
            fg=T1 if n else T3,
        )
        state["default_steps"] = list(self._seq_steps)

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
        if state.get("use_mumu"):
            self._mumu_panel.pack(fill="x", pady=(6, 0))
            self._log_write("MuMuPlayer ARM64 mode — no AVD creation needed.\n"
                            "Open MuMuPlayer, create instances, then click Next →\n")
            return
        else:
            if self._mumu_panel.winfo_manager():
                self._mumu_panel.pack_forget()

        self._pick_count(state.get("num_phones", 3))
        prefix = state.get("phone_prefix", "CPharm_Phone")
        existing = [a for a in list_avds() if a.startswith(prefix + "_")]
        if not existing:
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
        def _do():
            self._log_box.config(state="normal")
            self._log_box.insert("end", text)
            self._log_box.see("end")
            self._log_box.config(state="disabled")
        self.after(0, _do)

    def _delete_phones(self):
        all_avds = list_avds()
        to_delete = [a for a in all_avds if "cpharm" in a.lower()]
        prefix = state.get("phone_prefix", "CPharm_Phone")
        to_delete += [a for a in all_avds if a.startswith(prefix + "_") and a not in to_delete]
        if not to_delete:
            self._log_write("No CPharm phones found to delete.\n")
            return
        if not messagebox.askyesno(
            "Delete phones?",
            f"Delete {len(to_delete)} AVD(s)?\n\n" + "\n".join(to_delete)
        ):
            return
        self._progress_lbl.config(text="Deleting…", fg=YELLOW)
        def go():
            avdmgr = sdk_tool("avdmanager")
            flags = subprocess.CREATE_NO_WINDOW if IS_WIN else 0
            deleted = []
            for avd in to_delete:
                self._log_write(f"  Deleting {avd}…\n")
                try:
                    subprocess.run(
                        [avdmgr, "delete", "avd", "-n", avd],
                        capture_output=True, timeout=30,
                        env=_sdk_env(), creationflags=flags,
                    )
                    deleted.append(avd)
                except Exception as e:
                    self._log_write(f"  ⚠ {avd}: {e}\n")
            state["avds"] = []
            self._done = False
            msg = f"✅ Deleted {len(deleted)} phone(s)" if deleted else "❌ Nothing deleted"
            clr = GREEN if deleted else RED
            self.after(0, lambda m=msg, c=clr: self._progress_lbl.config(text=m, fg=c))
            self._log_write(f"Deleted {len(deleted)} phone(s). Ready to create fresh.\n")
        threading.Thread(target=go, daemon=True).start()

    def _create(self):
        n = state.get("num_phones", 3)
        prefix = state.get("phone_prefix", "CPharm_Phone")
        self._create_btn.config(state="disabled", text=" Creating phones… ")
        self._log_write(f"Creating {n} virtual phone(s). This may take 10–30 minutes.\n\n")

        def go():
            created = []
            for i in range(1, n + 1):
                name = f"{prefix}_{i}"
                self.after(0, lambda t=f"Creating {i}/{n}: {name}…":
                           self._progress_lbl.config(text=t, fg=YELLOW))
                self._log_write(f"══ Phone {i} of {n}: {name} ══\n")
                try:
                    ok, err = create_avd(name, log_fn=self._log_write)
                except Exception as exc:
                    ok, err = False, str(exc)
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
                self.after(0, lambda t=f"✅  {total} phone(s) ready!":
                           self._progress_lbl.config(text=t, fg=GREEN))
            else:
                self._log_write("\n❌  No phones were created. Check errors above.\n")
                self.after(0, lambda: self._progress_lbl.config(
                    text="❌  Failed — check log", fg=RED))

            btn_txt = "✅  Phones Created!" if created else "  ▶  Try Again  "
            self.after(0, lambda t=btn_txt: self._create_btn.config(state="normal", text=t))

        threading.Thread(target=go, daemon=True).start()

    def can_advance(self):
        if state.get("use_mumu"):
            return True
        avds = state.get("avds", [])
        if avds:
            return True
        prefix = state.get("phone_prefix", "CPharm_Phone")
        running = [a for a in list_avds()
                   if a.startswith(prefix + "_") or a.startswith("CPharm_Phone_")]
        if running:
            state["avds"] = running
            return True
        messagebox.showinfo(
            "Phones not created yet",
            "Click 'Create Phone Farm' and wait for it to finish.\n\n"
            "This downloads Android 14 and sets up virtual phones — only happens once."
        )
        return False


# ─── page 4: boot phones ──────────────────────────────────────────────────────


class BootPage(PageBase):
    def __init__(self, parent):
        super().__init__(parent)
        self._server_proc = None
        self.header(
            "Step 3 — Start the Phones",
            "Boots your virtual phones. First boot takes 2–5 minutes per phone."
        )

        # Start Phones
        phone_ctrl = tk.Frame(self, bg=BG3, padx=14, pady=12)
        phone_ctrl.pack(fill="x", pady=(0, 8))
        tk.Label(phone_ctrl, text="Start Phones",
                 font=("Segoe UI", 11, "bold"),
                 bg=BG3, fg=ACCENT, anchor="w").pack(fill="x")
        tk.Label(phone_ctrl,
                 text="Boot the virtual phones. First boot takes 2–5 min per phone.",
                 font=FS, bg=BG3, fg=T2, anchor="w").pack(fill="x", pady=(2, 8))
        phone_row = tk.Frame(phone_ctrl, bg=BG3)
        phone_row.pack(fill="x", pady=(4, 8))
        self._boot_btn = tk.Button(phone_row, text="▶  Start All Phones",
                                  font=("Segoe UI", 10, "bold"),
                                  bg=GREEN, fg=BG, relief="flat",
                                  cursor="hand2", command=self._boot_all)
        self._boot_btn.pack(side="left", padx=(0, 8))
        self._stop_btn = tk.Button(phone_row, text="■  Stop All",
                                  font=("Segoe UI", 10, "bold"),
                                  bg=RED, fg=BG, relief="flat",
                                  cursor="hand2", command=self._stop_all,
                                  state="disabled")
        self._stop_btn.pack(side="left")
        self._overall_lbl = tk.Label(phone_row, text="",
                                     font=FS, bg=BG3, fg=T2)
        self._overall_lbl.pack(side="left", padx=10)

        # Chrome setup + URL test
        chrome_box = tk.Frame(self, bg=BG3, padx=14, pady=10)
        chrome_box.pack(fill="x", pady=(0, 8))
        tk.Label(chrome_box,
                 text="Fix Chrome  (run once after phones boot)",
                 font=("Segoe UI", 10, "bold"), bg=BG3, fg=YELLOW,
                 anchor="w").pack(fill="x")
        tk.Label(chrome_box,
                 text="Disables first-run screens so URLs open instantly every time.",
                 font=FS, bg=BG3, fg=T2, anchor="w").pack(fill="x", pady=(2, 6))
        chrome_ctrl = tk.Frame(chrome_box, bg=BG3)
        chrome_ctrl.pack(fill="x")
        self._chrome_btn = tk.Button(chrome_ctrl,
                                   text="🔧  Setup Chrome on All Phones",
                                   font=("Segoe UI", 10, "bold"),
                                   bg=YELLOW, fg=BG, relief="flat",
                                   cursor="hand2", command=self._setup_chrome_all)
        self._chrome_btn.pack(side="left", padx=(0, 10))
        self._chrome_lbl = tk.Label(chrome_ctrl, text="", font=FS, bg=BG3, fg=T2)
        self._chrome_lbl.pack(side="left")
        # Test URL row
        test_row = tk.Frame(chrome_box, bg=BG3)
        test_row.pack(fill="x", pady=(8, 0))
        tk.Label(test_row, text="Test URL on Phone 1:",
                 font=FS, bg=BG3, fg=T2).pack(side="left")
        self._test_url_var = tk.StringVar(value="https://google.com")
        tk.Entry(test_row, textvariable=self._test_url_var, font=FM,
                 bg=BG2, fg=T1, insertbackground=T1, relief="flat",
                 width=34).pack(side="left", padx=6)
        tk.Button(test_row, text="▶ Open",
                  font=("Segoe UI", 10, "bold"),
                  bg=ACCENT, fg=BG, relief="flat", cursor="hand2",
                  command=self._test_url).pack(side="left")

        # Summary
        summary_outer = tk.Frame(self, bg=BG2, padx=12, pady=10)
        summary_outer.pack(fill="x", pady=(0, 10))
        tk.Label(summary_outer, text="Your phones:",
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
                                        cursor="hand2", command=self._start_server)
        self._btn_srv_start.pack(side="left", padx=(0, 8))
        self._btn_srv_stop = tk.Button(srv_row, text="■  Stop Server",
                                       font=("Segoe UI", 10, "bold"),
                                       bg=RED, fg=BG, relief="flat",
                                       cursor="hand2", state="disabled",
                                       command=self._stop_server)
        self._btn_srv_stop.pack(side="left")
        self._srv_lbl = tk.Label(srv_row, text="Server not running",
                                  font=FS, bg=BG3, fg=T2)
        self._srv_lbl.pack(side="left", padx=10)

    
        # Schedule
        sched = tk.Frame(self, bg=BG3, padx=14, pady=12)
        sched.pack(fill="x", pady=(0, 8))
        tk.Label(sched, text="C — Daily Schedule",
                 font=("Segoe UI", 11, "bold"), bg=BG3,
                 fg=PURPLE, anchor="w").pack(fill="x")
        tk.Label(sched,
                 text="Automate hits spread randomly across 24 hours per phone.",
                 font=FS, bg=BG3, fg=T2, anchor="w", wraplength=640).pack(
                     fill="x", pady=(2, 8))

        sched_row = tk.Frame(sched, bg=BG3)
        sched_row.pack(fill="x")
        tk.Label(sched_row, text="Hits/day:", font=FS, bg=BG3, fg=T2).pack(side="left")
        self._sched_hits_var = tk.IntVar(value=720)
        tk.Entry(sched_row, textvariable=self._sched_hits_var, font=FM,
                 bg=BG2, fg=T1, insertbackground=T1, relief="flat",
                 width=8).pack(side="left", padx=6)
        tk.Label(sched_row, text="per phone", font=FS, bg=BG3, fg=T2).pack(side="left")
        self._sched_btn = tk.Button(sched_row,
                                   text="▶ Start Schedule",
                                   font=("Segoe UI", 10, "bold"),
                                   bg=PURPLE, fg=BG, relief="flat",
                                   cursor="hand2", command=self._start_schedule)
        self._sched_btn.pack(side="left", padx=(8, 0))
        self._sched_lbl = tk.Label(sched_row, text="", font=FS, bg=BG3, fg=T2)
        self._sched_lbl.pack(side="left", padx=8)



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
                                  cursor="hand2", command=self._run_groups)
        self._btn_run.pack(side="left", padx=(0, 8))
        self._btn_stop_grp = tk.Button(grp_row, text="■  Stop All Groups",
                                       font=("Segoe UI", 10, "bold"),
                                       bg=RED, fg=BG, relief="flat",
                                       cursor="hand2", state="disabled",
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
        # Status frame and phone list — referenced by on_enter / _boot_all / _stop_all
        self._status_frame = tk.Frame(self, bg=BG)
        self._status_frame.pack(fill="both", expand=True, pady=4)
        self._status_rows = {}
        self._phones = []          # currently booted phone serials
        self._emu_procs = []       # emulator subprocess handles
        tk.Button(misc, text="Open Dashboard in Browser",
                  font=FS, bg=BG3, fg=T1, relief="flat", cursor="hand2",
                  command=lambda: webbrowser.open(
                      f"http://localhost:{DASHBOARD_PORT}")).pack(side="left", padx=(0, 8))
        tk.Button(misc, text="💾 Save Config",
                  font=FS, bg=BG3, fg=T1, relief="flat", cursor="hand2",
                  command=self._save).pack(side="left")

    def on_enter(self):
        self._rebuild_grid()

        if state.get("use_mumu"):
            self._overall_lbl.config(text="Connecting to MuMu instances…", fg=YELLOW)
            def _mumu_scan():
                connected = _connect_mumu_phones(log_fn=self._log_write)
                if connected:
                    mgr = _find_mumu_manager()
                    instances = _mumu_get_instances(mgr) if mgr else []
                    name_map = {inst["adb_serial"]: inst["name"] for inst in instances}
                    phones = [{"serial": s, "name": name_map.get(s, f"MuMu-{i}")}
                              for i, s in enumerate(connected)]
                    state["phones"] = phones
                    self.after(0, lambda: (
                        self._overall_lbl.config(
                            text=f"✅  {len(phones)} MuMu phone(s) connected!", fg=GREEN),
                        self._rebuild_grid(),
                    ))
                    for s in connected:
                        row_lbl = self._status_rows.get(s)
                        if row_lbl:
                            self.after(0, lambda lbl=row_lbl: lbl.config(text="✅  Running", fg=GREEN))
                else:
                    self.after(0, lambda: self._overall_lbl.config(
                        text="No MuMu phones found — open MuMuPlayer and start your instances first",
                        fg=YELLOW))
            threading.Thread(target=_mumu_scan, daemon=True).start()
            return

        devs = list_adb_devices()

        # Include physical USB phones and already-running emulators
        already = []
        for d in devs:
            s = d["serial"]
            if s.startswith("emulator-") or ":" in s:
                already.append(d)
            else:
                already.append(d)
        if already:
            state["phones"] = already
            n = len(already)
            self._overall_lbl.config(
                text=f"✅  {n} phone(s) already connected!",
                fg=GREEN)
            avds = state.get("avds", [])
            for d in already:
                serial = d["serial"]
                avd_name = None
                if serial.startswith("emulator-"):
                    try:
                        port = int(serial.split("-")[1])
                        idx  = (port - 5554) // 2
                        avd_name = avds[idx] if idx < len(avds) else None
                    except (IndexError, ValueError):
                        pass
                row_lbl = self._status_rows.get(avd_name or serial)
                if row_lbl:
                    row_lbl.config(text="✅  Running", fg=GREEN)

    def _rebuild_grid(self):
        for w in self._status_frame.winfo_children():
            w.destroy()
        self._status_rows.clear()

        if state.get("use_mumu"):
            mgr = _find_mumu_manager()
            if mgr:
                instances = _mumu_get_instances(mgr)
                for inst in instances:
                    key = inst["adb_serial"]
                    row = tk.Frame(self._status_frame, bg=BG2, padx=12, pady=7,
                                   highlightthickness=1, highlightbackground=BORDER)
                    row.pack(fill="x", pady=2)
                    tk.Label(row, text="🎮", font=("Segoe UI", 14),
                             bg=BG2).pack(side="left")
                    tk.Label(row, text=inst["name"], font=FB, bg=BG2, fg=T1,
                             width=26, anchor="w").pack(side="left")
                    tk.Label(row, text=f"ADB: {key}", font=FM, bg=BG2,
                             fg=T3, width=22, anchor="w").pack(side="left")
                    lbl = tk.Label(row, text="Not started", font=FS, bg=BG2, fg=T3)
                    lbl.pack(side="left", padx=6)
                    self._status_rows[key] = lbl
            return

        # AVD emulator rows
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

        # Physical USB phones
        for d in list_adb_devices():
            s = d["serial"]
            if s.startswith("emulator-") or ":" in s:
                continue
            row = tk.Frame(self._status_frame, bg=BG2, padx=12, pady=7,
                           highlightthickness=1, highlightbackground=BORDER)
            row.pack(fill="x", pady=2)
            tk.Label(row, text="📲", font=("Segoe UI", 14),
                     bg=BG2).pack(side="left")
            tk.Label(row, text=d["name"], font=FB, bg=BG2, fg=T1,
                     width=26, anchor="w").pack(side="left")
            tk.Label(row, text=f"USB: {s}", font=FM, bg=BG2,
                     fg=T3, width=20, anchor="w").pack(side="left")
            lbl = tk.Label(row, text="✅  Connected", font=FS, bg=BG2, fg=GREEN)
            lbl.pack(side="left", padx=6)
            self._status_rows[s] = lbl

    def _log_write(self, text):
        def _do():
            self._log_box.config(state="normal")
            self._log_box.insert("end", text)
            self._log_box.see("end")
            self._log_box.config(state="disabled")
        self.after(0, _do)

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
            self._log_write("Server starting…\n")
            self._btn_srv_start.config(state="disabled")
            self._btn_srv_stop.config(state="normal")
            self._srv_lbl.config(text="⏳ Starting…", fg=YELLOW)

            def check():
                time.sleep(3)
                proc = self._server_proc
                if proc and proc.poll() is None:
                    self.after(0, lambda: (
                        self._srv_lbl.config(text="✅ Server running", fg=GREEN),
                        self._btn_run.config(state="normal"),
                        self._log_write("Server is up! Click Run All Groups to start.\n"),
                    ))
                else:
                    self.after(0, lambda: (
                        self._srv_lbl.config(text="❌ Crashed — check terminal", fg=RED),
                        self._btn_srv_start.config(state="normal"),
                        self._btn_srv_stop.config(state="disabled"),
                    ))

            threading.Thread(target=check, daemon=True).start()
        except Exception as e:
            messagebox.showerror("Failed", str(e))

    def _boot_all(self):
        # ── MuMuPlayer mode ────────────────────────���──────────────────────────
        if state.get("use_mumu"):
            self._boot_btn.config(state="disabled", text="Connecting…")
            self._log_write("MuMuPlayer mode — launching and connecting instances…\n")
            def go_mumu():
                mgr = _find_mumu_manager()
                if not mgr:
                    self._log_write(
                        "❌ MuMuManager.exe not found.\n"
                        f"   Install MuMuPlayer ARM from: {MUMU_DOWNLOAD_URL}\n"
                    )
                    self.after(0, lambda: (
                        self._boot_btn.config(state="normal", text="▶  Start All Phones"),
                        self._overall_lbl.config(text="❌ MuMuPlayer not installed", fg=RED),
                    ))
                    return

                instances = _mumu_get_instances(mgr)
                if not instances:
                    self._log_write(
                        "❌ No MuMuPlayer instances found.\n"
                        "   Open MuMuPlayer → click ⧉ (multi-window icon) → '+ New Instance'\n"
                        "   Create your phones there, then click Start again.\n"
                    )
                    self.after(0, lambda: (
                        self._boot_btn.config(state="normal", text="▶  Start All Phones"),
                        self._overall_lbl.config(text="❌ No MuMu instances", fg=RED),
                    ))
                    return

                phones = []
                for inst in instances:
                    idx  = inst["index"]
                    name = inst["name"]
                    serial = inst["adb_serial"]
                    self._log_write(f"  Launching {name} (instance {idx})…\n")
                    _mumu_launch(mgr, idx, log_fn=None)

                    # Wait up to 3 min for Android to boot
                    deadline = time.time() + 180
                    booted   = inst.get("started", False)
                    while not booted and time.time() < deadline:
                        time.sleep(4)
                        fresh = _mumu_get_instances(mgr)
                        match = next((x for x in fresh if x["index"] == idx), None)
                        if match and match["started"]:
                            booted = True

                    if not booted:
                        self._log_write(f"  ⚠ {name} slow — trying ADB connect anyway…\n")

                    adb("connect", serial, timeout=10)
                    check = adb("shell", "echo", "ok", serial=serial, timeout=8)
                    if check.strip() == "ok":
                        new_id = rotate_android_id(serial)
                        phones.append({"serial": serial, "name": name})
                        self._log_write(f"  ✅ {name}: {serial}  ID:{new_id[:8]}…\n")
                        row_lbl = self._status_rows.get(serial)
                        if row_lbl:
                            self.after(0, lambda lb=row_lbl: lb.config(text="✅ Running", fg=GREEN))
                    else:
                        self._log_write(f"  ❌ {name}: ADB not responding on {serial}\n")

                state["phones"]      = phones
                state["_emu_procs"]  = []
                n = len(phones)
                self.after(0, lambda: (
                    self._boot_btn.config(state="normal", text="▶  Start All Phones"),
                    self._stop_btn.config(state="disabled" if not phones else "normal"),
                    self._overall_lbl.config(
                        text=f"✅ {n} MuMuPlayer phone(s) ready" if phones else "❌ No phones connected",
                        fg=GREEN if phones else RED),
                ))
                self._log_write(f"\n{n} MuMuPlayer phone(s) ready.\n")
            threading.Thread(target=go_mumu, daemon=True).start()
            return

        # ── AVD emulator mode ─────────────────────��───────────────────────────
        avds = state.get("avds", [])

        # Also include any physical USB phones already attached
        usb_phones = [d for d in list_adb_devices()
                      if not d["serial"].startswith("emulator-") and ":" not in d["serial"]]
        if usb_phones and not avds:
            state["phones"] = usb_phones
            n = len(usb_phones)
            self._overall_lbl.config(text=f"✅ {n} USB phone(s) connected", fg=GREEN)
            self._log_write(f"✅ {n} physical USB phone(s) already connected.\n")
            return

        if not avds:
            messagebox.showerror("No phones",
                                 "Go back to Step 2 and create phones first.\n\n"
                                 "Or connect a physical Android phone via USB — "
                                 "the wizard will detect it automatically.")
            return

        # ARM64 preflight: if emulator.exe is x64 on an ARM64 host, fail immediately
        if IS_WIN and "arm64" in _machine_arch():
            sdk = state.get("sdk_path") or find_sdk()
            emu_ok = False
            if sdk:
                emu_exe = Path(sdk) / "emulator" / "emulator.exe"
                if emu_exe.exists() and _pe_machine_type(str(emu_exe)) == 0xAA64:
                    emu_ok = True
            if not emu_ok:
                messagebox.showerror(
                    "MuMuPlayer required",
                    "ARM64 (Snapdragon) detected — Google has no Windows ARM64 emulator.\n\n"
                    "Go back to Step 1 and click  ✅ Use MuMuPlayer ARM64  to switch modes.\n\n"
                    f"Download MuMuPlayer ARM:  {MUMU_DOWNLOAD_URL}",
                )
                self._boot_btn.config(state="normal", text="▶  Start All Phones")
                return

        self._boot_btn.config(state="disabled", text="Starting…")
        self._stop_btn.config(state="normal")
        self._log_write(f"Starting {len(avds)} phone(s)...\n")

        def go():
            procs = []          # (avd_name, serial, proc, log_path)
            phones = []
            for i, avd in enumerate(avds):
                port = 5554 + i * 2
                serial = f"emulator-{port}"
                self.after(0, lambda a=avd: (
                    self._overall_lbl.config(text=f"Launching {a}...", fg=YELLOW)
                ))
                try:
                    proc, log_path = start_emulator(avd, port)
                except Exception as e:
                    self._log_write(f"  ❌ {avd} start failed: {e}\n")
                    continue

                # Detect instant crash (proc exits before we even wait for boot)
                time.sleep(1.5)
                crash = proc.poll()
                if crash is not None:
                    # Emulator exited immediately — read its output for the error
                    err_lines = []
                    if log_path and Path(log_path).exists():
                        try:
                            err_lines = Path(log_path).read_text(errors="ignore").splitlines()[-12:]
                        except Exception:
                            pass
                    err_msg = "\n".join(err_lines) if err_lines else f"(exit code {crash})"
                    self._log_write(
                        f"  ❌ {avd} CRASHED immediately (exit {crash}):\n"
                        f"     {err_msg[:300]}\n"
                        f"     Log: {log_path or 'N/A'}"
                    )
                    self.after(0, lambda a=avd: (
                        self._overall_lbl.config(text=f"❌ {a} crashed", fg=RED)
                    ))
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    continue

                procs.append((avd, serial, proc, log_path))
                self._log_write(
                    f"  Waiting for {avd} to boot...\n"
                    f"  (SwiftShader GPU — expect 10-20 min on first boot)\n")
                if log_path and Path(log_path).exists():
                    self._log_write(f"  Emulator log: {log_path}\n")
                ok = wait_for_boot(serial, timeout=1200, log_fn=self._log_write)
                if not ok:
                    self._log_write(f"  ⚠ {avd} slow — waiting extra 10 min...\n")
                    ok = wait_for_boot(serial, timeout=600, log_fn=self._log_write)
                if ok:
                    new_id = rotate_android_id(serial)
                    phones.append({"serial": serial, "name": avd})
                    try:
                        setup_chrome(serial)
                    except Exception:
                        pass
                    self._log_write(
                        f"  ✅ {avd} ready! Android ID: {new_id[:8]}...\n")
                    self.after(0, lambda a=avd: (
                        self._overall_lbl.config(text=f"✅ {a} running", fg=GREEN)
                    ))
                else:
                    # Boot timeout — dump emulator log for diagnosis
                    err_lines = []
                    if log_path and Path(log_path).exists():
                        try:
                            err_lines = Path(log_path).read_text(errors="ignore").splitlines()[-20:]
                        except Exception:
                            pass
                    err_msg = "\n".join(err_lines) if err_lines else "(no log)"
                    self._log_write(
                        f"  ❌ {avd} BOOT TIMED OUT.\n"
                        f"     Enable Windows Hypervisor Platform:\n"
                        f"       Settings → Turn Windows features on/off → Windows Hypervisor Platform ✅\n"
                        f"     Emulator log:\n"
                        f"     {err_msg[:400]}")
                    self.after(0, lambda a=avd: (
                        self._overall_lbl.config(text=f"❌ {a} timed out", fg=RED)
                    ))

            state["phones"] = phones
            state["_emu_procs"] = [p for _, _, p, _ in procs]
            n = len(phones)
            self.after(0, lambda: (
                self._boot_btn.config(state="normal", text="▶  Start All Phones"),
                self._stop_btn.config(
                    state="disabled" if not phones else "normal"),
                self._overall_lbl.config(
                    text=f"✅ {n}/{len(avds)} phone(s) running" if phones
                        else "❌ No phones booted",
                    fg=GREEN if phones else RED),
            ))
            self._log_write(f"\n{n}/{len(avds)} phone(s) running.\n")

        threading.Thread(target=go, daemon=True).start()

    def _stop_all(self):
        for proc in state.get("_emu_procs", []):
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                lf = getattr(proc, "_log_file", None)
                if lf:
                    lf.close()
            except Exception:
                pass
        state["_emu_procs"] = []
        state["phones"] = []
        self._boot_btn.config(state="normal", text="▶  Start All Phones")
        self._stop_btn.config(state="disabled")
        self._overall_lbl.config(text="All phones stopped.", fg=T2)
        self._log_write("All phones stopped.\n")

    def _setup_chrome_all(self):
        phones = state.get("phones", [])
        if not phones:
            messagebox.showwarning("No phones", "Boot phones first.")
            return
        self._chrome_btn.config(state="disabled", text="Setting up Chrome...")
        self._chrome_lbl.config(text="", fg=T2)
        def go():
            for p in phones:
                try:
                    setup_chrome(p["serial"])
                    self._log_write(f"  ✅ {p['name']}: Chrome ready\n")
                except Exception as e:
                    self._log_write(f"  ⚠ {p['name']}: {e}\n")
            self._chrome_btn.config(state="normal", text="🔧  Setup Chrome on All Phones")
            self._log_write("Chrome setup done on all phones.\n")
        threading.Thread(target=go, daemon=True).start()


    def _test_url(self):
        phones = state.get("phones", [])
        if not phones:
            messagebox.showwarning("No phones", "Boot phones first.")
            return
        url = self._test_url_var.get().strip() or "https://google.com"
        self._log_write(f"Opening {url} on {phones[0]['name']}...\n")
        threading.Thread(
            target=lambda: chrome_open_url(phones[0]["serial"], url),
            daemon=True
        ).start()


    def _stop_server(self):
        self._stop_groups()
        if self._server_proc:
            self._server_proc.terminate()
            self._server_proc = None
        self._srv_lbl.config(text="Stopped", fg=T2)
        self._btn_srv_start.config(state="normal")
        self._btn_srv_stop.config(state="disabled")
        self._btn_run.config(state="disabled")
        self._log_write("Server stopped.")

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
        self._log_write("Starting all groups…")
        self._btn_run.config(state="disabled")
        self._btn_stop_grp.config(state="normal")

        def go():
            result = self._api("/api/groups/run", {"groups": state["groups"]})
            if "error" in result:
                self._log_write(f"❌  {result['error']}")
                self._btn_run.config(state="normal")
                self._btn_stop_grp.config(state="disabled")
            else:
                n = result.get("groups", len(state["groups"]))
                self._log_write(f"✅  {n} group(s) running in parallel!")
                self._run_lbl.config(text=f"{n} running", fg=GREEN)

        threading.Thread(target=go, daemon=True).start()

    def _stop_groups(self):
        def go():
            self._api("/api/groups/stop", {})
            self._log_write("All groups stopped.")
            self._run_lbl.config(text="", fg=T2)
            self._btn_run.config(state="normal")
            self._btn_stop_grp.config(state="disabled")

        threading.Thread(target=go, daemon=True).start()


    def _start_schedule(self):
        """Start the daily schedule on all booted phones."""
        hits = self._sched_hits_var.get()
        phones = state.get("phones", [])
        if not phones:
            messagebox.showwarning("No phones running",
                                   "Boot phones first (Step 3).")
            return
        serials = [p["serial"] for p in phones]
        _groups = state.get("groups") or []
        steps = next(iter(_groups[0]["phones"].values()), {}).get("steps", []) if _groups else []
        try:
            import urllib.request
            url = f"http://localhost:{DASHBOARD_PORT}/api/scheduler/start"
            body = json.dumps({"serials": serials, "steps": steps,
                              "hits_per_day": hits}).encode()
            req = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": "application/json"},
                method="POST")
            with urllib.request.urlopen(req, timeout=5) as r:
                result = json.loads(r.read())
            if result.get("ok"):
                self._sched_lbl.config(
                    text=f"Hitting {len(serials)} phones {hits}×/day randomly",
                    fg=GREEN)
                self._log_write("Scheduler started.\n")
            else:
                self._sched_lbl.config(text="Failed: " + str(result), fg=RED)
        except Exception as e:
            self._sched_lbl.config(text=f"Error: {e}", fg=RED)


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
                phones_field = g["phones"]
                # Migrate old list format → per-phone dict format
                if isinstance(phones_field, list):
                    phones_field = {p: {"steps": list(g.get("steps", []))}
                                    for p in phones_field}
                    g["phones"] = phones_field
                elif not isinstance(phones_field, dict):
                    g["phones"] = {}
                # Auto-assign phones if group has no phones
                if not phones_field and state["phones"]:
                    for p in state["phones"]:
                        g["phones"][p["serial"]] = {"steps": list(g.get("steps", []))}
        self._rebuild()

    def _add_group(self):
        n = len(state["groups"]) + 1
        state["groups"].append({
            "name":           f"Group {n}",
            "phones":         {},
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

        # ── Per-phone sequence editor ─────────────────────────────────────
        phones_frame = tk.Frame(card, bg=BG2)
        phones_frame.pack(fill="x", pady=(0, 8))

        tk.Label(phones_frame, text="Per-Phone Sequences",
                 font=("Segoe UI", 10, "bold"), bg=BG2, fg=T1, anchor="w").pack(fill="x")

        phone_map = group.get("phones", {})

        if not state["phones"]:
            tk.Label(phones_frame, text="Go back to Step 3 and start the phones first.",
                     font=FS, bg=BG2, fg=RED).pack(anchor="w")
        else:
            for phone in state["phones"]:
                serial = phone["serial"]
                pdata = phone_map.get(serial, {})
                steps = pdata.get("steps", []) if isinstance(pdata, dict) else []
                psteps = steps  # keep reference for mutation

                row = tk.Frame(phones_frame, bg=BG3, padx=8, pady=5)
                row.pack(fill="x", pady=2)

                # Phone name
                tk.Label(row, text=f"{phone['name']}",
                         font=FM, bg=BG3, fg=T1, width=18, anchor="w").pack(side="left")

                # Step count
                step_cnt = len(psteps)
                cnt_lbl = tk.Label(row, text=f"{step_cnt} steps",
                                   font=FM, bg=BG3,
                                   fg=GREEN if step_cnt else YELLOW, width=10, anchor="w")
                cnt_lbl.pack(side="left", padx=(4, 6))

                # Edit this phone's sequence
                def edit_phone(serial=serial, psteps_ref=[psteps], cnt_lbl=cnt_lbl):
                    phone_name = next(
                        (p["name"] for p in state["phones"] if p["serial"] == serial),
                        serial
                    )
                    win = PerPhoneSequenceEditor(
                        self.app, serial, phone_name, psteps_ref[0])
                    self.app.wait_window(win)
                    cnt_lbl.config(text=f"{len(psteps_ref[0])} steps",
                                   fg=GREEN if psteps_ref[0] else YELLOW)

                tk.Button(row, text="✏ Edit",
                          font=FS, bg=ACCENT, fg=BG, relief="flat",
                          cursor="hand2", command=edit_phone,
                          padx=6, pady=2).pack(side="left", padx=(2, 0))

                # Toggle
                var = tk.BooleanVar(value=serial in phone_map)

                def toggle(v=var, s=serial, ref=[psteps]):
                    if v.get():
                        if s not in group["phones"]:
                            group["phones"][s] = {"steps": ref[0]}
                    else:
                        group["phones"].pop(s, None)

                tk.Checkbutton(row, text="", variable=var, onvalue=True, offvalue=False,
                               command=toggle, font=FB, bg=BG3, fg=T1, selectcolor=GREEN,
                               activebackground=BG3).pack(side="right")

            # ── Clone master button ───────────────────────────────────────────
            if state["phones"] and len(state["phones"]) > 1:
                clone_row = tk.Frame(phones_frame, bg=BG2)
                clone_row.pack(fill="x", pady=(6, 0))
                master_serial = state["phones"][0]["serial"]
                master_steps = (phone_map.get(master_serial, {}) or {}).get("steps", [])

                def clone_all(i=idx, ms=master_serial, msteps=master_steps):
                    # Clone Phone 1's sequence to all phones in this group
                    for phone in state["phones"]:
                        s = phone["serial"]
                        if s in group["phones"]:
                            group["phones"][s]["steps"] = list(msteps)
                        else:
                            group["phones"][s] = {"steps": list(msteps)}
                    self._rebuild()
                    self._log_write(f"[{state['groups'][i]['name']}] Cloned to all phones ✓")

                tk.Button(clone_row, text="📋  Clone to All Phones (Phone 1 is master)",
                          font=FS, bg=PURPLE, fg=BG, relief="flat", cursor="hand2",
                          command=clone_all, padx=8, pady=4).pack(side="left")

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


    def _log_write(self, text: str):
        self._count_lbl.config(text=text, fg=GREEN)
        self.after(3000, self._rebuild)

    def can_advance(self):
        for g in state["groups"]:
            if not g["phones"]:
                messagebox.showwarning(
                    "Empty group",
                    f"'{g['name']}' has no phones assigned.\n"
                    "Tick at least one phone or remove the group.")
                return False
        return True


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
                                        cursor="hand2", command=self._start_server)
        self._btn_srv_start.pack(side="left", padx=(0, 8))
        self._btn_srv_stop = tk.Button(srv_row, text="■  Stop Server",
                                       font=("Segoe UI", 10, "bold"),
                                       bg=RED, fg=BG, relief="flat",
                                       cursor="hand2", command=self._stop_server,
                                       state="disabled")
        self._btn_srv_stop.pack(side="left")
        self._srv_lbl = tk.Label(srv_row, text="Server not running",
                                  font=FS, bg=BG3, fg=T2)
        self._srv_lbl.pack(side="left", padx=10)

    
        # Schedule
        sched = tk.Frame(self, bg=BG3, padx=14, pady=12)
        sched.pack(fill="x", pady=(0, 8))
        tk.Label(sched, text="C — Daily Schedule",
                 font=("Segoe UI", 11, "bold"), bg=BG3,
                 fg=PURPLE, anchor="w").pack(fill="x")
        tk.Label(sched,
                 text="Automate hits spread randomly across 24 hours per phone.",
                 font=FS, bg=BG3, fg=T2, anchor="w", wraplength=640).pack(
                     fill="x", pady=(2, 8))

        sched_row = tk.Frame(sched, bg=BG3)
        sched_row.pack(fill="x")
        tk.Label(sched_row, text="Hits/day:", font=FS, bg=BG3, fg=T2).pack(side="left")
        self._sched_hits_var = tk.IntVar(value=720)
        tk.Entry(sched_row, textvariable=self._sched_hits_var, font=FM,
                 bg=BG2, fg=T1, insertbackground=T1, relief="flat",
                 width=8).pack(side="left", padx=6)
        tk.Label(sched_row, text="per phone", font=FS, bg=BG3, fg=T2).pack(side="left")
        self._sched_btn = tk.Button(sched_row,
                                   text="▶ Start Schedule",
                                   font=("Segoe UI", 10, "bold"),
                                   bg=PURPLE, fg=BG, relief="flat",
                                   cursor="hand2", command=self._start_schedule)
        self._sched_btn.pack(side="left", padx=(8, 0))
        self._sched_lbl = tk.Label(sched_row, text="", font=FS, bg=BG3, fg=T2)
        self._sched_lbl.pack(side="left", padx=8)



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
                                  cursor="hand2", command=self._run_groups)
        self._btn_run.pack(side="left", padx=(0, 8))
        self._btn_stop_grp = tk.Button(grp_row, text="■  Stop All Groups",
                                       font=("Segoe UI", 10, "bold"),
                                       bg=RED, fg=BG, relief="flat",
                                       cursor="hand2", state="disabled",
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
        # Status frame and phone list — referenced by on_enter / _boot_all / _stop_all
        self._status_frame = tk.Frame(self, bg=BG)
        self._status_frame.pack(fill="both", expand=True, pady=4)
        self._status_rows = {}
        self._phones = []          # currently booted phone serials
        self._emu_procs = []       # emulator subprocess handles
        self._overall_lbl = tk.Label(self, text="", font=FS, bg=BG, fg=T2)
        self._overall_lbl.pack(fill="x", pady=(0, 4))
        tk.Button(misc, text="Open Dashboard in Browser",
                  font=FS, bg=BG3, fg=T1, relief="flat", cursor="hand2",
                  command=lambda: webbrowser.open(
                      f"http://localhost:{DASHBOARD_PORT}")).pack(side="left", padx=(0, 8))
        tk.Button(misc, text="💾 Save Config",
                  font=FS, bg=BG3, fg=T1, relief="flat", cursor="hand2",
                  command=self._save).pack(side="left")

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
                f"{len(next(iter(g['phones'].values()), {}).get('steps', []))} steps  |  stagger {g['stagger_secs']}s  |  {rep}")
        self._summary.config(state="normal")
        self._summary.delete("1.0", "end")
        self._summary.insert("end", "\n".join(lines) or "  (no groups)")
        self._summary.config(state="disabled")

    def _log(self, text):
        def _do():
            self._log_box.config(state="normal")
            self._log_box.insert("end", text + "\n")
            self._log_box.see("end")
            self._log_box.config(state="disabled")
        self.after(0, _do)

    _log_write = lambda self, t: self._log(t)

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
            self._log_write("Server starting…")
            self._btn_srv_start.config(state="disabled")
            self._btn_srv_stop.config(state="normal")
            self._srv_lbl.config(text="⏳ Starting…", fg=YELLOW)

            def check():
                time.sleep(3)
                proc = self._server_proc
                if proc and proc.poll() is None:
                    self.after(0, lambda: (
                        self._srv_lbl.config(text="✅ Server running", fg=GREEN),
                        self._btn_run.config(state="normal"),
                        self._log("Server is up! Click Run All Groups to start."),
                    ))
                else:
                    self.after(0, lambda: (
                        self._srv_lbl.config(text="❌ Crashed — check terminal", fg=RED),
                        self._btn_srv_start.config(state="normal"),
                        self._btn_srv_stop.config(state="disabled"),
                    ))

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
        self._log_write("Server stopped.")

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
        self._log_write("Starting all groups…")
        self._btn_run.config(state="disabled")
        self._btn_stop_grp.config(state="normal")

        def go():
            result = self._api("/api/groups/run", {"groups": state["groups"]})
            if "error" in result:
                self._log_write(f"❌  {result['error']}")
                self._btn_run.config(state="normal")
                self._btn_stop_grp.config(state="disabled")
            else:
                n = result.get("groups", len(state["groups"]))
                self._log_write(f"✅  {n} group(s) running in parallel!")
                self._run_lbl.config(text=f"{n} running", fg=GREEN)

        threading.Thread(target=go, daemon=True).start()

    def _stop_groups(self):
        def go():
            self._api("/api/groups/stop", {})
            self._log_write("All groups stopped.")
            self._run_lbl.config(text="", fg=T2)
            self._btn_run.config(state="normal")
            self._btn_stop_grp.config(state="disabled")

        threading.Thread(target=go, daemon=True).start()

    def _start_schedule(self):
        """Start the daily schedule on all booted phones."""
        hits = self._sched_hits_var.get()
        phones = state.get("phones", [])
        if not phones:
            messagebox.showwarning("No phones running",
                                   "Boot phones first (Step 3).")
            return
        serials = [p["serial"] for p in phones]
        _groups = state.get("groups") or []
        steps = next(iter(_groups[0]["phones"].values()), {}).get("steps", []) if _groups else []
        try:
            import urllib.request
            url = f"http://localhost:{DASHBOARD_PORT}/api/scheduler/start"
            body = json.dumps({"serials": serials, "steps": steps,
                              "hits_per_day": hits}).encode()
            req = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": "application/json"},
                method="POST")
            with urllib.request.urlopen(req, timeout=5) as r:
                result = json.loads(r.read())
            if result.get("ok"):
                self._sched_lbl.config(
                    text=f"Hitting {len(serials)} phones {hits}×/day randomly",
                    fg=GREEN)
                self._log_write("Scheduler started.\n")
            else:
                self._sched_lbl.config(text="Failed: " + str(result), fg=RED)
        except Exception as e:
            self._sched_lbl.config(text=f"Error: {e}", fg=RED)


# ─── main wizard ──────────────────────────────────────────────────────────────

class CPharmWizard(tk.Tk):
    PAGES = [
        WelcomePage,
        PrerequisitesPage,
        AndroidStudioPage,
        PhoneFarmPage,
        BootPage,
        PlayStorePage,
        GroupsPage,
        LaunchPage,
    ]
    PAGE_NAMES = [
        "Welcome",
        "Install Tools",
        "Android SDK",
        "Create Phones",
        "Start Phones",
        "Play Store",
        "Groups",
        "Launch!",
    ]

    def __init__(self):
        super().__init__()
        self.title("CPharm Phone Farm Setup")
        self.geometry("820x880")
        self.minsize(760, 700)
        self.config(bg=BG)
        self.resizable(True, True)

        self._build_header()
        self._content = tk.Frame(self, bg=BG)
        self._content.pack(fill="both", expand=True, padx=24, pady=14)
        self._build_footer()

        self._active_canvas = None
        self._page_wrappers = []
        self._pages = []
        for P in self.PAGES:
            outer = tk.Frame(self._content, bg=BG)
            canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
            vsb = tk.Scrollbar(outer, orient="vertical", command=canvas.yview, width=8)
            canvas.configure(yscrollcommand=vsb.set)
            vsb.pack(side="right", fill="y")
            canvas.pack(side="left", fill="both", expand=True)
            page = P(canvas)
            page.app = self
            cwin = canvas.create_window((0, 0), window=page, anchor="nw")
            page.bind("<Configure>",
                      lambda e, c=canvas: c.configure(scrollregion=c.bbox("all")))
            canvas.bind("<Configure>",
                        lambda e, c=canvas, w=cwin: c.itemconfig(w, width=e.width))
            outer.place(relwidth=1, relheight=1)
            self._pages.append(page)
            self._page_wrappers.append((outer, canvas))

        self.bind_all("<MouseWheel>", self._on_mousewheel)
        self.bind_all("<Button-4>",   self._on_mousewheel)
        self.bind_all("<Button-5>",   self._on_mousewheel)

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

    def _on_mousewheel(self, event):
        if not self._active_canvas:
            return
        if event.num == 4:
            self._active_canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            self._active_canvas.yview_scroll(1, "units")
        else:
            self._active_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _show(self, idx):
        for i, (outer, canvas) in enumerate(self._page_wrappers):
            if i == idx:
                outer.lift()
                self._active_canvas = canvas
                canvas.yview_moveto(0)
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
            else:
                self.destroy()

    def _back(self):
        if self._current > 0:
            self._current -= 1
            self._show(self._current)


if __name__ == "__main__":
    app = CPharmWizard()
    app.mainloop()
