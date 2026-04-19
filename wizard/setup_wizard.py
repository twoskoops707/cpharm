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

CMDLINE_TOOLS_URL   = "https://dl.google.com/android/repository/commandlinetools-win-14742923_latest.zip"
SDK_DEFAULT_PATH    = os.path.join(os.environ.get("LOCALAPPDATA", "C:\\"), "Android", "Sdk")
JAVA_DOWNLOAD_URL   = "https://aka.ms/download-jdk/microsoft-jdk-21-windows-aarch64.msi"

# ── prerequisite download URLs ────────────────────────────────────────────────
PYTHON_URL        = "https://www.python.org/ftp/python/3.13.0/python-3.13.0-arm64.exe"
TOR_FALLBACK_URL  = "https://dist.torproject.org/torbrowser/14.0.9/tor-expert-bundle-windows-x86_64-14.0.9.tar.gz"
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

    # 2. Android Studio bundles a JetBrains Runtime (jbr/) — check there first
    #    because users who "can't install Android Studio" may still have it partially.
    jbr_candidates = [
        os.path.join(os.environ.get("PROGRAMFILES",  r"C:\Program Files"),
                     "Android", "Android Studio", "jbr"),
        os.path.join(os.environ.get("LOCALAPPDATA",  ""),
                     "Programs", "Android Studio", "jbr"),
        os.path.join(os.environ.get("PROGRAMFILES",  r"C:\Program Files"),
                     "Android Studio", "jbr"),
        r"C:\Program Files\Android\Android Studio\jbr",
        r"C:\Android\android-studio\jbr",
    ]
    for jbr in jbr_candidates:
        if jbr and Path(jbr, "bin", "java.exe").exists():
            return jbr

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
    Return a copy of os.environ with JAVA_HOME and PATH set so sdkmanager can find Java.
    """
    env = os.environ.copy()
    java_home = _find_java_home()
    if java_home:
        env["JAVA_HOME"] = java_home
        java_bin = str(Path(java_home) / "bin")
        env["PATH"] = java_bin + os.pathsep + env.get("PATH", "")
    return env


def _machine_arch():
    """
    Return the Android ABI for the HOST machine.
    On Windows ARM64, Python is often an x64 build so platform.machine()
    reports 'AMD64' — we check the WOW64 env var to get the real CPU arch.
    """
    if IS_WIN:
        # PROCESSOR_ARCHITEW6432 is set by Windows when a non-native process runs
        wow = os.environ.get("PROCESSOR_ARCHITEW6432", "").upper()
        native = os.environ.get("PROCESSOR_ARCHITECTURE", "").upper()
        arch_str = wow or native
        if "ARM" in arch_str:
            return "arm64-v8a"
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
    Download platform-tools directly via Python urllib, bypassing Java/sdkmanager.
    Uses Google's documented stable URL — no XML parsing needed.
    """
    url  = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
    dest = Path(sdk_path)
    tmp  = dest / "_pt_tmp.zip"
    if log_fn:
        log_fn(f"  Downloading platform-tools directly…\n")
    try:
        urllib.request.urlretrieve(url, tmp)
        with zipfile.ZipFile(tmp, "r") as zf:
            zf.extractall(dest)
        tmp.unlink(missing_ok=True)
        if log_fn:
            log_fn("  platform-tools installed ✅\n")
        return True
    except Exception as e:
        if log_fn:
            log_fn(f"  ❌  platform-tools direct download failed: {e}\n")
        tmp.unlink(missing_ok=True)
        return False


def _direct_download_emulator(sdk_path, log_fn=None):
    """
    Download emulator directly via Python urllib, bypassing Java/sdkmanager.
    Fetches Google's repository manifest XML to find the current emulator URL,
    then downloads and extracts the ZIP.
    """
    import xml.etree.ElementTree as ET
    REPO_XML = "https://dl.google.com/android/repository/repository2-3.xml"
    BASE_URL = "https://dl.google.com/android/repository/"

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

    # Find the emulator Windows ZIP URL in the manifest
    emulator_url = None
    try:
        root = ET.fromstring(xml_data)
        for pkg in root.iter():
            if pkg.get("path") == "emulator":
                for archive in pkg.iter():
                    tag = archive.tag.split("}")[-1] if "}" in archive.tag else archive.tag
                    if tag == "url":
                        val = (archive.text or "").strip()
                        if "windows" in val and val.endswith(".zip"):
                            emulator_url = BASE_URL + val
                            break
                if emulator_url:
                    break
    except ET.ParseError:
        pass

    if not emulator_url:
        matches = re.findall(r"emulator-windows[^\"<\s]+\.zip", xml_data)
        if matches:
            emulator_url = BASE_URL + matches[-1]

    if not emulator_url:
        if log_fn:
            log_fn("  ❌  Emulator URL not found in repository XML.\n")
        return False

    if log_fn:
        log_fn(f"  Emulator URL: {emulator_url}\n")
        log_fn("  Downloading emulator (~300 MB)…\n")

    tmp = Path(sdk_path) / "_emu_tmp.zip"
    try:
        urllib.request.urlretrieve(emulator_url, tmp)
        emu_dest = Path(sdk_path) / "emulator"
        if emu_dest.exists():
            shutil.rmtree(emu_dest)
        with zipfile.ZipFile(tmp, "r") as zf:
            zf.extractall(Path(sdk_path))
        tmp.unlink(missing_ok=True)
        if log_fn:
            log_fn("  emulator installed ✅\n")
        return True
    except Exception as e:
        if log_fn:
            log_fn(f"  ❌  Emulator direct download failed: {e}\n")
        tmp.unlink(missing_ok=True)
        return False


def _add_firewall_exception_for_java():
    """
    Run an elevated PowerShell command to add a Windows Defender Firewall
    outbound rule allowing Java (sdkmanager's runtime) through.
    Shows a UAC prompt — user must click Yes.
    """
    java_home = _find_java_home()
    if not java_home:
        return False
    java_exe = str(Path(java_home) / "bin" / "java.exe").replace("\\", "\\\\")
    ps_cmd = (
        f'New-NetFirewallRule -DisplayName \\"CPharm Java SDK\\" '
        f'-Direction Outbound -Program \\"{java_exe}\\" '
        f'-Action Allow -Profile Any; '
        f'Write-Host \\"Done.\\"'
    )
    try:
        subprocess.Popen(
            ["powershell", "-Command",
             f"Start-Process powershell -Verb RunAs -ArgumentList '-Command {ps_cmd}'"],
            creationflags=subprocess.CREATE_NO_WINDOW if IS_WIN else 0,
        )
        return True
    except Exception:
        return False


def create_avd(name, log_fn=None):
    sdk = state.get("sdk_path") or find_sdk()
    if not sdk:
        return False, "Android SDK not found"

    # avdmanager lives beside sdkmanager in cmdline-tools/latest/bin/
    sdkmgr_path = _canonical_sdkmanager(sdk)
    ext    = ".bat" if IS_WIN else ""
    avdmgr = str(Path(sdkmgr_path).parent / f"avdmanager{ext}")
    arch   = _machine_arch()
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

    # ── accept all licenses first ─────────────────────────────────────────────
    if log_fn:
        log_fn("  Accepting SDK licenses…\n")
    _run_sdkmanager(["--licenses"], sdk, log_fn=None, timeout=60)

    # ── install system image ──────────────────────────────────────────────────
    if log_fn:
        log_fn(f"  Installing Android 14 image — this downloads ~1 GB, please wait…\n")
    ok, out = _run_sdkmanager([image], sdk, log_fn=log_fn, timeout=1200)

    if not ok:
        # Check if image actually landed despite non-zero exit (sdkmanager quirk)
        img_path = Path(sdk) / "system-images" / "android-34" / "google_apis" / arch
        if img_path.exists():
            if log_fn:
                log_fn("  Image present on disk — continuing despite sdkmanager exit code.\n")
        else:
            return False, f"sdkmanager failed. Last output:\n{out[-400:]}"

    # ── create the AVD ────────────────────────────────────────────────────────
    if log_fn:
        log_fn(f"\n  Creating AVD: {name}…\n")

    flags = subprocess.CREATE_NO_WINDOW if IS_WIN else 0
    proc  = subprocess.Popen(
        [avdmgr, "create", "avd", "-n", name,
         "-k", image, "-d", "pixel_6", "--force"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_sdk_env(),
        creationflags=flags,
    )
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
            log_fn(f"  ✅  {name} created.\n")
        return True, ""
    return False, combined or "avdmanager returned non-zero exit code"


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
                [cmd, "--version"], capture_output=True, text=True, timeout=6
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
        self._log_box.config(state="normal")
        self._log_box.insert("end", text + "\n")
        self._log_box.see("end")
        self._log_box.config(state="disabled")

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
        urllib.request.urlretrieve(url, dest, hook)

    # ── checks ────────────────────────────────────────────────────────────────

    def _check_java(self) -> bool:
        ok, _ = run_cmd(["java", "-version"])
        if ok:
            return True
        for pattern in [
            os.path.join(os.environ.get("PROGRAMFILES", ""), "**", "java.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "**", "java.exe"),
        ]:
            import glob
            if glob.glob(pattern, recursive=True):
                return True
        return False

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

    # ── installers ────────────────────────────────────────────────────────────

    def _install_java(self):
        self._set_row("java", "⬇  Downloading…", ACCENT)
        self._log("Downloading Java JDK 21 (ARM64)…")
        tmp = Path(os.environ.get("TEMP", "C:\\Temp")) / "microsoft-jdk-21-arm64.msi"
        self._download(JAVA_DOWNLOAD_URL, tmp, "Java JDK", 2, 18)
        self._set_row("java", "⚙  Installing…", YELLOW)
        self._log("Installing Java (silent, no restart)…")
        ok, out = run_cmd(
            ["msiexec", "/i", str(tmp), "/quiet", "/norestart"],
            timeout=300,
        )
        tmp.unlink(missing_ok=True)
        if ok or self._check_java():
            self._set_row("java", "✅  Done", GREEN)
            self._log("Java installed ✅")
        else:
            self._set_row("java", "❌  Failed", RED)
            self._log(f"Java install failed: {out[-200:]}")

    def _install_python(self):
        self._set_row("python", "⬇  Downloading…", ACCENT)
        self._log("Downloading Python 3.13 (ARM64)…")
        tmp = Path(os.environ.get("TEMP", "C:\\Temp")) / "python-3.13-arm64.exe"
        self._download(PYTHON_URL, tmp, "Python", 20, 36)
        self._set_row("python", "⚙  Installing…", YELLOW)
        self._log("Installing Python (no admin needed, adds to PATH)…")
        ok, out = run_cmd(
            [str(tmp), "/quiet",
             "InstallAllUsers=0", "PrependPath=1", "Include_launcher=0"],
            timeout=300,
        )
        tmp.unlink(missing_ok=True)
        if ok or self._check_python():
            state["python_cmd"] = _find_python() or "python"
            self._set_row("python", "✅  Done", GREEN)
            self._log(f"Python installed ✅  ({state['python_cmd']})")
        else:
            self._set_row("python", "❌  Failed", RED)
            self._log(f"Python install failed: {out[-200:]}")

    def _install_packages(self):
        py = _find_python() or "python"
        state["python_cmd"] = py
        self._set_row("packages", "⚙  Installing…", YELLOW)
        self._set_progress(55, "Installing Python packages…")
        self._log("Running: pip install websockets psutil…")
        ok, out = run_cmd(
            [py, "-m", "pip", "install", "--upgrade", "websockets", "psutil"],
            timeout=120,
        )
        if ok or self._check_packages():
            self._set_row("packages", "✅  Done", GREEN)
            self._log("Packages installed ✅")
        else:
            self._set_row("packages", "❌  Failed", RED)
            self._log(f"pip failed: {out[-300:]}")

    def _install_tor(self):
        install_dir = Path(state.get("cpharm_dir", "") or self._install_dir.get())
        tor_dir = install_dir / "automation" / "tor"
        tor_dir.mkdir(parents=True, exist_ok=True)

        self._set_row("tor", "⬇  Finding latest…", ACCENT)
        self._log("Looking up latest Tor version…")
        tor_url = _fetch_latest_tor_url()
        self._log(f"Downloading: {tor_url}")
        self._set_row("tor", "⬇  Downloading…", ACCENT)

        tmp = tor_dir / "_tor_bundle.tar.gz"
        self._download(tor_url, tmp, "Tor", 58, 72)

        self._set_row("tor", "📦  Extracting…", YELLOW)
        self._log("Extracting Tor…")
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
            self._log("Tor installed ✅")
        else:
            self._set_row("tor", "⚠  Skipped", T3)
            self._log("Tor not found after extract — IP rotation will use system Tor if available.")

    def _install_cpharm(self):
        install_dir = Path(self._install_dir.get())
        if self._check_cpharm():
            self._set_row("cpharm", "✅  Already here", GREEN)
            self._log(f"CPharm files already present at {state.get('cpharm_dir', install_dir)}")
            return

        self._set_row("cpharm", "⬇  Downloading…", ACCENT)
        self._log("Downloading CPharm files from GitHub…")
        tmp = Path(os.environ.get("TEMP", "C:\\Temp")) / "cpharm.zip"
        self._download(CPHARM_ZIP_URL, tmp, "CPharm", 76, 88)

        self._set_row("cpharm", "📦  Extracting…", YELLOW)
        self._log(f"Extracting to {install_dir}…")
        extract_tmp = install_dir.parent / "_cpharm_extract"
        if extract_tmp.exists():
            shutil.rmtree(extract_tmp)
        with zipfile.ZipFile(tmp, "r") as zf:
            zf.extractall(extract_tmp)
        tmp.unlink(missing_ok=True)

        inner = next(extract_tmp.iterdir(), None)
        if inner and inner.is_dir():
            if install_dir.exists():
                shutil.rmtree(install_dir)
            shutil.move(str(inner), str(install_dir))
        shutil.rmtree(extract_tmp, ignore_errors=True)

        if (install_dir / "automation" / "dashboard.py").exists():
            state["cpharm_dir"] = str(install_dir)
            self._set_row("cpharm", "✅  Done", GREEN)
            self._log(f"CPharm installed at {install_dir} ✅")
        else:
            self._set_row("cpharm", "❌  Failed", RED)
            self._log("CPharm files not found after extraction.")

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

            if not self._check_tor():
                self._install_tor()
            else:
                self._set_row("tor", "✅  Already installed", GREEN)

            self._set_progress(74, "")

            if not self._check_cpharm():
                self._install_cpharm()
            else:
                self._set_row("cpharm", "✅  Already here", GREEN)

            self._set_progress(100, "Done!")
            self._log("\n✅  All done. Click Next → to continue.")
            self._ready = True
            self._install_btn.config(
                state="normal",
                text="✅  Ready — click Next →",
                bg=GREEN,
            )
        except Exception as exc:
            self._log(f"\n❌  Error: {exc}")
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
        self._log_box.config(state="normal")
        self._log_box.insert("end", text + "\n")
        self._log_box.see("end")
        self._log_box.config(state="disabled")

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
            self._log(f"\n❌  Unexpected error: {exc}")
            self._set_status("❌", "Something went wrong — see log below.", color=RED)
        finally:
            self._working = False
            if not self._ready:
                self._install_btn.config(state="normal",
                                         text="⬇   Try Again")

    def _do_install(self):
        sdk_path = Path(SDK_DEFAULT_PATH)

        # ── step 0: check if already installed ───────────────────────────────
        self._set_status("🔍", "Checking for existing SDK…", color=T2)
        self._set_progress(5, "Scanning…")
        existing = find_sdk()
        if existing:
            state["sdk_path"] = existing
            self._log(f"SDK already found at:  {existing}")
            # Make sure emulator is actually present — if not, install it
            if not Path(sdk_tool("emulator")).exists():
                self._log("Emulator missing — installing it now…")
                self._install_missing_tools(existing)
                return
            self._finish_ok(existing)
            return

        # ── step 1: Java check ────────────────────────────────────────────────
        self._set_status("☕", "Checking for Java…", color=T2)
        self._set_progress(8, "Checking Java…")
        if not self._has_java():
            self._log("Java not found. The SDK tools need Java to run.")
            self._log(f"Download Java here:  {JAVA_DOWNLOAD_URL}")
            self._set_status(
                "☕",
                "Java is required — please install it first.",
                detail="Click the button below, install Java, then click 'Try Again'.",
                color=YELLOW,
            )
            self._set_progress(0, "")
            self.after(0, self._show_java_button)
            self._working = False
            self._install_btn.config(state="normal", text="⬇   Try Again  (after installing Java)")
            return
        self._log("Java found ✅")

        # ── step 2: create SDK folder ─────────────────────────────────────────
        self._set_status("📁", "Creating SDK folder…", color=T2)
        self._set_progress(12, "Creating folders…")
        tools_dir = sdk_path / "cmdline-tools"
        tools_dir.mkdir(parents=True, exist_ok=True)
        self._log(f"SDK folder:  {sdk_path}")

        # ── step 3: download cmdline-tools zip ────────────────────────────────
        zip_path = sdk_path / "cmdline-tools" / "cmdline-tools.zip"
        if zip_path.exists():
            self._log("Zip already downloaded — skipping download.")
            self._set_progress(50, "Already downloaded.")
        else:
            self._set_status("⬇", "Downloading Android SDK tools…",
                             detail="~130 MB — this takes a minute on slow connections.",
                             color=ACCENT)
            self._log(f"Downloading from:\n  {CMDLINE_TOOLS_URL}")

            def _progress_hook(block_num, block_size, total_size):
                if total_size > 0:
                    pct = min(12 + int(block_num * block_size / total_size * 38), 50)
                    mb_done = block_num * block_size / 1_048_576
                    mb_total = total_size / 1_048_576
                    self._set_progress(pct, f"Downloading…  {mb_done:.0f} / {mb_total:.0f} MB")

            try:
                urllib.request.urlretrieve(CMDLINE_TOOLS_URL, zip_path, _progress_hook)
            except Exception as e:
                self._log(f"Download failed: {e}")
                self._set_status("❌", "Download failed — check internet connection.", color=RED)
                self._set_progress(0, "")
                return

            self._log("Download complete ✅")

        # ── step 4: extract zip ───────────────────────────────────────────────
        self._set_status("📦", "Extracting files…", color=T2)
        self._set_progress(55, "Extracting…")
        self._log("Extracting zip…")

        extract_tmp = tools_dir / "_extract_tmp"
        if extract_tmp.exists():
            shutil.rmtree(extract_tmp)

        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_tmp)

        # The zip contains a single top-level folder called "cmdline-tools"
        # We need it at sdk/cmdline-tools/latest/
        inner = extract_tmp / "cmdline-tools"
        latest_dir = tools_dir / "latest"
        if latest_dir.exists():
            shutil.rmtree(latest_dir)
        shutil.move(str(inner), str(latest_dir))
        shutil.rmtree(extract_tmp)
        zip_path.unlink(missing_ok=True)
        self._log("Extraction done ✅")
        self._set_progress(62, "Extracted.")

        # ── step 5: run sdkmanager to install platform-tools + emulator ───────
        state["sdk_path"] = str(sdk_path)
        self._install_missing_tools(str(sdk_path))

    def _install_missing_tools(self, sdk):
        """
        Install platform-tools and emulator via sdkmanager.
        Uses _run_sdkmanager so Java env is injected and licenses are auto-accepted.
        """
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
        self._log(f"JAVA_HOME: {_find_java_home() or '(not found — Java required)'}")

        # Accept all pending SDK licenses first — required before any install on fresh SDK
        self._log("Accepting SDK licenses…")
        _run_sdkmanager(["--licenses"], sdk, log_fn=self._log, timeout=60)

        # Check what packages sdkmanager can actually see — if emulator doesn't show
        # here it means the remote repo fetch is failing (network/proxy/TLS issue).
        self._set_progress(63, "Checking available packages…")
        self._log("Checking available packages (fetching remote catalog)…")
        _, list_out = _run_sdkmanager(["--list", "--channel=0"], sdk, log_fn=None, timeout=90)
        if list_out:
            emulator_lines = [l for l in list_out.splitlines() if "emulator" in l.lower()]
            if emulator_lines:
                self._log(f"  emulator found in catalog: {emulator_lines[0].strip()}")
            else:
                self._log("  ⚠  'emulator' not found in catalog — remote repo may be unreachable.")
                self._log("     Trying install anyway…")
        else:
            self._log("  ⚠  sdkmanager --list returned no output — network may be blocked.")

        self._set_progress(65, "Running sdkmanager…")
        self._log(f"Installing: {', '.join(missing)}")

        # Use positional package args — NOT --install flag.
        # The --install flag in newer cmdline-tools skips the remote catalog fetch
        # and only searches locally, causing "Failed to find package emulator".
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
                self._log("\n❌  Both sdkmanager and direct download failed.")
                self._log("    Your network is blocking dl.google.com entirely.")
                self._set_status("❌", "Network blocked — see options below", color=RED)
                self._set_progress(0, "")
                self._install_btn.config(state="normal", text="⬇   Try Again")
                self.after(0, self._show_firewall_btn)
                return

        self._log("emulator        ✅")
        self._log("platform-tools  ✅")
        self._finish_ok(sdk)

    def _finish_ok(self, sdk_path):
        self._set_status("✅", "Android SDK is ready!",
                         detail=f"SDK installed at:  {sdk_path}",
                         color=GREEN)
        self._set_progress(100, "Done!")
        self._log(f"\n✅  All good. Click Next → to continue.")
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
            self._log("\nFirewall rule submitted — approve the UAC prompt that appeared.")
            self._log("Then click Try Again.\n")
        else:
            self._log("\n❌  Could not launch PowerShell — add the rule manually:\n")
            self._log('   Windows Security → Firewall → Advanced → Outbound Rules')
            self._log('   → New Rule → Program → browse to java.exe → Allow\n')

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

        sdk = find_sdk()
        if sdk:
            state["sdk_path"] = sdk
            self._path_var.set(sdk)

        sdk = state.get("sdk_path", "")
        if not sdk:
            self._set_status("⬇", "Android SDK not installed yet.",
                             detail="Click the green button above to install it automatically.",
                             color=T2)
            self._ready = False
            return False

        has_emu = Path(sdk_tool("emulator")).exists()
        has_avd = Path(sdk_tool("avdmanager")).exists()
        has_sdk = Path(sdk_tool("sdkmanager")).exists()

        if has_emu and has_avd and has_sdk:
            self._finish_ok(sdk)
            return True

        missing = []
        if not has_emu: missing.append("emulator")
        if not has_avd: missing.append("avdmanager")
        if not has_sdk: missing.append("sdkmanager")
        self._set_status(
            "⚙",
            f"SDK found but missing: {', '.join(missing)}",
            detail="Click the button below to install the missing pieces.",
            color=YELLOW,
        )
        self._install_btn.config(
            state="normal",
            text=f"⬇   Install Missing Tools ({', '.join(missing)})",
            bg=YELLOW,
        )
        self._ready = False
        return False

    def can_advance(self):
        if not self._ready:
            messagebox.showinfo(
                "SDK not ready yet",
                "Click the green 'Install Android SDK' button and wait for it to finish.\n\n"
                "The wizard will download and set everything up automatically."
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
        self._log = tk.Text(log_fr, height=6, font=FM, bg=BG2, fg=T1,
                            relief="flat", state="disabled", wrap="word")
        sb = tk.Scrollbar(log_fr, orient="vertical", command=self._log.yview)
        self._log.configure(yscrollcommand=sb.set)
        self._log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # ── Chrome setup + URL test ───────────────────────────────────────────
        chrome_box = tk.Frame(self, bg=BG3, padx=14, pady=10)
        chrome_box.pack(fill="x", pady=(6, 0))

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
                                     cursor="hand2", padx=14, pady=6,
                                     command=self._setup_chrome_all)
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
                  padx=10, pady=5,
                  command=self._test_url).pack(side="left")

    def _setup_chrome_all(self):
        phones = state.get("phones", [])
        if not phones:
            messagebox.showwarning("No phones running",
                                   "Start the phones first, then setup Chrome.")
            return
        self._chrome_btn.config(state="disabled", text="Setting up Chrome…")
        self._chrome_lbl.config(text="", fg=T2)

        def go():
            ok_count = 0
            for p in phones:
                self._chrome_lbl.config(
                    text=f"Setting up {p['name']}…", fg=YELLOW)
                try:
                    setup_chrome(p["serial"])
                    ok_count += 1
                except Exception as e:
                    self._log_write(f"  Chrome setup error on {p['name']}: {e}\n")
            self._chrome_btn.config(state="normal",
                                    text="🔧  Setup Chrome on All Phones")
            self._chrome_lbl.config(
                text=f"✅  Done ({ok_count}/{len(phones)} phones)",
                fg=GREEN)
            self._log_write(
                f"Chrome setup complete on {ok_count} phone(s). "
                "URLs will now open directly.\n")

        threading.Thread(target=go, daemon=True).start()

    def _test_url(self):
        phones = state.get("phones", [])
        if not phones:
            messagebox.showwarning("No phones", "Boot phones first.")
            return
        serial = phones[0]["serial"]
        url    = self._test_url_var.get().strip() or "https://google.com"
        self._log_write(f"Opening {url} on {phones[0]['name']}…\n")
        threading.Thread(
            target=lambda: chrome_open_url(serial, url), daemon=True).start()

    def on_enter(self):
        self._rebuild_grid()
        devs = list_adb_devices()
        running = [d for d in devs if d["serial"].startswith("emulator-")]
        if running:
            state["phones"] = running
            self._overall_lbl.config(
                text=f"✅  {len(running)} phone(s) already running!",
                fg=GREEN)
            avds = state.get("avds", [])
            for d in running:
                # Status rows are keyed by AVD name, not ADB device model name.
                # Derive AVD index from the emulator serial (emulator-5554 → idx 0, etc.)
                try:
                    port = int(d["serial"].split("-")[1])
                    idx  = (port - 5554) // 2
                    avd_name = avds[idx] if idx < len(avds) else None
                except (IndexError, ValueError):
                    avd_name = None
                row_lbl = self._status_rows.get(avd_name) if avd_name else None
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
