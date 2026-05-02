"""
CPharm Setup Wizard — guided onboarding for a virtual Android phone farm on Windows.

Intended journey (high level):
  1. Welcome — what you get (farm, Tor, Play closed testing, groups, scheduler).
  2. Install tools — Java/Python/Tor/CPharm tree into a chosen folder (merge-safe same-dir).
  3. Choose runtime — full Android SDK + emulators, or MuMu/nemux ARM on Snapdragon hosts.
  4. Create devices — Pixel AVDs via sdkmanager/avdmanager, or MuMu instances you spawn yourself.
  5. Connect & verify — ADB, boot emulators or MuMu phones, Chrome smoke checks; logs stay here.
  6. Automation — default step sequence (URLs, taps, Tor rotate); seeds Groups assignments.
  7. Groups — stagger/repeat, per-phone overrides, clone-from-master (unchanged behavior).
  8. Play Console — closed-testing checklist when you ship builds to testers.
  9. Launch — start dashboard server, run groups, daily scheduler, open local dashboard URL.

State keys `state[...]` hold install path, `use_mumu`, `sdk_path`, `avds`, `phones`, `groups`,
`default_steps`, and merge install uses `_install_targets_live_tree` + `_merge_zip_tree_safe` / `_merge_dir_into`.

Build (one-file), from the ``wizard`` directory:
    pip install pyinstaller pillow
    pyinstaller --onefile --windowed --name CPharmSetup --hidden-import wizard_theme ^
      --add-data "assets;assets" setup_wizard.py
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

from wizard_theme import (
    ACCENT,
    BG,
    BG2,
    BG3,
    BG4,
    BORDER,
    BORDER_STRONG,
    CPharm_TSCROLL,
    FB,
    FH,
    FM,
    FS,
    FONT_CAPTION,
    FONT_HERO,
    FONT_H2,
    FONT_LEAD,
    GREEN,
    ON_ACCENT,
    PURPLE,
    RED,
    SP,
    T1,
    T2,
    T3,
    YELLOW,
    _attach_readonly_log_text,
    _style_scrollbars,
    draw_round_rect,
    load_icon,
    style_danger_button,
    style_primary_button,
    style_secondary_button,
)

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

# MuMu / Netease CLI: newer installs use nemux-shell-winui.Manager.exe; older builds use MuMuManager.exe
MUMU_MANAGER_PRIMARY = "nemux-shell-winui.Manager.exe"
MUMU_MANAGER_LEGACY = "MuMuManager.exe"
MUMU_MANAGER_EXES = (MUMU_MANAGER_PRIMARY, MUMU_MANAGER_LEGACY)


def _dev_repo_root() -> Path:
    """Directory containing ``automation/`` when running the wizard from source."""
    return Path(__file__).resolve().parent.parent


def _cpharm_install_root() -> Path:
    """
    Root directory that contains ``automation/dashboard.py`` (checkout or install tree).

    When ``sys.frozen`` (PyInstaller), prefers ``CPHARM_HOME``, then the directory
    containing ``sys.executable``, then ``sys._MEIPASS`` if the bundle includes
    ``automation/``. Unfrozen builds fall back to the repo root next to ``wizard/``.
    """
    env = os.environ.get("CPHARM_HOME", "").strip()
    if env:
        p = Path(env).expanduser().resolve()
        if (p / "automation" / "dashboard.py").exists():
            return p

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        if (exe_dir / "automation" / "dashboard.py").exists():
            return exe_dir
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            mp = Path(meipass)
            if (mp / "automation" / "dashboard.py").exists():
                return mp

    return _dev_repo_root()


def _wizard_runtime_root() -> Path:
    """Repo/install root containing automation/ and wizard/ for this running wizard."""
    return _cpharm_install_root()


def _install_targets_live_tree(install_dir: Path) -> bool:
    """True if we must not delete/replace install_dir wholesale (wizard runs from that tree or below)."""
    try:
        inst = install_dir.resolve()
        rt = _wizard_runtime_root().resolve()
    except OSError:
        return False
    if inst == rt:
        return True
    try:
        return rt.is_relative_to(inst)
    except (ValueError, AttributeError):
        ip, rp = str(inst), str(rt)
        if not ip.endswith(os.sep):
            ip += os.sep
        return rp.lower().startswith(ip.lower())


def _merge_dir_into(src: Path, dst: Path) -> None:
    """Copy src tree into dst; existing files are overwritten. Does not delete dst first."""
    if not src.is_dir():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        s, d = item, dst / item.name
        if s.is_dir():
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)


def _merge_zip_tree_safe(src: Path, dst: Path) -> None:
    """Merge GitHub ZIP extract into dst by walking **src** only.

    Avoids ``shutil.copytree`` merges that can touch nested ``.git`` trees under *dst*
    (WinError 5: Access denied on locked objects/*.git).
    Skips any destination path whose parts contain ``.git`` or ``.svn``.
    """
    if not src.is_dir():
        return
    skip_names = {".git", ".svn", "__pycache__", ".venv", "node_modules"}
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in skip_names]
        rel = Path(root).relative_to(src)
        for fname in files:
            if fname.endswith(".pyc"):
                continue
            sp = Path(root) / fname
            dp = dst / rel / fname
            if ".git" in dp.parts or ".svn" in dp.parts:
                continue
            try:
                dp.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(sp, dp)
            except OSError as e:
                raise OSError(f"merge {sp} -> {dp}: {e}") from e


def _git_repo_roots_for_pull(install_dir: Path) -> list[Path]:
    """Prefer nested clone ``install_dir/cpharm`` if it has .git, else ``install_dir``."""
    roots = []
    nested = install_dir / "cpharm"
    if (nested / ".git").is_dir():
        roots.append(nested)
    if (install_dir / ".git").is_dir() and install_dir not in roots:
        roots.append(install_dir)
    return roots


def _try_git_pull_cpharm(install_dir: Path, log_fn) -> bool:
    """If a git checkout exists, try ``git pull --ff-only`` so we never overwrite .git via ZIP."""
    for repo in _git_repo_roots_for_pull(install_dir):
        log_fn(f"  Git repo found at {repo} — trying git pull --ff-only…\n")
        ok, out = run_cmd(
            ["git", "-C", str(repo), "pull", "--ff-only"],
            timeout=180,
        )
        if ok and (repo / "automation" / "dashboard.py").exists():
            log_fn("  Updated from GitHub via git pull ✅\n")
            return True
        log_fn(f"  git pull failed or incomplete: {(out or '')[-500:]}\n")
    return False


def _find_cpharm_dashboard_root(install_dir: Path) -> Path | None:
    """Directory containing ``automation/dashboard.py`` (top-level or nested ``cpharm/``)."""
    for base in (install_dir, install_dir / "cpharm"):
        if (base / "automation" / "dashboard.py").exists():
            return base.resolve()
    return None


def _is_mumu_manager_cli_path(path: Path | str) -> bool:
    n = Path(path).name.lower()
    return n in {x.lower() for x in MUMU_MANAGER_EXES}


def _is_mumu_gui_path(path: Path | str) -> bool:
    """True if path looks like the MuMu Player GUI, not the shell CLI manager."""
    n = Path(path).name.lower()
    if _is_mumu_manager_cli_path(path):
        return False
    return n in ("mumuplayer.exe", "mumunxmain.exe")


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
    "use_mumu": False,
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
            ("Move up",     self._up,     BG3),
            ("Move down",   self._dn,     BG3),
        ]:
            b = tk.Button(ctrl, text=text, command=cmd,
                          bg=color, fg=ON_ACCENT if color != BG3 else T1,
                          font=("Segoe UI", 10, "bold"),
                          relief="flat", cursor="hand2",
                          padx=10, pady=6, bd=0, highlightthickness=0)
            if color == GREEN:
                b.configure(activebackground="#22c55e", activeforeground=ON_ACCENT)
            elif color == RED:
                style_danger_button(b)
            else:
                style_secondary_button(b)
            b.pack(side="left", padx=(0, 6))

        fr = tk.Frame(self, bg=BG2)
        fr.pack(fill="both", expand=True, padx=16, pady=8)
        self._lb = tk.Listbox(fr, font=FM, bg=BG2, fg=T1,
                              selectbackground=ACCENT, relief="flat",
                              height=12, activestyle="none")
        sb = ttk.Scrollbar(fr, orient="vertical", command=self._lb.yview,
                           style=CPharm_TSCROLL)
        self._lb.configure(yscrollcommand=sb.set)
        self._lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        bottom = tk.Frame(self, bg=BG)
        bottom.pack(fill="x", padx=16, pady=10)
        done = tk.Button(bottom, text="Done",
                         font=("Segoe UI", 11, "bold"),
                         bg=GREEN, fg=ON_ACCENT, relief="flat",
                         cursor="hand2", command=self.destroy,
                         padx=16, pady=8, bd=0, highlightthickness=0)
        done.configure(activebackground="#22c55e", activeforeground=ON_ACCENT)
        done.pack(side="right")

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


def adb_executable() -> str:
    """Prefer SDK platform-tools adb so TCP connects work even when PATH is wrong."""
    sdk = state.get("sdk_path") or find_sdk()
    if sdk:
        p = Path(sdk) / "platform-tools" / ("adb.exe" if IS_WIN else "adb")
        if p.exists():
            return str(p)
    guess = Path(SDK_DEFAULT_PATH) / "platform-tools" / ("adb.exe" if IS_WIN else "adb")
    if guess.exists():
        return str(guess)
    import shutil
    w = shutil.which("adb")
    if w:
        return w
    return "adb"


def _ensure_minimal_platform_tools(log_fn=None) -> bool:
    """
    MuMu-only users often skip the full SDK; still need adb for TCP.
    Drop Google's platform-tools into %LOCALAPPDATA%\\Android\\Sdk if missing.
    """
    sdk_root = Path(SDK_DEFAULT_PATH)
    pt = sdk_root / "platform-tools" / ("adb.exe" if IS_WIN else "adb")
    if pt.exists():
        if not state.get("sdk_path"):
            state["sdk_path"] = str(sdk_root)
        return True
    try:
        sdk_root.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    if log_fn:
        log_fn("  Downloading Android platform-tools (adb only, ~6 MB)…\n")
    ok = _direct_download_platform_tools(str(sdk_root), log_fn=log_fn)
    if ok and pt.exists():
        state["sdk_path"] = str(sdk_root)
        return True
    return False


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
    exe = adb_executable()
    cmd = [exe]
    if serial:
        cmd += ["-s", serial]
    cmd += list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, creationflags=_NO_WIN)
        return (r.stdout + r.stderr).strip()
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


def _adb_devices_tcp_ready_serials(devices_l_output: str):
    """Parse ``adb devices -l`` for TCP serials (``host:port``) already in ``device`` state."""
    lines = []
    serials = []
    for line in (devices_l_output or "").splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
            continue
        lines.append(line)
        parts = line.split()
        if len(parts) < 2:
            continue
        ser, st = parts[0], parts[1]
        if ":" in ser and st == "device":
            serials.append(ser)
    return len(lines), serials


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


_arm64_adb_warned = False


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


def _warn_arm64_x64_adb(log_fn) -> None:
    """If the host is Windows ARM64 but ``adb.exe`` is x64, log one Prism-emulation note."""
    global _arm64_adb_warned
    if not log_fn or not IS_WIN or _arm64_adb_warned:
        return
    if "arm64" not in _machine_arch():
        return
    exe = adb_executable()
    if not exe or not Path(exe).exists():
        return
    if _pe_machine_type(exe) != 0x8664:
        return
    _arm64_adb_warned = True
    log_fn(
        "  Note: adb.exe is x64 — on ARM64 Windows it runs under Prism emulation "
        "(may be slower than a native ARM64 build).\n"
    )


MUMU_DOWNLOAD_URL = "https://www.mumuplayer.com/windows-arm.html"

# MuMu only forwards ADB from the host after it is enabled *per instance* (settings / device info).
MUMU_ADB_PER_INSTANCE_HINT = (
    "In each running instance: open that phone’s menu (≡) or Settings → enable ADB / "
    "USB or network debugging (wording varies; sometimes under “Other” or “Device information”). "
    "Without this, the PC never sees a host port to scan. On Windows use "
    "adb connect 127.0.0.1:<port> if MuMu shows a port."
)


def _mumu_install_root(mgr_path) -> Path:
    """Directory that contains MuMuPlayer.exe (parent of shell/nx_main)."""
    p = Path(mgr_path)
    if p.parent.name.lower() in ("shell", "nx_main"):
        return p.parent.parent
    return p.parent


def _glob_mumu_manager(max_dirs=160):
    """Search for nemux-shell-winui.Manager.exe or MuMuManager.exe under MuMu install trees."""
    roots = []
    for base_var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA", "PROGRAMW6432"):
        base = os.environ.get(base_var, "")
        if not base:
            continue
        base_path = Path(base)
        for child in ("Netease", "MuMuPlayer", "MuMuPlayerARM"):
            d = base_path / child
            if d.is_dir():
                roots.append(d)
        try:
            for d in base_path.glob("MuMu*"):
                if d.is_dir():
                    roots.append(d)
        except Exception:
            pass
    seen_paths = set()
    seen_roots = set()
    for root in roots:
        if root in seen_roots:
            continue
        seen_roots.add(root)
        for exe_name in MUMU_MANAGER_EXES:
            try:
                for i, p in enumerate(root.rglob(exe_name)):
                    if i >= max_dirs:
                        break
                    if p.is_file():
                        rp = str(p.resolve())
                        if rp not in seen_paths:
                            seen_paths.add(rp)
                            yield p
            except Exception:
                pass


def _find_mumu_manager():
    """Return path to MuMu shell CLI (nemux-shell-winui.Manager.exe preferred), or None.

    Search order:
      1. User-browsed path stored in state["mumu_mgr_path"]
      2. 'where' for nemux-shell-winui.Manager.exe / MuMuManager.exe (PATH)
      3. Windows registry Uninstall entries for any "MuMu" product
      4. Standard filesystem candidates under PROGRAMFILES / LOCALAPPDATA
      5. Shallow glob under Netease / MuMu* folders
    """
    if not IS_WIN:
        return None

    # 1. User manually selected path
    cached = state.get("mumu_mgr_path", "")
    if cached:
        p = Path(cached)
        if p.exists() and _is_mumu_manager_cli_path(p):
            return p

    candidates = []

    # 2. 'where' command — works if MuMu install dir is on PATH
    for query in (
        MUMU_MANAGER_PRIMARY,
        MUMU_MANAGER_LEGACY,
        "nemux-shell-winui.Manager",
        "MuMuManager",
    ):
        try:
            r = subprocess.run(
                ["where", query],
                capture_output=True, text=True, timeout=8,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if r.returncode == 0:
                for line in (r.stdout + r.stderr).strip().splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    p = Path(line)
                    if p.exists() and _is_mumu_manager_cli_path(p):
                        candidates.insert(0, p)
        except Exception:
            pass

    # 3. Registry Uninstall entries
    try:
        import winreg
        reg_roots = [
            (winreg.HKEY_LOCAL_MACHINE,
             r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE,
             r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER,
             r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        for hive, reg_path in reg_roots:
            try:
                key = winreg.OpenKey(hive, reg_path)
                i = 0
                while True:
                    try:
                        sub_name = winreg.EnumKey(key, i)
                        i += 1
                        sub = winreg.OpenKey(key, sub_name)
                        try:
                            name_val = winreg.QueryValueEx(sub, "DisplayName")[0]
                            if "mumu" in name_val.lower():
                                for val_name in ("InstallLocation", "InstallDir"):
                                    try:
                                        loc = Path(winreg.QueryValueEx(sub, val_name)[0])
                                        for sub_dir in ("shell", "nx_main", ""):
                                            for exe_name in MUMU_MANAGER_EXES:
                                                exe = (
                                                    loc / sub_dir / exe_name
                                                    if sub_dir
                                                    else loc / exe_name
                                                )
                                                candidates.append(exe)
                                    except OSError:
                                        pass
                        except OSError:
                            pass
                        winreg.CloseKey(sub)
                    except OSError:
                        break
                winreg.CloseKey(key)
            except OSError:
                pass
    except ImportError:
        pass

    # 4. Filesystem candidates (prefer nemux CLI, then legacy)
    for base_var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA", "PROGRAMW6432"):
        base = os.environ.get(base_var, "")
        if not base:
            continue
        for folder in (
            "Netease\\MuMuPlayerARM",
            "Netease\\MuMuPlayer-12.0",
            "Netease\\MuMuPlayerGlobal-12.0",
            "Netease\\MuMuPlayer",
            "MuMuPlayerARM",
            "MuMuPlayer-12.0",
            "MuMuPlayer",
        ):
            root = Path(base) / folder
            for sub_dir in ("shell", "nx_main", ""):
                for exe_name in MUMU_MANAGER_EXES:
                    candidates.append(
                        root / sub_dir / exe_name if sub_dir
                        else root / exe_name
                    )

    for p in candidates:
        if p.exists() and _is_mumu_manager_cli_path(p):
            return p

    # 5. Discovery search (new install paths / renamed folders)
    try:
        for p in _glob_mumu_manager():
            if _is_mumu_manager_cli_path(p):
                return p
    except Exception:
        pass
    return None


def _find_mumu_player():
    """Return the MuMuPlayer install root (contains MuMuPlayer.exe), or None."""
    mgr = _find_mumu_manager()
    if mgr:
        return _mumu_install_root(mgr)
    return None


def _find_mumu_player_exe() -> Path | None:
    """Resolve path to the MuMu **GUI** launcher (not the shell CLI)."""
    root = _find_mumu_player()
    candidates = (
        Path("MuMuPlayer.exe"),
        Path("MuMuNxMain.exe"),
        Path("nx_main") / "MuMuPlayer.exe",
        Path("nx_main") / "MuMuNxMain.exe",
        Path("shell") / "MuMuPlayer.exe",
        Path("shell") / "MuMuNxMain.exe",
    )
    if root:
        for rel in candidates:
            p = root / rel
            if p.is_file():
                return p
        try:
            for pat in ("MuMuPlayer.exe", "MuMuNxMain.exe"):
                for i, p in enumerate(root.rglob(pat)):
                    if i > 50:
                        break
                    if p.is_file():
                        return p
        except Exception:
            pass
    # Manager not found — still try common GUI exe locations (fresh installs).
    for base_var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(base_var, "")
        if not base:
            continue
        base_path = Path(base)
        for folder in (
            "Netease\\MuMuPlayerARM",
            "Netease\\MuMuPlayer-12.0",
            "Netease\\MuMuPlayerGlobal-12.0",
            "Netease\\MuMuPlayer",
            "MuMuPlayerARM",
            "MuMuPlayer",
        ):
            rp = base_path / folder
            if not rp.is_dir():
                continue
            for sub in ("", "nx_main", "shell"):
                base = rp / sub if sub else rp
                for name in ("MuMuPlayer.exe", "MuMuNxMain.exe"):
                    p = base / name
                    if p.is_file():
                        return p
    return None


def _launch_gui_exe(exe: Path) -> bool:
    """Start a Windows GUI app — never use CREATE_NO_WINDOW (it suppresses MuMuPlayer)."""
    if not exe.is_file():
        return False
    if IS_WIN:
        try:
            os.startfile(str(exe))
            return True
        except OSError:
            pass
        try:
            subprocess.Popen(
                [str(exe)],
                cwd=str(exe.parent),
                shell=False,
            )
            return True
        except Exception:
            return False
    try:
        subprocess.Popen([str(exe)], cwd=str(exe.parent))
        return True
    except Exception:
        return False


def _mumu_parse_json_output(out: str):
    """MuMu shell CLI sometimes prints banners; extract the JSON object or array."""
    s = (out or "").strip()
    if not s:
        return None
    if s.startswith("{") or s.startswith("["):
        try:
            return json.loads(s)
        except Exception:
            pass
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        i = s.find(open_ch)
        j = s.rfind(close_ch)
        if i >= 0 and j > i:
            try:
                return json.loads(s[i : j + 1])
            except Exception:
                continue
    # Nemux / newer builds often mix log lines ("info: g.gX…", "App … Redirect") with JSON.
    for blob in _mumu_iter_json_blobs(s):
        try:
            return json.loads(blob)
        except Exception:
            continue
    return None


def _mumu_iter_json_blobs(text: str):
    """Yield balanced `{…}` / `[…]` substrings — finds JSON embedded in noisy CLI logs."""
    if not text:
        return
    i = 0
    n = len(text)
    while i < n:
        if text[i] not in "{[":
            i += 1
            continue
        opener, closer = text[i], "}" if text[i] == "{" else "]"
        depth = 0
        start = i
        for j in range(i, n):
            c = text[j]
            if c == opener:
                depth += 1
            elif c == closer:
                depth -= 1
                if depth == 0:
                    yield text[start : j + 1]
                    i = j + 1
                    break
        else:
            i += 1


def _mumu_shell_manager_fallback(nemux_path: Path) -> Path | None:
    """MuMuPlayerARM often ships ``shell\\MuMuManager.exe`` — JSON ``info`` works there when nemux only logs GUI spam."""
    root = nemux_path.resolve().parent.parent
    for rel in (
        Path("shell") / "MuMuManager.exe",
        Path("shell") / MUMU_MANAGER_LEGACY,
        Path("nx_main") / "MuMuManager.exe",
    ):
        p = root / rel
        if p.is_file():
            return p
    return None


def _mumu_adb_port(d: dict):
    for k in ("adb_port", "adb_host_port", "port", "AdbPort", "adbPort"):
        v = d.get(k)
        if v is None:
            continue
        try:
            return int(v)
        except (TypeError, ValueError):
            continue
    return None


def _mumu_run_capture(mgr_path, *args, timeout=15):
    """Run MuMu CLI; return (ok, stdout, stderr). Nemux often puts JSON only on one stream."""
    mgr_path = Path(mgr_path)
    cwd = str(mgr_path.parent)
    flags = subprocess.CREATE_NO_WINDOW if IS_WIN else 0
    try:
        r = subprocess.run(
            [str(mgr_path)] + list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=flags,
            cwd=cwd,
            encoding="utf-8",
            errors="replace",
        )
        return r.returncode == 0, (r.stdout or ""), (r.stderr or "")
    except Exception as e:
        return False, "", str(e)


def _mumu_run(mgr_path, *args, timeout=15):
    """Run MuMu shell CLI — returns (ok, combined_stdout_stderr)."""
    ok, out, err = _mumu_run_capture(mgr_path, *args, timeout=timeout)
    return ok, (out + "\n" + err).strip()


def _instances_append_from_info_json(data, instances: list) -> None:
    """Parse MuMu ``info`` JSON into instance dicts (mutates ``instances``)."""

    def _parse_one(d):
        if not isinstance(d, dict):
            return
        ip = d.get("adb_host_ip") or d.get("ip") or "127.0.0.1"
        port = _mumu_adb_port(d)
        if port is None:
            return
        idx = d.get("index", 0)
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            idx = 0
        instances.append({
            "index":      idx,
            "name":       d.get("name") or d.get("vm_name") or f"MuMu-{idx}",
            "adb_serial": f"{ip}:{port}",
            "started":    bool(d.get("is_android_started") or d.get("started")),
        })

    if isinstance(data, dict):
        if any(k in data for k in ("index", "adb_port")) or _mumu_adb_port(data) is not None:
            _parse_one(data)
        else:
            for v in data.values():
                if isinstance(v, dict):
                    _parse_one(v)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                _parse_one(item)


def _mumu_get_instances(mgr_path, log_fn=None):
    """Return list of dicts: {index, name, adb_serial, started}.

    Tries ``nemux-shell-winui.Manager.exe`` and ``shell\\MuMuManager.exe`` (JSON ``info``
    often works only on the legacy shell binary). Falls back to ``info -v <n>`` per VM.
    """
    mgr_path = Path(mgr_path)
    fb = _mumu_shell_manager_fallback(mgr_path)
    exes: list[Path] = [mgr_path]
    if fb and fb.resolve() != mgr_path.resolve():
        exes.append(fb)

    if log_fn:
        install_root = mgr_path.resolve().parent.parent
        if fb is None:
            log_fn(
                "  Fallback not found: no shell\\MuMuManager.exe next to install "
                f"under {install_root}\n"
            )
        elif fb.resolve() != mgr_path.resolve():
            log_fn(f"  Shell fallback will run after primary if needed: {fb}\n")

    instances: list = []

    for ei, exe in enumerate(exes):
        if ei > 0 and log_fn:
            log_fn(f"  Trying fallback: {exe}\n")
        tag = exe.name
        # 1) info -v all — try stdout, stderr, merge (nemux may spam GUI logs without JSON).
        ok, so, se = _mumu_run_capture(exe, "info", "-v", "all", timeout=18)
        if log_fn:
            comb = (so + "\n" + se).strip()
            log_fn(
                f"  Manager CLI ({tag}) rc={ok} out={repr(comb[:320]) if comb else '(empty)'}\n"
            )
        for blob in (so + "\n" + se, so, se):
            data = _mumu_parse_json_output(blob)
            if data is not None:
                _instances_append_from_info_json(data, instances)
        if instances:
            if log_fn and exe != mgr_path:
                log_fn(f"  Using JSON from fallback: {exe}\n")
            break

        # 2) Per-VM info -v 0 .. 15 (works when ``all`` returns log spam only).
        if log_fn:
            log_fn(f"  Trying per-VM info -v <n> via {tag}…\n")
        for idx in range(16):
            ok, so, se = _mumu_run_capture(exe, "info", "-v", str(idx), timeout=10)
            blob = (so + "\n" + se).strip()
            if not blob:
                continue
            data = _mumu_parse_json_output(blob)
            if data is None:
                continue
            before = len(instances)
            _instances_append_from_info_json(data, instances)
            if len(instances) > before:
                continue
        if instances:
            if log_fn and exe != mgr_path:
                log_fn(f"  Instance list via fallback: {exe}\n")
            break

    if not instances and log_fn:
        log_fn(
            "  Could not parse JSON from manager output — "
            "will rely on adb port scan.\n"
        )

    # Dedupe by adb_serial
    seen = set()
    uniq = []
    for inst in sorted(instances, key=lambda x: x["index"]):
        s = inst["adb_serial"]
        if s not in seen:
            seen.add(s)
            uniq.append(inst)
    return uniq


def _mumu_launch(mgr_path, index, log_fn=None):
    """Launch a MuMuPlayer instance by index."""
    if log_fn:
        log_fn(f"  Launching MuMu instance {index}…\n")
    ok, out = _mumu_run(mgr_path, "control", "-v", str(index), "launch", timeout=30)
    return ok


def _connect_mumu_phones(count=16, log_fn=None):
    """Connect to MuMuPlayer instances via MuMu shell CLI + adb connect.

    Always tries both the manager CLI (for names/exact ports) AND the port
    fallback scan — so phones are found even if the MuMu CLI lookup fails.
    Returns list of connected ADB serials.
    """
    exe = adb_executable()
    if not Path(exe).exists() and exe == "adb":
        if log_fn:
            log_fn("  adb not found — downloading minimal platform-tools…\n")
        _ensure_minimal_platform_tools(log_fn=log_fn)

    # Fresh adb server avoids stale TCP state after emulator updates.
    adb("kill-server", timeout=5)
    adb("start-server", timeout=12)

    devices_raw = adb("devices", "-l", timeout=12)
    n_dev_lines, tcp_ready = _adb_devices_tcp_ready_serials(devices_raw)
    if log_fn:
        log_fn(
            f"  adb devices -l: {n_dev_lines} device line(s), "
            f"{len(tcp_ready)} TCP host:port in device state\n"
        )

    _warn_arm64_x64_adb(log_fn)

    mgr = _find_mumu_manager()
    connected = []
    tried_serials = set()

    for serial in tcp_ready:
        if serial in tried_serials:
            continue
        tried_serials.add(serial)
        check = adb("shell", "echo", "ok", serial=serial, timeout=6)
        if check.strip() == "ok":
            connected.append(serial)
            if log_fn:
                log_fn(f"  ✅ using listed TCP device: {serial}\n")

    if mgr:
        if log_fn:
            log_fn(f"  MuMu CLI found ({Path(mgr).name}): {mgr}\n")
        instances = _mumu_get_instances(mgr, log_fn=log_fn)
        if log_fn:
            log_fn(f"  Instances from MuMu CLI: {len(instances)}\n")
        for inst in instances:
            serial = inst["adb_serial"]
            tried_serials.add(serial)
            out = adb("connect", serial, timeout=8)
            if log_fn:
                log_fn(f"  adb connect {serial}: {out or '(no output)'}\n")
            if "connected" in out.lower() or "already" in out.lower():
                check = adb("shell", "echo", "ok", serial=serial, timeout=6)
                if check.strip() == "ok":
                    connected.append(serial)
                    if log_fn:
                        log_fn(f"  ✅ {inst['name']}: {serial}\n")
    else:
        if log_fn:
            log_fn("  MuMu shell CLI not found — using port scan fallback\n")

    # Port scan: MuMu 12 uses 16384 + n*32; ARM / variant builds also use 7555 bands,
    # 5555-class ports, and high ports such as 22471 (cap iterations per base ~48).
    n_cap = min(48, max(count, 16))

    def _scan_ports(base_port, stride, n_iter):
        for i in range(n_iter):
            serial = f"127.0.0.1:{base_port + i * stride}"
            if serial in tried_serials:
                continue
            out = adb("connect", serial, timeout=4)
            low = out.lower()
            if "connected" in low or "already" in low:
                check = adb("shell", "echo", "ok", serial=serial, timeout=5)
                if check.strip() == "ok":
                    connected.append(serial)
                    tried_serials.add(serial)
                    if log_fn:
                        log_fn(f"  ✅ port scan: {serial}\n")

    for base_port, stride, n_iter in (
        (16384, 32, n_cap),
        (7555, 2, n_cap),
        (5555, 2, min(32, max(count, 8))),
        (22471, 1, min(32, max(count, 8))),
    ):
        _scan_ports(base_port, stride, n_iter)

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
                    "     4. Keep MuMuPlayer running, then open Connect & boot in this wizard.\n"
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
                    "     directly to Connect & boot — the wizard attaches over ADB.\n"
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
            "Go back to Install tools and make sure Java installed successfully,\n"
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
            "Go back to Android runtime and reinstall the SDK."
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
    """AVD: emulator-5554->0. MuMu TCP: 127.0.0.1:16384->0, :16416->1, ..."""
    try:
        if serial.startswith("emulator-"):
            return max(0, (int(serial.split("-")[1]) - 5554) // 2)
        if ":" in serial:
            port = int(serial.rsplit(":", 1)[1])
            if 16384 <= port <= 16896:
                return (port - 16384) // 32
        return 0
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


def _apk_pkg_installed(serial: str, pkg: str) -> bool:
    out = adb("shell", "pm", "path", pkg, serial=serial, timeout=20)
    return "package:" in (out or "")


def _chrome_pkg_installed(serial: str) -> bool:
    return _apk_pkg_installed(serial, "com.android.chrome")


# F-Droid browser packages (no APK shipped here). Optional files under ``wizard/bundled/``:
# ``Fennec.apk``, ``Mull.apk``, or ``fennec_fdroid.apk`` — user obtains builds from F-Droid / upstream.
FDROID_FENNEC_PKG = "org.mozilla.fennec_fdroid"
FDROID_MULL_PKG = "us.spotco.fennec_dos"


def _try_pm_install_existing_pkg(serial: str, pkg: str, log_fn=None) -> bool:
    """Enable stub / recoverable system package via ``pm install-existing`` variants."""
    if _apk_pkg_installed(serial, pkg):
        return True
    adb("shell", "pm", "enable", pkg, serial=serial, timeout=15)
    if _apk_pkg_installed(serial, pkg):
        return True
    for args in (
        ("shell", "cmd", "package", "install-existing", pkg),
        ("shell", "cmd", "package", "install-existing", "--user", "0", pkg),
        ("shell", "pm", "install-existing", pkg),
        ("shell", "pm", "install-existing", "--user", "0", pkg),
    ):
        out = adb(*args, serial=serial, timeout=120)
        if log_fn:
            log_fn(f"    {' '.join(args[1:])} → {(out or '')[:160]}\n")
        if _apk_pkg_installed(serial, pkg):
            return True
    return False


def _try_bundled_fdroid_apk(serial: str, log_fn=None) -> str | None:
    """Install optional bundled Fennec/Mull APKs; return package id if one becomes installed."""
    here = Path(__file__).resolve().parent / "bundled"
    candidates = (
        ("Fennec.apk", FDROID_FENNEC_PKG),
        ("fennec_fdroid.apk", FDROID_FENNEC_PKG),
        ("Mull.apk", FDROID_MULL_PKG),
    )
    for fname, expect_pkg in candidates:
        p = here / fname
        if not p.is_file():
            continue
        if log_fn:
            log_fn(f"    adb install (bundled {fname})…\n")
        out = adb("install", "-r", str(p), serial=serial, timeout=300)
        if log_fn and out:
            log_fn(f"    → {(out or '')[:200]}\n")
        if _apk_pkg_installed(serial, expect_pkg):
            return expect_pkg
    return None


def ensure_fdroid_browser(serial: str, log_fn=None) -> str | None:
    """Ensure Fennec or Mull is installed; return the package id or None.

    Order: already installed → ``install-existing`` for Fennec then Mull → optional bundled APKs.
    """
    if _apk_pkg_installed(serial, FDROID_FENNEC_PKG):
        return FDROID_FENNEC_PKG
    if _apk_pkg_installed(serial, FDROID_MULL_PKG):
        return FDROID_MULL_PKG
    if _try_pm_install_existing_pkg(serial, FDROID_FENNEC_PKG, log_fn):
        return FDROID_FENNEC_PKG
    if _try_pm_install_existing_pkg(serial, FDROID_MULL_PKG, log_fn):
        return FDROID_MULL_PKG
    got = _try_bundled_fdroid_apk(serial, log_fn)
    return got


#
# Chrome APK workflow (user supplies APK — do not redistribute Google Chrome APK in open source):
# - Sign in to Play on **one** phone with your Google account, install Chrome there, then either
#   sideload the same build to other devices via **Install APK on all phones**, or place
#   ``ChromePublic.apk`` / ``chrome.apk`` under ``wizard/bundled/`` (wizard installs per device).
# - Alternatively obtain a matching ARM APK from a trusted source once, then push to every serial
#   with **Install APK on all phones** (``adb install -r`` per device).
#


def _install_apk_all_phones(apk_path: str | Path, serials, log_fn=None) -> dict:
    """Run ``adb install -r`` on each serial. ``serials`` is an iterable of ADB serial strings."""
    path = Path(apk_path)
    results = []
    if not path.is_file():
        if log_fn:
            log_fn(f"    APK not found or not a file: {path}\n")
        return {"ok": False, "results": results}
    resolved = str(path.resolve())
    name = path.name
    for serial in serials:
        if log_fn:
            log_fn(f"    [{serial}] adb install -r {name} …\n")
        out = adb("install", "-r", resolved, serial=serial, timeout=600)
        if log_fn and out:
            log_fn(f"    → {(out or '')[:400]}\n")
        o = out or ""
        ok = ("Success" in o) and ("Failure" not in o)
        results.append({"serial": serial, "ok": ok})
    return {"ok": all(r["ok"] for r in results) if results else False, "results": results}


def _bundled_chrome_apk_path():
    """Optional APK in ``wizard/bundled/`` (not shipped by default)."""
    here = Path(__file__).resolve().parent / "bundled"
    for name in ("ChromePublic.apk", "chrome.apk"):
        p = here / name
        if p.is_file():
            return p
    return None


def _try_install_bundled_chrome_apk(serial: str, log_fn) -> bool:
    apk = _bundled_chrome_apk_path()
    if not apk:
        return False
    if log_fn:
        log_fn(f"    adb install (bundled {apk.name})…\n")
    out = adb("install", "-r", str(apk), serial=serial, timeout=300)
    if log_fn and out:
        log_fn(f"    → {(out or '')[:200]}\n")
    return _chrome_pkg_installed(serial)


def ensure_android_chrome(
    serial: str,
    log_fn=None,
    *,
    open_play_if_missing: bool = False,
) -> bool:
    """Ensure ``com.android.chrome`` exists (many MuMu / AVD images ship without Chrome).

    Default path: enable stub if present, optional bundled APK under ``wizard/bundled/``,
    then ``pm install-existing``. Opening the Play Store is opt-in only
    (``open_play_if_missing=True``), e.g. user explicitly chose that flow.
    """
    if _chrome_pkg_installed(serial):
        return True
    adb("shell", "pm", "enable", "com.android.chrome", serial=serial, timeout=15)
    if _chrome_pkg_installed(serial):
        return True
    if _try_install_bundled_chrome_apk(serial, log_fn):
        return True
    for args in (
        ("shell", "cmd", "package", "install-existing", "com.android.chrome"),
        ("shell", "cmd", "package", "install-existing", "--user", "0", "com.android.chrome"),
        ("shell", "pm", "install-existing", "com.android.chrome"),
        ("shell", "pm", "install-existing", "--user", "0", "com.android.chrome"),
    ):
        out = adb(*args, serial=serial, timeout=120)
        if log_fn:
            log_fn(f"    {' '.join(args[1:])} → {(out or '')[:160]}\n")
        if _chrome_pkg_installed(serial):
            return True
    if open_play_if_missing:
        if log_fn:
            log_fn(
                "    Opening Play Store listing for Chrome (optional — requires a Google account on "
                "this device). Install Chrome if needed, then run **Configure Chrome** again.\n"
            )
        adb(
            "shell",
            "am",
            "start",
            "-a",
            "android.intent.action.VIEW",
            "-d",
            "market://details?id=com.android.chrome",
            serial=serial,
            timeout=20,
        )
    elif log_fn:
        log_fn(
            "    Chrome still missing — sideload an APK with ``adb install -r chrome.apk`` (no Google "
            "account needed), or place ``ChromePublic.apk`` / ``chrome.apk`` under wizard/bundled/ "
            "and try again.\n"
        )
    return False


def setup_chrome(serial, log_fn=None, *, offer_play_store: bool = False):
    """
    Kill Chrome first-run experience so URLs open immediately every time.

    Fresh AVDs show ToS / sync screens that swallow the target URL.
    Writing the chrome-command-line flags file disables all of that.
    Works without root — shell user can write to /data/local/tmp.

    If Chrome is not installed, tries bundled APK (if present), ``pm install-existing``, and
    optionally opens Play Store only when ``offer_play_store`` is True (explicit user choice).
    """
    if not ensure_android_chrome(
        serial,
        log_fn=log_fn,
        open_play_if_missing=offer_play_store,
    ):
        if offer_play_store:
            raise RuntimeError(
                "Google Chrome is not on this device yet. "
                "If you used Play Store, finish installing Chrome (Google account required there), "
                "then tap Configure Chrome again — or sideload an APK without Google."
            )
        return False
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
    t = step.get("type", "")
    if t == "open_url":
        return f"Open URL  →  {step.get('url', '')}"
    if t == "tap":
        return f"Tap  →  ({step.get('x', 0)}, {step.get('y', 0)})"
    if t == "wait":
        return f"Wait  →  {step.get('seconds', 1)}s"
    if t == "swipe":
        return (f"Swipe  →  ({step.get('x1', 0)},{step.get('y1', 0)})"
                f"  →  ({step.get('x2', 0)},{step.get('y2', 0)})")
    if t == "keyevent":
        return f"Key  →  {step.get('key', 'BACK')}"
    if t == "close_app":
        return f"Close app  →  {step.get('package', 'Chrome')}"
    if t == "clear_cookies":
        return "Clear cookies"
    if t == "rotate_identity":
        return "Rotate IP + Android ID"
    if t == "type_text":
        return f"Type  →  \"{step.get('text', '')}\""
    return f"Step  →  {t}" if t else "Step"


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
            font=("Segoe UI", 10, "bold"),
            relief="flat", cursor="hand2", pady=pady, padx=14, bd=0,
            highlightthickness=0,
        )
        if width:
            kw["width"] = width
        if color in (ACCENT, GREEN, RED, PURPLE):
            kw["bg"], kw["fg"] = color, ON_ACCENT
        elif color in (BG3, BG4):
            kw["bg"], kw["fg"] = color, T1
        else:
            kw["bg"], kw["fg"] = color, T1
        b = tk.Button(parent, **kw)
        if color == ACCENT:
            style_primary_button(b)
        elif color in (BG3, BG4):
            style_secondary_button(b)
        elif color == RED:
            style_danger_button(b)
        elif color == GREEN:
            b.configure(activebackground="#22c55e", activeforeground=ON_ACCENT)
        elif color == PURPLE:
            b.configure(activebackground="#8b5cf6", activeforeground=ON_ACCENT)
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
        top = self.winfo_toplevel()
        self._welcome_images: list[tk.PhotoImage] = []

        tk.Label(self, text="CPharm Phone Farm", font=FONT_HERO,
                 bg=BG, fg=T1).pack(pady=(18, 4))
        tk.Label(
            self,
            text="Virtual Android phones on your Windows laptop — Snapdragon ARM64 or Intel.\n"
                 "This wizard installs and wires everything; primary actions use the accent controls below.",
            font=FONT_LEAD, bg=BG, fg=T2, justify="center",
        ).pack(pady=(0, 8))

        steps = tk.Frame(self, bg=BG2, padx=20, pady=16,
                         highlightthickness=1, highlightbackground=BORDER)
        steps.pack(fill="x", pady=(0, 14))
        tk.Label(steps, text="Flow overview",
                 font=FONT_H2, bg=BG2, fg=T1, anchor="w").pack(fill="x")
        tk.Label(steps,
                 text="Setup → devices → automation & Play checklist → launch dashboard.\n\n"
                      "Use Next on each screen. Tor, groups, and scheduling appear on later steps.\n"
                      "On Snapdragon / ARM64 Windows, MuMu replaces Google emulators (no ARM64 AVD).",
                 font=FS, bg=BG2, fg=T2, justify="left", anchor="w").pack(fill="x", pady=(8, 0))

        # Architecture diagram — canvas only; static content is packed below, NOT inside the callback
        c = tk.Canvas(self, bg=BG3, height=152,
                      highlightthickness=1, highlightbackground=BORDER_STRONG)
        c.pack(fill="x", padx=16, pady=(0, 14))
        c.bind("<Configure>", lambda e: self._draw_diagram(c))

        # Static "What you get:" section — built once here, never inside _draw_diagram
        tk.Label(self, text="What you get",
                 font=FONT_H2, bg=BG, fg=ACCENT,
                 anchor="w").pack(fill="x", padx=4)
        tk.Label(self, text="Session flows, groups, scheduling, and Play Store closed-testing paths "
                                "stay available on later wizard pages.",
                 font=FS, bg=BG, fg=T3, anchor="w").pack(fill="x", padx=4, pady=(0, 4))
        f = tk.Frame(self, bg=BG2, padx=16, pady=14,
                     highlightthickness=1, highlightbackground=BORDER)
        f.pack(fill="x", pady=(4, 0))
        feat_icons = (
            "feat_phones",
            "feat_network",
            "feat_identity",
            "feat_play",
            "feat_parallel",
            "feat_arm",
        )
        items = [
            ("Multiple isolated Android phones — each its own world"),
            ("Each phone browses, taps, scrolls, installs apps via ADB"),
            ("New Android ID + different IP (Tor) on every session"),
            ("Google Play closed testing — phones act like real registered devices"),
            ("Parallel groups — several phones test your app while others browse your site"),
            ("Works on Snapdragon ARM — uses Windows Hypervisor, no Intel required"),
        ]
        for stem, text in zip(feat_icons, items):
            row = tk.Frame(f, bg=BG2)
            row.pack(fill="x", pady=4)
            img = load_icon(stem, top)
            if img:
                self._welcome_images.append(img)
                tk.Label(row, image=img, bg=BG2).pack(side="left", padx=(0, 10))
            else:
                tk.Label(row, text="·", font=FB, bg=BG2, fg=T3, width=2).pack(side="left")
            tk.Label(row, text=text, font=FB, bg=BG2, fg=T1,
                     anchor="w").pack(side="left")

    def _draw_diagram(self, c):
        c.delete("all")
        w = c.winfo_width() or 700
        boxes = [
            (ACCENT, "Android\nSDK tools"),
            (GREEN, "Virtual phones\n(AVD / MuMu)"),
            (YELLOW, "CPharm\ndashboard"),
            (PURPLE, "Wizard\ncontrol"),
        ]
        bw = 128
        gap = max(8, (w - len(boxes) * bw) // (len(boxes) + 1))
        y1, y2 = 22, 102
        rr = 12
        positions = []
        for i, (col, label) in enumerate(boxes):
            x1 = gap + i * (bw + gap)
            x2 = x1 + bw
            mx, my = (x1 + x2) // 2, (y1 + y2) // 2
            draw_round_rect(c, x1, y1, x2, y2, rr, fill=BG4, outline=col, width=2)
            c.create_text(mx, my, text=label, fill=T1,
                          font=("Segoe UI", 9, "bold"), justify="center")
            positions.append((x1, x2, y1, y2))

        for i in range(len(positions) - 1):
            _, rx, _, _ = positions[i]
            lx, _, _, _ = positions[i + 1]
            mid_y = (y1 + y2) // 2
            c.create_line(rx, mid_y, lx, mid_y, fill=T3, width=2, arrow="last")

        c.create_text(
            w // 2, 132,
            text="Runs on your Windows laptop — no extra hardware",
            fill=T2, font=("Segoe UI", 9),
        )


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
            "Install tools & CPharm files",
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

        self._prereq_icons: list[tk.PhotoImage] = []
        top = self.winfo_toplevel()
        items = [
            ("java",     "prereq_java", "Java JDK 21",             "Needed to run the Android SDK tools",        True),
            ("python",   "prereq_python", "Python 3.13",             "Needed to run CPharm automation scripts",    True),
            ("packages", "prereq_packages", "Python packages",         "websockets + psutil for the dashboard",      True),
            ("tor",      "prereq_tor", "Tor",                     "IP rotation between sessions  (optional)",   False),
            ("cpharm",   "prereq_cpharm", "CPharm automation files", "The bot scripts and dashboard",              True),
        ]
        for key, icon_stem, name, desc, required in items:
            row = tk.Frame(list_card, bg=BG2, pady=5)
            row.pack(fill="x")
            pic = load_icon(icon_stem, top)
            if pic:
                self._prereq_icons.append(pic)
                tk.Label(row, image=pic, bg=BG2).pack(side="left", padx=(0, 6))
            else:
                tk.Label(row, text="·", font=FB, bg=BG2, fg=T3, width=2).pack(side="left")
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
        dl_icon = load_icon("icon_download", top)
        if dl_icon:
            self._prereq_icons.append(dl_icon)
        ib_kw = dict(
            text="Install everything",
            font=("Segoe UI", 14, "bold"),
            bg=GREEN, fg=ON_ACCENT,
            relief="flat", cursor="hand2",
            padx=24, pady=14, bd=0, highlightthickness=0,
            command=self._install_all,
        )
        if dl_icon:
            ib_kw["image"] = dl_icon
            ib_kw["compound"] = "left"
        self._install_btn = tk.Button(self, **ib_kw)
        self._install_btn.configure(activebackground="#22c55e", activeforeground=ON_ACCENT)
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
                                relief="flat", wrap="word")
        _attach_readonly_log_text(self._log_box)
        sb = ttk.Scrollbar(log_fr, orient="vertical", command=self._log_box.yview,
                           style=CPharm_TSCROLL)
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
            self._log_box.insert("end", text if text.endswith("\n") else text + "\n")
            self._log_box.see("end")
        self.after(0, _do)

    def _set_row(self, key, text, color=T2):
        def _do():
            lbl = self._rows.get(key)
            if lbl:
                lbl.config(text=text, fg=color)

        self.after(0, _do)

    def _set_progress(self, pct, label=""):
        def _do():
            self._progress["value"] = pct
            self._progress_lbl.config(text=label)

        self.after(0, _do)

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
        root = _cpharm_install_root()
        if (root / "automation" / "dashboard.py").exists():
            state["cpharm_dir"] = str(root)
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
        self.after(0, self._show_java_manual_btn)

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

        if (tor_dir / "tor.exe").exists():
            self._set_row("tor", "✅  Already installed", GREEN)
            self._log_write(
                f"Tor already present at {tor_dir} — skipping re-download "
                f"(avoids overwriting files while the wizard uses this tree).\n"
            )
            return

        self._set_row("tor", "⬇  Finding latest…", ACCENT)
        self._log_write("Looking up latest Tor version…")
        tor_url = _fetch_latest_tor_url()
        self._log_write(f"Downloading: {tor_url}")
        self._set_row("tor", "⬇  Downloading…", ACCENT)

        tmp = Path(os.environ.get("TEMP", "C:\\Temp")) / "cpharm_tor_bundle.tar.gz"
        try:
            _urlretrieve(tor_url, tmp, timeout=120)
        except Exception as e:
            self._log_write(f"  Download failed: {e}")
            self._set_row("tor", "❌  Failed", RED)
            self._log_write("  Tor download failed — IP rotation will use system Tor if available.")
            return

        self._set_row("tor", "📦  Extracting…", YELLOW)
        self._log_write("Extracting Tor to a temp folder, then merging into automation/tor…")
        ext_root = Path(os.environ.get("TEMP", "C:\\Temp")) / "_cpharm_tor_extract"
        try:
            if ext_root.exists():
                shutil.rmtree(ext_root)
            ext_root.mkdir(parents=True)
            with tarfile.open(tmp, "r:gz") as tf:
                for member in tf.getmembers():
                    if member.name.startswith("tor/"):
                        member.name = member.name[len("tor/"):]
                        if not member.name:
                            continue
                        tf.extract(member, ext_root)
            inner = ext_root
            if inner.exists():
                _merge_dir_into(inner, tor_dir)
        except Exception as e:
            self._log_write(f"  Extract/merge failed: {e}")
            self._set_row("tor", "❌  Failed", RED)
            self._log_write("  Tor install failed — IP rotation will use system Tor if available.")
            return
        finally:
            tmp.unlink(missing_ok=True)
            shutil.rmtree(ext_root, ignore_errors=True)

        if (tor_dir / "tor.exe").exists():
            self._set_row("tor", "✅  Done", GREEN)
            self._log_write("Tor installed ✅")
        else:
            self._set_row("tor", "⚠  Skipped", T3)
            self._log_write("Tor not found after extract — IP rotation will use system Tor if available.")

    def _install_cpharm(self):
        install_dir = Path(self._install_dir.get()).resolve()
        live_tree = _install_targets_live_tree(install_dir)

        if self._check_cpharm():
            self._set_row("cpharm", "✅  Already here", GREEN)
            self._log_write(f"CPharm files already present at {state.get('cpharm_dir', install_dir)}")
            return

        if live_tree:
            self._log_write(
                "Install folder is (or contains) the running CPharm checkout — "
                "will merge updates without deleting the live tree.\n"
            )
            self._log_write(
                "If this path is a git repo, updating with git pull first "
                "(ZIP files never overwrite .git — avoids Access denied on locked objects).\n"
            )
            if _try_git_pull_cpharm(install_dir, self._log_write):
                root = _find_cpharm_dashboard_root(install_dir)
                if root:
                    state["cpharm_dir"] = str(root)
                    self._set_row("cpharm", "✅  Done", GREEN)
                    self._log_write(f"CPharm ready at {root} ✅\n")
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
            self._log_write(f"Extracting ZIP to temp, then merging into {install_dir}…")
            extract_tmp = Path(os.environ.get("TEMP", "C:\\Temp")) / "_cpharm_zip_extract"
            if extract_tmp.exists():
                shutil.rmtree(extract_tmp)
            extract_tmp.mkdir(parents=True)
            try:
                with zipfile.ZipFile(tmp, "r") as zf:
                    zf.extractall(extract_tmp)
                tmp.unlink(missing_ok=True)
                inner = next(extract_tmp.iterdir(), None)
                src_root = inner if inner and inner.is_dir() else extract_tmp
                if live_tree:
                    install_dir.mkdir(parents=True, exist_ok=True)
                    _merge_zip_tree_safe(src_root, install_dir)
                    self._log_write(
                        "Merged ZIP contents into the folder (safe merge — skipped .git / VCS dirs).\n"
                    )
                else:
                    if install_dir.exists():
                        shutil.rmtree(install_dir)
                    install_dir.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src_root), str(install_dir))
                shutil.rmtree(extract_tmp, ignore_errors=True)
            except Exception as e:
                self._log_write(f"  Extraction failed: {e}")
                zip_ok = False
                shutil.rmtree(extract_tmp, ignore_errors=True)

        root = _find_cpharm_dashboard_root(install_dir)
        if root:
            state["cpharm_dir"] = str(root)
            self._set_row("cpharm", "✅  Done", GREEN)
            self._log_write(f"CPharm installed at {root} ✅")
            return

        self._log_write("  ZIP method failed — trying git clone fallback…")
        self._set_row("cpharm", "⬇  git clone…", YELLOW)

        if live_tree and install_dir == _wizard_runtime_root().resolve():
            self._set_row("cpharm", "❌  Failed", RED)
            self._log_write(
                "Git clone skipped: install path is this running repo, but automation/dashboard.py "
                "is still missing.\n"
                "Clone or unpack CPharm into this folder manually, or choose a different install directory.\n"
            )
            self._log_write(f"Manual: git clone {REPO_URL} \"{install_dir}\"")
            return

        ok, out = run_cmd(
            ["git", "clone", REPO_URL, str(install_dir)],
            timeout=120,
        )
        root = _find_cpharm_dashboard_root(install_dir)
        if root:
            state["cpharm_dir"] = str(root)
            self._set_row("cpharm", "✅  Done", GREEN)
            self._log_write("CPharm installed via git clone ✅")
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
            self.after(0, lambda: self._install_btn.config(
                state="normal",
                text="✅  Ready — click Next →",
                bg=GREEN,
            ))
        except Exception as exc:
            self._log_write(f"\n❌  Error: {exc}")
            self.after(0, lambda: self._install_btn.config(state="normal", text="Try again"))
        finally:
            self._working = False

    # ── page hooks ────────────────────────────────────────────────────────────

    def on_enter(self):
        threading.Thread(target=self._scan_thread, daemon=True).start()

    def _scan_thread(self):
        all_ok = self._check_all()
        if all_ok:
            self._set_progress(100, "All tools already installed!")
            self.after(0, lambda: self._install_btn.config(
                text="✅  Everything Ready — click Next →",
                bg=GREEN,
            ))
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
            "Android SDK & runtime choice",
            "One button does everything. The wizard downloads command-line tools, platform-tools, "
            "and the emulator — licenses accepted automatically.\n"
            "No Android Studio UI. No terminal. ARM64 hosts can use MuMuPlayer instead (card below)."
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
            text="Install Android SDK",
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
                                relief="flat", wrap="word")
        _attach_readonly_log_text(self._log_box)
        sb = ttk.Scrollbar(log_fr, orient="vertical", command=self._log_box.yview,
                           style=CPharm_TSCROLL)
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
                     text="ARM64 Windows: use MuMuPlayer instead of Android SDK",
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
                text="Use MuMuPlayer ARM64 (skip SDK)",
                font=("Segoe UI", 11, "bold"),
                bg=GREEN, fg=ON_ACCENT,
                relief="flat", cursor="hand2",
                padx=16, pady=8, bd=0, highlightthickness=0,
                command=self._activate_mumu_mode,
            )
            self._mumu_mode_btn.configure(activebackground="#22c55e", activeforeground=ON_ACCENT)
            self._mumu_mode_btn.pack(side="left", padx=(0, 10))
            tk.Button(mumu_btn_row,
                      text="Download MuMuPlayer ARM",
                      font=FS, bg=BG3, fg=T1,
                      relief="flat", cursor="hand2", padx=8, pady=6,
                      command=lambda: __import__("webbrowser").open(MUMU_DOWNLOAD_URL),
                      ).pack(side="left")
            tk.Button(
                mumu_btn_row,
                text="Locate MuMu CLI…",
                font=FS, bg=BG3, fg=T1,
                relief="flat", cursor="hand2", padx=8, pady=6,
                command=self._browse_mumu_early,
            ).pack(side="left", padx=(6, 0))
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
            self._log_box.insert("end", text if text.endswith("\n") else text + "\n")
            self._log_box.see("end")
        self.after(0, _do)

    def _set_status(self, icon, text, detail="", color=T1):
        def _do():
            self._icon_lbl.config(text=icon)
            self._status_lbl.config(text=text, fg=color)
            self._detail_lbl.config(text=detail)

        self.after(0, _do)

    def _set_progress(self, pct, label=""):
        def _do():
            self._progress["value"] = pct
            self._progress_lbl.config(text=label)

        self.after(0, _do)

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
                self.after(0, lambda: self._install_btn.config(state="normal",
                                                              text="Try Again"))

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
            self.after(0, lambda: self._install_btn.config(
                state="normal", text="Try Again  (after installing Java)"))
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
                self.after(0, lambda: self._install_btn.config(state="normal", text="Try Again"))
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
        self.after(0, lambda: self._install_btn.config(
            state="normal",
            text="✅  SDK Ready — click Next →",
            bg=GREEN,
        ))

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
            lbl = (f"✅ MuMuPlayer found at {_mumu_install_root(mgr)}\n"
                   "   Steps 2 and 3 will use MuMuPlayer — click Next →")
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

    def _browse_mumu_early(self):
        path = filedialog.askopenfilename(
            title="Select MuMu shell CLI (shell or nx_main folder)",
            filetypes=[
                ("MuMu CLI", "*.exe"),
                ("nemux-shell-winui.Manager", "nemux-shell-winui.Manager.exe"),
                ("MuMuManager (legacy)", "MuMuManager.exe"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        if _is_mumu_gui_path(path):
            messagebox.showwarning(
                "Wrong file",
                "That looks like the MuMu Player app, not the command-line manager.\n\n"
                "Pick nemux-shell-winui.Manager.exe or MuMuManager.exe in shell\\ or nx_main\\:\n"
                "  …\\MuMuPlayerARM\\shell\\nemux-shell-winui.Manager.exe",
            )
            return
        if not _is_mumu_manager_cli_path(path):
            messagebox.showwarning(
                "Wrong file",
                "Please select nemux-shell-winui.Manager.exe or MuMuManager.exe.\n\n"
                "Typical:\n"
                "  …\\MuMuPlayerARM\\shell\\nemux-shell-winui.Manager.exe\n"
                "  …\\MuMuPlayerARM\\shell\\MuMuManager.exe",
            )
            return
        state["mumu_mgr_path"] = path
        if hasattr(self, "_mumu_status_lbl"):
            self._mumu_status_lbl.config(
                text=f"✅ Saved this path — now click “Use MuMuPlayer ARM64” above.",
                fg=GREEN,
            )

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
            "Create virtual phones",
            "Google path: wizard downloads Android 14 and creates Pixel 6 AVDs.\n"
            "MuMu path: create instances in MuMuPlayer — automation comes after phones boot.\n"
            "One-time setup. Each emulator uses ~2 GB RAM + ~4 GB disk."
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
        self._prefix_var.trace_add("write", lambda *_: state.update(
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
                          fg=ON_ACCENT if n == 3 else T1,
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
                                     text="Create phone farm",
                                     font=("Segoe UI", 12, "bold"),
                                     bg=GREEN, fg=ON_ACCENT, relief="flat",
                                     cursor="hand2", command=self._create,
                                     padx=20, pady=10, bd=0, highlightthickness=0)
        self._create_btn.configure(activebackground="#22c55e", activeforeground=ON_ACCENT)
        self._create_btn.pack(side="left", padx=(0, 10))
        top_pf = self.winfo_toplevel()
        trash = load_icon("icon_trash", top_pf)
        del_kw = dict(
            text="Delete all CPharm phones",
            font=FS, bg=RED, fg=ON_ACCENT, relief="flat",
            cursor="hand2", command=self._delete_phones,
            padx=10, pady=8, bd=0, highlightthickness=0,
        )
        if trash:
            del_kw["image"] = trash
            del_kw["compound"] = "left"
        del_btn = tk.Button(create_row, **del_kw)
        style_danger_button(del_btn)
        del_btn.pack(side="left", padx=(0, 10))
        self._progress_lbl = tk.Label(create_row, text="", font=FS, bg=BG, fg=T2)
        self._progress_lbl.pack(side="left")

        # Log
        log_fr = tk.Frame(self, bg=BG2)
        log_fr.pack(fill="both", expand=True, pady=(4, 0))
        self._log_box = tk.Text(log_fr, height=7, font=FM, bg=BG2, fg=T1,
                                relief="flat", wrap="word")
        _attach_readonly_log_text(self._log_box)
        sb = ttk.Scrollbar(log_fr, orient="vertical", command=self._log_box.yview,
                           style=CPharm_TSCROLL)
        self._log_box.configure(yscrollcommand=sb.set)
        self._log_box.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # MuMuPlayer info panel (hidden on non-MuMu machines at on_enter)
        self._mumu_panel = tk.Frame(self, bg="#1a2a1a", padx=14, pady=12,
                                    highlightthickness=1, highlightbackground=GREEN)
        tk.Label(self._mumu_panel,
                 text="MuMuPlayer ARM64 mode",
                 font=("Segoe UI", 12, "bold"), bg="#1a2a1a", fg=GREEN,
                 anchor="w").pack(fill="x")
        tk.Label(self._mumu_panel,
                 text="MuMuPlayer handles phone creation through its own interface.\n\n"
                      "How to create phones:\n"
                      "  1. Open MuMuPlayer (it's in your Start menu or taskbar)\n"
                      "  2. Click the multi-window icon at the top-right\n"
                      "  3. Click '+ New Instance' for each phone you want\n"
                      "  4. In each instance open Settings (≡ or gear) and turn ON ADB / "
                      "debugging — otherwise this PC cannot connect\n"
                      "  5. Leave MuMuPlayer running\n"
                      "  6. Click Next below — the wizard connects automatically\n\n"
                      "Note: MuMu uses OEM-style images (e.g. Google Pixel 8 / GC3VE, HONOR 200 Pro,\n"
                      "  etc.) — whatever appears under Device model here is normal for that template.\n"
                      "  Root / writable system may be off unless you enable them in MuMu settings.",
                 font=FS, bg="#1a2a1a", fg=T2,
                 anchor="w", justify="left").pack(fill="x", pady=(6, 10))
        tk.Button(self._mumu_panel,
                  text="Open MuMuPlayer Now",
                  font=("Segoe UI", 10, "bold"),
                  bg=GREEN, fg="#000000", relief="flat", cursor="hand2",
                  padx=12, pady=6,
                  command=self._open_mumu).pack(anchor="w")

    def _open_mumu(self):
        exe = _find_mumu_player_exe()
        if exe and _launch_gui_exe(exe):
            return
        messagebox.showinfo(
            "MuMuPlayer Not Found",
            "Could not find MuMuPlayer.exe / MuMuNxMain.exe.\n\n"
            "Install MuMu Player ARM from mumuplayer.com, or on Step 3 use "
            "“Locate MuMuManager.exe” so the wizard can find your install folder.\n\n"
            "You can still open MuMu from the Start menu."
        )

    def _pick_count(self, n):
        state["num_phones"] = n
        for num, btn in self._count_btns.items():
            btn.config(bg=ACCENT if num == n else BG2,
                       fg=ON_ACCENT    if num == n else T1)
        ram  = n * 2
        disk = n * 4
        self._count_lbl.config(
            text=f"{n} phone{'s' if n != 1 else ''} selected  "
                 f"(~{ram} GB RAM, ~{disk} GB disk needed)")

    def on_enter(self):
        if state.get("use_mumu"):
            self._mumu_panel.pack(fill="x", pady=(6, 0))
            self._log_write(
                "MuMuPlayer ARM64 mode — no AVD creation needed.\n"
                "Open MuMuPlayer, create instances, enable ADB in each instance’s settings, "
                "then click Next →\n"
            )

            def _prep_adb():
                _ensure_minimal_platform_tools(log_fn=self._log_write)
                self._log_write(f"Using adb: {adb_executable()}\n")
                _warn_arm64_x64_adb(self._log_write)

            threading.Thread(target=_prep_adb, daemon=True).start()
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
            self._log_box.insert("end", text)
            self._log_box.see("end")
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

            btn_txt = "Phones created" if created else "Try again"
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
        self._boot_row_images: list[tk.PhotoImage] = []
        self.header(
            "Connect & verify — boot phones",
            "Boot MuMu instances or AVDs, confirm ADB, optional Chrome check. "
            "Automation server and scheduler run from the Launch step."
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
        self._boot_btn = tk.Button(phone_row, text="Start all phones",
                                  font=("Segoe UI", 10, "bold"),
                                  bg=GREEN, fg=ON_ACCENT, relief="flat",
                                  cursor="hand2", command=self._boot_all,
                                  bd=0, highlightthickness=0)
        self._boot_btn.configure(activebackground="#22c55e", activeforeground=ON_ACCENT)
        self._boot_btn.pack(side="left", padx=(0, 8))
        self._stop_btn = tk.Button(phone_row, text="Stop all",
                                  font=("Segoe UI", 10, "bold"),
                                  bg=RED, fg=ON_ACCENT, relief="flat",
                                  cursor="hand2", command=self._stop_all,
                                  state="disabled", bd=0, highlightthickness=0)
        style_danger_button(self._stop_btn)
        self._stop_btn.pack(side="left")
        self._overall_lbl = tk.Label(phone_row, text="",
                                     font=FS, bg=BG3, fg=T2)
        self._overall_lbl.pack(side="left", padx=10)

        self._mumu_cfg_row = tk.Frame(phone_ctrl, bg=BG3)
        tk.Label(self._mumu_cfg_row, text="MuMu CLI  (nemux…Manager.exe or MuMuManager.exe):",
                 font=FS, bg=BG3, fg=T2).pack(side="left")
        self._mumu_path_var = tk.StringVar(value="")
        self._mumu_path_entry = tk.Entry(
            self._mumu_cfg_row, textvariable=self._mumu_path_var,
            font=FM, bg=BG2, fg=T1, insertbackground=T1,
            relief="flat", width=40,
        )
        self._mumu_path_entry.pack(side="left", padx=6)
        tk.Button(self._mumu_cfg_row, text="Browse…",
                  font=FS, bg=BG3, fg=T1, relief="flat", cursor="hand2",
                  command=self._browse_mumu_mgr).pack(side="left")
        self._mumu_hint = tk.Label(phone_ctrl,
                 text="Hint: …\\MuMuPlayerARM\\shell\\nemux-shell-winui.Manager.exe  (or MuMuManager.exe)",
                 font=("Segoe UI", 9), bg=BG3, fg=T3, anchor="w")

        # Chrome setup + URL test
        chrome_box = tk.Frame(self, bg=BG3, padx=14, pady=10)
        chrome_box.pack(fill="x", pady=(0, 8))
        tk.Label(chrome_box,
                 text="Google Chrome on each phone",
                 font=("Segoe UI", 10, "bold"), bg=BG3, fg=YELLOW,
                 anchor="w").pack(fill="x")
        tk.Label(chrome_box,
                 text="Default: enable / pm install-existing / optional bundled APK under wizard/bundled/ — "
                      "no Google login. Use **Install APK on all phones** to push one Chrome APK to every "
                      "serial, or try Fennec/Mull (F-Droid) as an alternative browser.",
                 font=FS, bg=BG3, fg=T2, anchor="w").pack(fill="x", pady=(2, 6))
        chrome_ctrl = tk.Frame(chrome_box, bg=BG3)
        chrome_ctrl.pack(fill="x")
        self._chrome_btn = tk.Button(chrome_ctrl,
                                   text="Configure Chrome on all phones",
                                   font=("Segoe UI", 10, "bold"),
                                   bg=YELLOW, fg=ON_ACCENT, relief="flat",
                                   cursor="hand2", command=self._setup_chrome_all,
                                   bd=0, highlightthickness=0)
        self._chrome_btn.pack(side="left", padx=(0, 10))
        opt_ps = tk.Button(
            chrome_ctrl,
            text="Optional: Play Store install…",
            font=FS,
            bg=BG3,
            fg=T2,
            relief="flat",
            cursor="hand2",
            command=self._setup_chrome_via_play_store_all,
            bd=0,
            highlightthickness=0,
        )
        style_secondary_button(opt_ps)
        opt_ps.pack(side="left", padx=(0, 10))
        self._chrome_lbl = tk.Label(chrome_ctrl, text="", font=FS, bg=BG3, fg=T2)
        self._chrome_lbl.pack(side="left")

        chrome_extra = tk.Frame(chrome_box, bg=BG3)
        chrome_extra.pack(fill="x", pady=(8, 0))
        install_apk_btn = tk.Button(
            chrome_extra,
            text="Install APK on all phones…",
            font=FS,
            bg=BG3,
            fg=ACCENT,
            relief="flat",
            cursor="hand2",
            command=self._install_user_apk_all_phones,
            bd=0,
            highlightthickness=0,
        )
        style_secondary_button(install_apk_btn)
        install_apk_btn.pack(side="left", padx=(0, 10))
        fdroid_btn = tk.Button(
            chrome_extra,
            text="Try Fennec / Mull on all phones",
            font=FS,
            bg=BG3,
            fg=T2,
            relief="flat",
            cursor="hand2",
            command=self._setup_fdroid_browsers_all,
            bd=0,
            highlightthickness=0,
        )
        style_secondary_button(fdroid_btn)
        fdroid_btn.pack(side="left")
        # Test URL row
        test_row = tk.Frame(chrome_box, bg=BG3)
        test_row.pack(fill="x", pady=(8, 0))
        tk.Label(test_row, text="Test URL on Phone 1:",
                 font=FS, bg=BG3, fg=T2).pack(side="left")
        self._test_url_var = tk.StringVar(value="https://google.com")
        tk.Entry(test_row, textvariable=self._test_url_var, font=FM,
                 bg=BG2, fg=T1, insertbackground=T1, relief="flat",
                 width=34).pack(side="left", padx=6)
        ob = tk.Button(test_row, text="Open",
                  font=("Segoe UI", 10, "bold"),
                  command=self._test_url)
        style_primary_button(ob)
        ob.pack(side="left")

        # Summary
        summary_outer = tk.Frame(self, bg=BG2, padx=12, pady=10)
        summary_outer.pack(fill="x", pady=(0, 10))
        tk.Label(summary_outer, text="Your phones:",
                 font=("Segoe UI", 10, "bold"), bg=BG2, fg=T1,
                 anchor="w").pack(fill="x")
        self._summary = tk.Text(summary_outer, height=4, font=FM, bg=BG2, fg=T1,
                                relief="flat", wrap="word")
        _attach_readonly_log_text(self._summary)
        self._summary.pack(fill="x")

        # Status grid — one row per target phone before boot
        self._status_frame = tk.Frame(self, bg=BG)
        self._status_frame.pack(fill="both", expand=True, pady=(SP["sm"], SP["xs"]))
        self._status_rows = {}

        tk.Label(self, text="Boot log",
                 font=("Segoe UI", 9, "bold"), bg=BG, fg=T2, anchor="w").pack(
                     fill="x", pady=(SP["sm"], 2))
        log_fr = tk.Frame(self, bg=BG2, highlightthickness=1,
                          highlightbackground=BORDER)
        log_fr.pack(fill="both", expand=True)
        self._log_box = tk.Text(log_fr, height=8, font=FM, bg=BG2, fg=T1,
                                relief="flat", wrap="word")
        _attach_readonly_log_text(self._log_box)
        log_sb = ttk.Scrollbar(log_fr, orient="vertical",
                               command=self._log_box.yview, style=CPharm_TSCROLL)
        self._log_box.configure(yscrollcommand=log_sb.set)
        self._log_box.pack(side="left", fill="both", expand=True)
        log_sb.pack(side="right", fill="y")

    def on_enter(self):
        self._rebuild_grid()

        if state.get("use_mumu"):
            detected = str(_find_mumu_manager() or "")
            if detected and not self._mumu_path_var.get():
                self._mumu_path_var.set(detected)
            self._mumu_cfg_row.pack(fill="x", pady=(0, 2))
            self._mumu_hint.pack(fill="x", pady=(0, 4))
        else:
            self._mumu_cfg_row.pack_forget()
            self._mumu_hint.pack_forget()

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
                    for p in phones:
                        try:
                            setup_chrome(p["serial"], offer_play_store=False)
                        except Exception:
                            pass
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
                        text="No MuMu phones found — start instances, enable ADB in each instance’s settings, retry",
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
        self._boot_row_images.clear()
        top = self.winfo_toplevel()

        if state.get("use_mumu"):
            mgr = _find_mumu_manager()
            if mgr:
                instances = _mumu_get_instances(mgr)
                for inst in instances:
                    key = inst["adb_serial"]
                    row = tk.Frame(self._status_frame, bg=BG2, padx=12, pady=7,
                                   highlightthickness=1, highlightbackground=BORDER)
                    row.pack(fill="x", pady=2)
                    im = load_icon("row_mumu", top)
                    if im:
                        self._boot_row_images.append(im)
                        tk.Label(row, image=im, bg=BG2).pack(side="left", padx=(0, 6))
                    else:
                        tk.Label(row, text="MuMu", font=FONT_CAPTION, bg=BG2, fg=T3
                                 ).pack(side="left", padx=(0, 6))
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
            im = load_icon("row_avd", top)
            if im:
                self._boot_row_images.append(im)
                tk.Label(row, image=im, bg=BG2).pack(side="left", padx=(0, 6))
            else:
                tk.Label(row, text="AVD", font=FONT_CAPTION, bg=BG2, fg=T3
                         ).pack(side="left", padx=(0, 6))
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
            im = load_icon("row_usb", top)
            if im:
                self._boot_row_images.append(im)
                tk.Label(row, image=im, bg=BG2).pack(side="left", padx=(0, 6))
            else:
                tk.Label(row, text="USB", font=FONT_CAPTION, bg=BG2, fg=T3
                         ).pack(side="left", padx=(0, 6))
            tk.Label(row, text=d["name"], font=FB, bg=BG2, fg=T1,
                     width=26, anchor="w").pack(side="left")
            tk.Label(row, text=f"USB: {s}", font=FM, bg=BG2,
                     fg=T3, width=20, anchor="w").pack(side="left")
            lbl = tk.Label(row, text="Connected", font=FS, bg=BG2, fg=GREEN)
            lbl.pack(side="left", padx=6)
            self._status_rows[s] = lbl

    def _browse_mumu_mgr(self):
        from tkinter import filedialog
        default_dir = str(
            Path(os.environ.get("PROGRAMFILES", "C:\\")) / "Netease"
        )
        path = filedialog.askopenfilename(
            title="Select MuMu shell CLI — shell\\ or nx_main\\ under MuMuPlayerARM",
            filetypes=[
                ("MuMu CLI", "*.exe"),
                ("nemux-shell-winui.Manager", "nemux-shell-winui.Manager.exe"),
                ("MuMuManager (legacy)", "MuMuManager.exe"),
                ("All executables", "*.exe"),
                ("All files", "*.*"),
            ],
            initialdir=default_dir,
        )
        if path:
            name = Path(path).name
            if _is_mumu_gui_path(path):
                messagebox.showwarning(
                    "Not the CLI",
                    f"⚠ '{name}' looks like the MuMu Player GUI.\n"
                    "  Pick nemux-shell-winui.Manager.exe or MuMuManager.exe in shell\\.",
                )
                return
            if not _is_mumu_manager_cli_path(path):
                messagebox.showwarning(
                    "Not MuMu CLI",
                    f"⚠ '{name}' is not the MuMu command-line manager.\n"
                    "  Typical:\n"
                    "  …\\shell\\nemux-shell-winui.Manager.exe\n"
                    "  …\\shell\\MuMuManager.exe",
                )
                return
            state["mumu_mgr_path"] = path
            self._mumu_path_var.set(path)
            self._log_write(f"MuMu CLI set ({name}): {path}\n")

    def _log_write(self, text):
        def _do():
            self._log_box.insert("end", text)
            self._log_box.see("end")
        self.after(0, _do)

    def _boot_all(self):
        # ── MuMuPlayer mode ───────────────────────────────────────────────────
        if state.get("use_mumu"):
            self._boot_btn.config(state="disabled", text="Connecting…")
            self._log_write("MuMuPlayer mode — launching and connecting instances…\n")
            def go_mumu():
                mgr = _find_mumu_manager()
                if not mgr:
                    self._log_write(
                        "❌ MuMu shell CLI not found "
                        "(nemux-shell-winui.Manager.exe or MuMuManager.exe in shell\\).\n"
                        f"   Install MuMuPlayer ARM from: {MUMU_DOWNLOAD_URL}\n"
                    )
                    self.after(0, lambda: (
                        self._boot_btn.config(state="normal", text="Start all phones"),
                        self._overall_lbl.config(text="❌ MuMuPlayer not installed", fg=RED),
                    ))
                    return

                instances = _mumu_get_instances(mgr, log_fn=self._log_write)
                if not instances:
                    self._log_write(
                        "  Manager CLI returned no instances — trying ADB port scan fallback…\n"
                        "  If this keeps finding nothing, enable ADB in each instance’s "
                        "Settings (see final message below).\n"
                    )
                    connected = _connect_mumu_phones(count=12, log_fn=self._log_write)
                    if connected:
                        phones_from_scan = [{"serial": s, "name": f"MuMu-{i}"} for i, s in enumerate(connected)]
                        state["phones"] = phones_from_scan
                        state["_emu_procs"] = []
                        n = len(phones_from_scan)
                        self.after(0, lambda: (
                            self._boot_btn.config(state="normal", text="Start all phones"),
                            self._stop_btn.config(state="normal"),
                            self._overall_lbl.config(text=f"✅ {n} MuMu phone(s) ready (port scan)", fg=GREEN),
                        ))
                        self._log_write(f"\n{n} phone(s) found via port scan.\n")
                        return
                    self._log_write(
                        "❌ No MuMuPlayer instances found via CLI or port scan.\n"
                        "   Make sure MuMuPlayer is open and instances are running.\n"
                        f"   {MUMU_ADB_PER_INSTANCE_HINT}\n"
                        "   Open MuMuPlayer → click ⧉ (multi-window icon) → '+ New Instance'\n"
                    )
                    self.after(0, lambda: (
                        self._boot_btn.config(state="normal", text="Start all phones"),
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
                        try:
                            if setup_chrome(serial, log_fn=self._log_write, offer_play_store=False):
                                self._log_write(f"  ✅ {name}: {serial}  ID:{new_id[:8]}… Chrome ✓\n")
                            else:
                                self._log_write(
                                    f"  ✅ {name}: {serial}  ID:{new_id[:8]}… "
                                    "(Chrome missing — sideload APK, add bundled/chrome.apk, or optional Play Store)\n"
                                )
                        except Exception as e:
                            self._log_write(f"  ⚠ {name}: {e}\n")
                        row_lbl = self._status_rows.get(serial)
                        if row_lbl:
                            self.after(0, lambda lb=row_lbl: lb.config(text="✅ Running", fg=GREEN))
                    else:
                        self._log_write(f"  ❌ {name}: ADB not responding on {serial}\n")

                state["phones"]      = phones
                state["_emu_procs"]  = []
                n = len(phones)
                self.after(0, lambda: (
                    self._boot_btn.config(state="normal", text="Start all phones"),
                    self._stop_btn.config(state="disabled" if not phones else "normal"),
                    self._overall_lbl.config(
                        text=f"✅ {n} MuMuPlayer phone(s) ready" if phones else "❌ No phones connected",
                        fg=GREEN if phones else RED),
                ))
                self._log_write(f"\n{n} MuMuPlayer phone(s) ready.\n")
            threading.Thread(target=go_mumu, daemon=True).start()
            return

        # ── AVD emulator mode ───────────────────────────────────────────────────
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
                                 "Go back to Create phones and finish there first.\n\n"
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
                self._log_write(
                    "❌ ARM64 (Snapdragon) detected — Google has no Windows ARM64 emulator.\n"
                    "   Go back to Android runtime and enable  ✅ Use MuMuPlayer ARM64.\n"
                    f"   Download: {MUMU_DOWNLOAD_URL}\n"
                )
                messagebox.showerror(
                    "MuMuPlayer required",
                    "ARM64 (Snapdragon) detected — Google has no Windows ARM64 emulator.\n\n"
                    "Go back to Android runtime and click  ✅ Use MuMuPlayer ARM64  to switch modes.\n\n"
                    f"Download MuMuPlayer ARM:  {MUMU_DOWNLOAD_URL}",
                )
                self._boot_btn.config(state="normal", text="Start all phones")
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
                        setup_chrome(serial, offer_play_store=False)
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
                self._boot_btn.config(state="normal", text="Start all phones"),
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
        self._boot_btn.config(state="normal", text="Start all phones")
        self._stop_btn.config(state="disabled")
        self._overall_lbl.config(text="All phones stopped.", fg=T2)
        self._log_write("All phones stopped.\n")

    def _setup_chrome_all(self):
        phones = state.get("phones", [])
        if not phones:
            messagebox.showwarning("No phones", "Boot phones first.")
            return
        self._chrome_btn.config(state="disabled", text="Configuring Chrome...")
        self._chrome_lbl.config(text="", fg=T2)
        def go():
            for p in phones:
                try:
                    setup_chrome(p["serial"], log_fn=self._log_write, offer_play_store=False)
                    self._log_write(f"  ✅ {p['name']}: Chrome ready\n")
                except Exception as e:
                    self._log_write(f"  ⚠ {p['name']}: {e}\n")
            def done_ui():
                self._chrome_btn.config(state="normal", text="Configure Chrome on all phones")
                self._log_write("Chrome configure pass finished (no Play Store).\n")

            self.after(0, done_ui)
        threading.Thread(target=go, daemon=True).start()

    def _setup_chrome_via_play_store_all(self):
        phones = state.get("phones", [])
        if not phones:
            messagebox.showwarning("No phones", "Boot phones first.")
            return
        if not messagebox.askokcancel(
            "Play Store (optional)",
            "This opens the Google Play Store for Chrome on each phone.\n"
            "You need a Google account signed in on the device.\n\n"
            "The recommended path stays sideload / pm install-existing / bundled APK — "
            "no Google login.\n\n"
            "Continue with Play Store?",
        ):
            return
        self._chrome_btn.config(state="disabled")
        self._chrome_lbl.config(text="", fg=T2)

        def go():
            for p in phones:
                try:
                    setup_chrome(p["serial"], log_fn=self._log_write, offer_play_store=True)
                    self._log_write(f"  ✅ {p['name']}: Chrome ready\n")
                except Exception as e:
                    self._log_write(f"  ⚠ {p['name']}: {e}\n")

            def done_ui():
                self._chrome_btn.config(state="normal")
                self._log_write("Play Store Chrome pass finished.\n")

            self.after(0, done_ui)

        threading.Thread(target=go, daemon=True).start()

    def _install_user_apk_all_phones(self):
        phones = state.get("phones", [])
        if not phones:
            messagebox.showwarning("No phones", "Boot phones first.")
            return
        path = filedialog.askopenfilename(
            title="Choose APK to install on all connected phones",
            filetypes=[("Android package", "*.apk"), ("All files", "*.*")],
        )
        if not path:
            return
        serials = [p["serial"] for p in phones]
        self._log_write(
            f"\nInstalling APK on {len(serials)} device(s): {Path(path).name}\n"
            "(Obtain APK yourself — e.g. Chrome from Play on one device then reuse the build, "
            "or a trusted mirror matching your ABI.)\n"
        )

        def go():
            summary = _install_apk_all_phones(path, serials, log_fn=self._log_write)
            for p in phones:
                match = next(
                    (r for r in summary["results"] if r["serial"] == p["serial"]),
                    None,
                )
                st = "✅" if match and match["ok"] else "❌"
                self._log_write(f"  {st} {p['name']}: {p['serial']}\n")
            self._log_write(
                "APK install pass finished.\n"
                "If this was Chrome, tap **Configure Chrome on all phones** next.\n"
            )

        threading.Thread(target=go, daemon=True).start()

    def _setup_fdroid_browsers_all(self):
        phones = state.get("phones", [])
        if not phones:
            messagebox.showwarning("No phones", "Boot phones first.")
            return
        self._log_write(
            "\nFennec / Mull: trying install-existing, then optional wizard/bundled/ "
            "(Fennec.apk, Mull.apk). Packages: "
            f"{FDROID_FENNEC_PKG}, {FDROID_MULL_PKG}\n"
        )

        def go():
            for p in phones:
                serial = p["serial"]
                try:
                    pkg = ensure_fdroid_browser(serial, log_fn=self._log_write)
                    if pkg:
                        self._log_write(f"  ✅ {p['name']}: {pkg}\n")
                    else:
                        self._log_write(
                            f"  ⚠ {p['name']}: no Fennec/Mull — install from F-Droid app or add "
                            f"bundled APK under wizard/bundled/\n"
                        )
                except Exception as e:
                    self._log_write(f"  ⚠ {p['name']}: {e}\n")
            self._log_write("Fennec/Mull pass finished.\n")

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


# ─── page: automation — default sequence (after devices exist) ────────────────


class AutomationPage(PageBase):
    """Edit default automation steps once phones are booted — feeds Groups."""

    def __init__(self, parent):
        super().__init__(parent)
        self._seq_steps: list = []
        self.header(
            "Automation — default sequence",
            "Optional template applied when assigning phones to groups. "
            "Per-phone overrides stay on the Groups step."
        )

        intro = tk.Frame(self, bg=BG2, padx=SP["card"], pady=SP["sm"],
                         highlightthickness=1, highlightbackground=BORDER)
        intro.pack(fill="x", pady=(0, SP["sm"]))
        tk.Label(
            intro,
            text="Tor rotation and Play flows stay supported — same step types as before.",
            font=FS, bg=BG2, fg=T2, justify="left", anchor="w",
        ).pack(fill="x")

        seq_row = tk.Frame(self, bg=BG)
        seq_row.pack(fill="x", pady=(SP["sm"], 0))
        tk.Button(
            seq_row, text="Edit default sequence",
            font=("Segoe UI", 10, "bold"),
            bg=ACCENT, fg=ON_ACCENT, relief="flat", cursor="hand2",
            padx=SP["md"], pady=SP["sm"],
            command=self._edit_sequence,
        ).pack(side="left", padx=(0, SP["sm"]))
        tk.Button(
            seq_row, text="Save JSON…",
            font=FS, bg=BG3, fg=T1, relief="flat", cursor="hand2",
            padx=SP["sm"], pady=SP["sm"],
            command=self._save_sequence,
        ).pack(side="left", padx=(0, SP["xs"]))
        tk.Button(
            seq_row, text="Load JSON…",
            font=FS, bg=BG3, fg=T1, relief="flat", cursor="hand2",
            padx=SP["sm"], pady=SP["sm"],
            command=self._load_sequence,
        ).pack(side="left", padx=(0, SP["sm"]))
        self._seq_lbl = tk.Label(seq_row, text="No steps defined",
                                 font=FS, bg=BG, fg=T3)
        self._seq_lbl.pack(side="left")

    def on_enter(self):
        self._seq_steps.clear()
        self._seq_steps.extend(state.get("default_steps", []))
        n = len(self._seq_steps)
        self._seq_lbl.config(
            text=f"{n} step{'s' if n != 1 else ''} in template" if n else "No steps defined",
            fg=T1 if n else T3,
        )

    def can_advance(self):
        if not state.get("phones"):
            messagebox.showwarning(
                "Boot phones first",
                "Go back one step and start your phones so ADB sees them.",
            )
            return False
        state["default_steps"] = list(self._seq_steps)
        return True

    def _edit_sequence(self):
        dlg = PerPhoneSequenceEditor(
            self,
            serial="default",
            phone_name="Default sequence (template for group assignments)",
            steps_list=self._seq_steps,
        )
        self.wait_window(dlg)
        n = len(self._seq_steps)
        self._seq_lbl.config(
            text=f"{n} step{'s' if n != 1 else ''} in template" if n else "No steps defined",
            fg=T1 if n else T3,
        )
        state["default_steps"] = list(self._seq_steps)

    def _save_sequence(self):
        if not self._seq_steps:
            messagebox.showinfo("Nothing to save", "Add steps first via Edit.")
            return
        path = filedialog.asksaveasfilename(
            title="Save sequence",
            defaultextension=".json",
            filetypes=[("JSON sequence", "*.json"), ("All files", "*.*")],
            initialfile="default_sequence.json",
        )
        if not path:
            return
        Path(path).write_text(json.dumps(self._seq_steps, indent=2))
        messagebox.showinfo("Saved", f"Sequence saved to:\n{path}")

    def _load_sequence(self):
        path = filedialog.askopenfilename(
            title="Load sequence",
            filetypes=[("JSON sequence", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text())
        except Exception as exc:
            messagebox.showerror("Load failed", str(exc))
            return
        if not isinstance(data, list):
            messagebox.showerror("Invalid file", "File must contain a JSON array of steps.")
            return
        self._seq_steps.clear()
        self._seq_steps.extend(data)
        state["default_steps"] = list(self._seq_steps)
        n = len(self._seq_steps)
        self._seq_lbl.config(
            text=f"{n} step{'s' if n != 1 else ''} loaded" if n else "No steps defined",
            fg=T1 if n else T3,
        )


# ─── page: google play testing guide ───────────────────────────────────────────

class PlayStorePage(PageBase):
    """Explains how to use the phone farm for Google Play closed testing."""
    def __init__(self, parent):
        super().__init__(parent)
        self.header(
            "Google Play closed testing",
            "How to use your virtual phones to get your app into the Play Store."
        )

        # Canvas scrolled area
        outer = tk.Frame(self, bg=BG)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        sb     = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview,
                               style=CPharm_TSCROLL)
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
                  font=("Segoe UI", 10, "bold"), bg=GREEN, fg=ON_ACCENT,
                  relief="flat", cursor="hand2", padx=14, pady=8,
                  command=lambda: webbrowser.open("https://play.google.com/console")).pack(
                      anchor="w", pady=10, padx=2)


# ─── page 6: groups & sequences ───────────────────────────────────────────────


class GroupsPage(PageBase):
    def __init__(self, parent):
        super().__init__(parent)
        self.header(
            "Groups & sequences",
            "Split phones into groups. Each group runs its own sequence — stagger, repeat, "
            "and per-phone overrides stay available below."
        )

        intro = tk.Frame(self, bg=BG2, padx=14, pady=10,
                         highlightthickness=1, highlightbackground=BORDER)
        intro.pack(fill="x", pady=(0, 10))
        tk.Label(intro,
                 text="Assign phones with checkboxes, edit per-phone sequences, clone from Phone 1, "
                      "and tune timing — nothing was removed; styling only tightened elsewhere.",
                 font=FS, bg=BG2, fg=T2, justify="left", anchor="w").pack(fill="x")

        ctrl = tk.Frame(self, bg=BG)
        ctrl.pack(fill="x", pady=(0, 8))
        self.btn(ctrl, "+ Add Group", self._add_group, color=GREEN)
        self._count_lbl = tk.Label(ctrl, text="", font=FS, bg=BG, fg=T2)
        self._count_lbl.pack(side="left", padx=8)

        wrapper = tk.Frame(self, bg=BG)
        wrapper.pack(fill="both", expand=True)
        self._canvas = tk.Canvas(wrapper, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(wrapper, orient="vertical", command=self._canvas.yview,
                           style=CPharm_TSCROLL)
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
        default_steps = state.get("default_steps", [])
        if state["phones"] and state["groups"]:
            for g in state["groups"]:
                phones_field = g["phones"]
                if isinstance(phones_field, list):
                    phones_field = {p: {"steps": list(default_steps)}
                                    for p in phones_field}
                    g["phones"] = phones_field
                elif not isinstance(phones_field, dict):
                    g["phones"] = {}
                if not phones_field and state["phones"]:
                    for p in state["phones"]:
                        g["phones"][p["serial"]] = {"steps": list(default_steps)}
        self._rebuild()

    def _add_group(self):
        n = len(state["groups"]) + 1
        default_steps = state.get("default_steps", [])
        phones_map = {p["serial"]: {"steps": list(default_steps)}
                      for p in state.get("phones", [])}
        state["groups"].append({
            "name":           f"Group {n}",
            "phones":         phones_map,
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
        name_var.trace_add("write", lambda *_: group.update({"name": name_var.get()}))

        if len(state["groups"]) > 1:
            rm = tk.Button(hdr, text="Remove", font=FS, bg=RED, fg=ON_ACCENT,
                           relief="flat", cursor="hand2", padx=8, pady=3, bd=0,
                           highlightthickness=0,
                           command=lambda i=idx: self._remove_group(i))
            style_danger_button(rm)
            rm.pack(side="right")

        tk.Frame(card, bg=BORDER, height=1).pack(fill="x", pady=8)

        # ── Per-phone sequence editor ─────────────────────────────────────
        phones_frame = tk.Frame(card, bg=BG2)
        phones_frame.pack(fill="x", pady=(0, 8))

        tk.Label(phones_frame, text="Per-Phone Sequences",
                 font=("Segoe UI", 10, "bold"), bg=BG2, fg=T1, anchor="w").pack(fill="x")

        phone_map = group.get("phones", {})

        if not state["phones"]:
            tk.Label(phones_frame, text="Boot phones on Connect & boot before assigning groups.",
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

                ed_img = load_icon("icon_edit", self.winfo_toplevel())
                eb_kw = dict(
                    text="Edit",
                    font=FS, bg=ACCENT, fg=ON_ACCENT, relief="flat",
                    cursor="hand2", command=edit_phone,
                    padx=6, pady=2, bd=0, highlightthickness=0,
                )
                if ed_img:
                    eb_kw["image"] = ed_img
                    eb_kw["compound"] = "left"
                eb = tk.Button(row, **eb_kw)
                style_primary_button(eb)
                eb.pack(side="left", padx=(2, 0))

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
                    self._log_write(f"[{state['groups'][i]['name']}] Cloned to all phones.")

                cl_img = load_icon("icon_clone", self.winfo_toplevel())
                cb_kw = dict(
                    text="Clone to all phones (Phone 1 is master)",
                    font=FS, bg=PURPLE, fg=ON_ACCENT, relief="flat", cursor="hand2",
                    command=clone_all, padx=8, pady=4, bd=0, highlightthickness=0,
                )
                if cl_img:
                    cb_kw["image"] = cl_img
                    cb_kw["compound"] = "left"
                cb = tk.Button(clone_row, **cb_kw)
                cb.configure(activebackground="#8b5cf6", activeforeground=ON_ACCENT)
                cb.pack(side="left")

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

        stag_var.trace_add("write", save)
        rep_var.trace_add("write", save)
        forever_var.trace_add("write", save)

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
                  bg=GREEN, fg=ON_ACCENT, relief="flat",
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
            "Launch — server, groups, scheduler",
            "Start the dashboard API, run groups, optional daily schedule, open the web UI."
        )

        # Summary
        summary_outer = tk.Frame(self, bg=BG2, padx=12, pady=10)
        summary_outer.pack(fill="x", pady=(0, 10))
        tk.Label(summary_outer, text="Your groups:",
                 font=("Segoe UI", 10, "bold"), bg=BG2, fg=T1,
                 anchor="w").pack(fill="x")
        self._summary = tk.Text(summary_outer, height=4, font=FM, bg=BG2, fg=T1,
                                relief="flat", wrap="word")
        _attach_readonly_log_text(self._summary)
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
        self._btn_srv_start = tk.Button(srv_row, text="Start server",
                                        font=("Segoe UI", 10, "bold"),
                                        bg=GREEN, fg=ON_ACCENT, relief="flat",
                                        cursor="hand2", command=self._start_server,
                                        bd=0, highlightthickness=0)
        self._btn_srv_start.configure(activebackground="#22c55e", activeforeground=ON_ACCENT)
        self._btn_srv_start.pack(side="left", padx=(0, 8))
        self._btn_srv_stop = tk.Button(srv_row, text="Stop server",
                                       font=("Segoe UI", 10, "bold"),
                                       bg=RED, fg=ON_ACCENT, relief="flat",
                                       cursor="hand2", command=self._stop_server,
                                       state="disabled", bd=0, highlightthickness=0)
        style_danger_button(self._btn_srv_stop)
        self._btn_srv_stop.pack(side="left")
        self._srv_lbl = tk.Label(srv_row, text="Server not running",
                                  font=FS, bg=BG3, fg=T2)
        self._srv_lbl.pack(side="left", padx=10)

        # Groups (B — after server)
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
        self._btn_run = tk.Button(grp_row, text="Run all groups",
                                  font=("Segoe UI", 10, "bold"),
                                  bg=GREEN, fg=ON_ACCENT, relief="flat",
                                  cursor="hand2", command=self._run_groups,
                                  bd=0, highlightthickness=0)
        self._btn_run.configure(activebackground="#22c55e", activeforeground=ON_ACCENT)
        self._btn_run.pack(side="left", padx=(0, 8))
        self._btn_stop_grp = tk.Button(grp_row, text="Stop all groups",
                                       font=("Segoe UI", 10, "bold"),
                                       bg=RED, fg=ON_ACCENT, relief="flat",
                                       cursor="hand2", state="disabled",
                                       command=self._stop_groups,
                                       bd=0, highlightthickness=0)
        style_danger_button(self._btn_stop_grp)
        self._btn_stop_grp.pack(side="left")
        self._run_lbl = tk.Label(grp_row, text="", font=FS, bg=BG3, fg=T2)
        self._run_lbl.pack(side="left", padx=10)

        # Schedule (C)
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
                                   text="Start schedule",
                                   font=("Segoe UI", 10, "bold"),
                                   bg=PURPLE, fg=ON_ACCENT, relief="flat",
                                   cursor="hand2", command=self._start_schedule,
                                   bd=0, highlightthickness=0)
        self._sched_btn.configure(activebackground="#8b5cf6", activeforeground=ON_ACCENT)
        self._sched_btn.pack(side="left", padx=(8, 0))
        self._sched_lbl = tk.Label(sched_row, text="", font=FS, bg=BG3, fg=T2)
        self._sched_lbl.pack(side="left", padx=8)

        # Log
        tk.Label(self, text="Live log:", font=("Segoe UI", 9, "bold"),
                 bg=BG, fg=T2, anchor="w").pack(fill="x", pady=(4, 2))
        log_fr = tk.Frame(self, bg=BG2)
        log_fr.pack(fill="both", expand=True)
        self._log_box = tk.Text(log_fr, height=7, font=FM, bg=BG2, fg=T1,
                                relief="flat", wrap="word")
        _attach_readonly_log_text(self._log_box)
        log_sb = ttk.Scrollbar(log_fr, orient="vertical",
                               command=self._log_box.yview, style=CPharm_TSCROLL)
        self._log_box.configure(yscrollcommand=log_sb.set)
        self._log_box.pack(side="left", fill="both", expand=True)
        log_sb.pack(side="right", fill="y")

        misc = tk.Frame(self, bg=BG)
        misc.pack(fill="x", pady=SP["sm"])
        tk.Button(misc, text="Open dashboard in browser",
                  font=FS, bg=BG3, fg=T1, relief="flat", cursor="hand2",
                  command=lambda: webbrowser.open(
                      f"http://localhost:{DASHBOARD_PORT}")).pack(side="left", padx=(0, SP["sm"]))
        tk.Button(misc, text="Save groups config",
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
        self._summary.delete("1.0", "end")
        self._summary.insert("end", "\n".join(lines) or "  (no groups)")

    def _log(self, text):
        def _do():
            self._log_box.insert("end", text + "\n")
            self._log_box.see("end")
        self.after(0, _do)

    _log_write = lambda self, t: self._log(t)

    def _save(self):
        d = (state.get("cpharm_dir") or "").strip()
        rec = (Path(d) if d else _cpharm_install_root()) / "automation" / "recordings"
        rec.mkdir(parents=True, exist_ok=True)
        out = rec / "groups_config.json"
        out.write_text(json.dumps({"groups": state["groups"]}, indent=2))
        return str(out)

    def _start_server(self):
        import tempfile

        d = state.get("cpharm_dir", "")
        if not d:
            d = str(_cpharm_install_root())
            state["cpharm_dir"] = d

        self._save()
        dashboard = Path(d) / "automation" / "dashboard.py"
        if not dashboard.exists():
            messagebox.showerror("Not found",
                                 f"dashboard.py not found at:\n{dashboard}\n\n"
                                 "Make sure CPharm is cloned correctly.")
            return

        self._btn_srv_start.config(state="disabled")
        self._srv_lbl.config(text="Installing packages…", fg=YELLOW)
        self._log("Checking/installing websockets + psutil…")

        def _launch():
            try:
                py = state.get("python_cmd", "python")
                if not py:
                    py = "python"
                cf = _NO_WIN

                self.after(0, lambda: self._log(f"Using Python: {py}"))

                pip = subprocess.run(
                    [py, "-m", "pip", "install", "--quiet",
                     "websockets>=12.0", "psutil>=5.9.0"],
                    capture_output=True, text=True, timeout=120,
                    creationflags=cf,
                )
                if pip.returncode != 0:
                    err = pip.stderr.strip() or pip.stdout.strip() or "pip failed"
                    self.after(0, lambda: (
                        self._srv_lbl.config(text="pip failed", fg=RED),
                        self._btn_srv_start.config(state="normal"),
                        self._log(f"pip error:\n{err}"),
                    ))
                    return

                self.after(0, lambda: (
                    self._srv_lbl.config(text="Starting…", fg=YELLOW),
                    self._log("Packages OK — launching server…"),
                ))

                import tempfile as _tmp
                err_file = _tmp.NamedTemporaryFile(
                    mode="w", suffix=".log", delete=False
                )
                err_path = err_file.name
                err_file.close()

                log_fh = open(err_path, "w")
                try:
                    proc = subprocess.Popen(
                        [py, str(dashboard)],
                        cwd=str(dashboard.parent),
                        stdout=log_fh,
                        stderr=log_fh,
                        creationflags=cf,
                    )
                finally:
                    log_fh.close()

                self._server_proc = proc
                self.after(0, lambda: self._btn_srv_stop.config(state="normal"))

                time.sleep(3)
                if proc.poll() is None:
                    self.after(0, lambda: (
                        self._srv_lbl.config(text="Server running", fg=GREEN),
                        self._btn_run.config(state="normal"),
                        self._log("Server is up! Click Run All Groups to start."),
                    ))
                else:
                    try:
                        errtxt = Path(err_path).read_text(errors="replace").strip()[-1200:]
                    except Exception:
                        errtxt = "(no output captured)"
                    self.after(0, lambda et=errtxt: (
                        self._srv_lbl.config(text="Server crashed", fg=RED),
                        self._btn_srv_start.config(state="normal"),
                        self._btn_srv_stop.config(state="disabled"),
                        self._log(f"Server crashed:\n{et}"),
                    ))
            except Exception as exc:
                self.after(0, lambda e=exc: (
                    self._srv_lbl.config(text="Launch error", fg=RED),
                    self._btn_srv_start.config(state="normal"),
                    self._log(f"Launch error: {e}"),
                ))

        threading.Thread(target=_launch, daemon=True).start()

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

                def err_ui():
                    self._log_write(f"❌  {result['error']}")
                    self._btn_run.config(state="normal")
                    self._btn_stop_grp.config(state="disabled")

                self.after(0, err_ui)
            else:
                n = result.get("groups", len(state["groups"]))

                def ok_ui():
                    self._log_write(f"✅  {n} group(s) running in parallel!")
                    self._run_lbl.config(text=f"{n} running", fg=GREEN)

                self.after(0, ok_ui)

        threading.Thread(target=go, daemon=True).start()

    def _stop_groups(self):
        def go():
            self._api("/api/groups/stop", {})

            def done_ui():
                self._log_write("All groups stopped.")
                self._run_lbl.config(text="", fg=T2)
                self._btn_run.config(state="normal")
                self._btn_stop_grp.config(state="disabled")

            self.after(0, done_ui)

        threading.Thread(target=go, daemon=True).start()

    def _start_schedule(self):
        """Start the daily schedule on all booted phones."""
        hits = self._sched_hits_var.get()
        phones = state.get("phones", [])
        if not phones:
            messagebox.showwarning("No phones running",
                                   "Boot phones on Connect & boot first.")
            return
        serials = [p["serial"] for p in phones]
        _groups = state.get("groups") or []
        first_phones = (_groups[0].get("phones") if _groups else None) or {}
        steps = next(iter(first_phones.values()), {}).get("steps", []) if first_phones else []
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
    """Phase rail: five milestones across nine screens (see PAGE_PHASE)."""

    PHASE_LABELS = ("Setup", "Devices", "Automate", "Ship", "Launch")
    # One phase index per page — aligns header rail with PAGE_NAMES / PAGES order.
    PAGE_PHASE = (0, 0, 0, 1, 1, 2, 2, 3, 4)

    PAGES = [
        WelcomePage,
        PrerequisitesPage,
        AndroidStudioPage,
        PhoneFarmPage,
        BootPage,
        AutomationPage,
        GroupsPage,
        PlayStorePage,
        LaunchPage,
    ]
    PAGE_NAMES = [
        "Welcome",
        "Install tools",
        "Android runtime",
        "Create phones",
        "Connect & boot",
        "Automation",
        "Groups",
        "Play Console",
        "Launch",
    ]

    def __init__(self):
        super().__init__()
        _style_scrollbars(self)
        self.title("CPharm Phone Farm Setup")
        self.geometry("820x880")
        self.minsize(760, 700)
        self.config(bg=BG)
        self.resizable(True, True)

        self._hdr_icon_refs: list[tk.PhotoImage] = []
        self._phase_diamonds: list[tk.Label] = []
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
            vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview,
                                style=CPharm_TSCROLL)
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
            _cpharm_install_root(),
            Path.home() / "CPharm",
            Path("C:/CPharm"),
        ]
        for g in guesses:
            if (g / "automation" / "dashboard.py").exists():
                state["cpharm_dir"] = str(g)
                break

    def _build_header(self):
        hdr = tk.Frame(self, bg=BG2, height=68, highlightthickness=1,
                       highlightbackground=BORDER_STRONG)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        title_fr = tk.Frame(hdr, bg=BG2)
        title_fr.pack(side="left", padx=SP["lg"], pady=SP["sm"])
        tk.Label(title_fr, text="CPharm",
                 font=("Segoe UI", 15, "bold"),
                 bg=BG2, fg=T1).pack(anchor="w")
        tk.Label(title_fr, text="Phone farm setup",
                 font=FONT_CAPTION, bg=BG2, fg=T3).pack(anchor="w")

        chev = load_icon("chevron_right", self)
        m_on = load_icon("milestone_on", self)
        m_off = load_icon("milestone_off", self)
        for img in (chev, m_on, m_off):
            if img:
                self._hdr_icon_refs.append(img)

        phase_row = tk.Frame(hdr, bg=BG2)
        phase_row.pack(side="left", padx=(SP["md"], 0), pady=SP["sm"])
        self._phase_lbls = []
        self._phase_m_on = m_on
        self._phase_m_off = m_off
        for i, label in enumerate(self.PHASE_LABELS):
            if i and chev:
                tk.Label(phase_row, image=chev, bg=BG2).pack(side="left", padx=1)
            elif i:
                tk.Label(phase_row, text="/", font=FONT_CAPTION, bg=BG2, fg=T3).pack(
                    side="left", padx=2)
            if m_off:
                dm = tk.Label(phase_row, image=m_off, bg=BG2)
            else:
                dm = tk.Label(phase_row, text="·", font=FONT_CAPTION, bg=BG2, fg=T3)
            dm.pack(side="left", padx=(0, 2))
            self._phase_diamonds.append(dm)
            lb = tk.Label(
                phase_row, text=label,
                font=(FONT_CAPTION[0], FONT_CAPTION[1], "bold"),
                bg=BG2, fg=T3,
            )
            lb.pack(side="left", padx=(0, 6))
            self._phase_lbls.append(lb)

        self._step_lbl = tk.Label(hdr, text="", font=FS, bg=BG2, fg=T2)
        self._step_lbl.pack(side="right", padx=(0, SP["lg"]))

    def _build_footer(self):
        ftr = tk.Frame(self, bg=BG2, height=56, highlightthickness=1,
                       highlightbackground=BORDER_STRONG)
        ftr.pack(fill="x")
        ftr.pack_propagate(False)
        self._next_btn = tk.Button(ftr, text="Next",
                                   font=("Segoe UI", 11, "bold"),
                                   command=self._next,
                                   padx=SP["lg"], pady=SP["sm"])
        style_primary_button(self._next_btn)
        self._next_btn.pack(side="right", padx=SP["lg"], pady=SP["sm"])
        self._back_btn = tk.Button(ftr, text="Back",
                                   font=("Segoe UI", 11),
                                   command=self._back,
                                   padx=SP["md"], pady=SP["sm"])
        style_secondary_button(self._back_btn)
        self._back_btn.pack(side="right", padx=(0, SP["sm"]), pady=SP["sm"])

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
        pidx = self.PAGE_PHASE[idx]
        for i, lb in enumerate(self._phase_lbls):
            lb.config(
                fg=ACCENT if i == pidx else T3,
                font=(FONT_CAPTION[0], FONT_CAPTION[1], "bold")
                if i == pidx
                else FONT_CAPTION,
            )
        for i, dm in enumerate(self._phase_diamonds):
            if self._phase_m_on and self._phase_m_off:
                dm.config(image=self._phase_m_on if i == pidx else self._phase_m_off)
        total = len(self.PAGES)
        self._step_lbl.config(
            text=f"Step {idx + 1} of {total}  ·  {self.PAGE_NAMES[idx]}")
        self._back_btn.config(state="normal" if idx > 0 else "disabled")
        self._next_btn.config(
            text="Finish" if idx == total - 1 else "Next")
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
