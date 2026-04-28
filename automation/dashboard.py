"""
CPharm Dashboard — ADB-native backend.
Works with any Android device: AVD emulators, BlueStacks, Genymotion, MEmu, NOX, real phones.
"""

import datetime
import asyncio
import base64
import concurrent.futures
import json
import logging
import mimetypes
import re
import socket
import subprocess
import time
import random
import threading
import urllib.parse
from pathlib import Path

from websockets.server import serve

import tor_manager
import teach as teach_mod
import playstore as ps_mod
from config import PORT, WS_PORT, APK_DIR, REC_DIR, EMULATOR_PORTS

logging.basicConfig(level=logging.INFO, format="  %(message)s")
log = logging.getLogger("cpharm")

# Swipe/tap coordinates calibrated for 1280×720 — works for most emulators at default res
_QUICK_ACTIONS = {
    "swipe_up":     ("shell", "input", "swipe", "640", "700", "640", "200", "400"),
    "swipe_down":   ("shell", "input", "swipe", "640", "200", "640", "700", "400"),
    "swipe_left":   ("shell", "input", "swipe", "900", "360", "100", "360", "400"),
    "swipe_right":  ("shell", "input", "swipe", "100", "360", "900", "360", "400"),
    "home":         ("shell", "input", "keyevent", "3"),
    "lock":         ("shell", "input", "keyevent", "26"),
    "wake":         ("shell", "input", "keyevent", "224"),
    "clear_recents":("shell", "input", "keyevent", "187"),
}

_ws_clients: set = set()
_teach_state    = {"state": "idle", "file": None}
_running_groups: dict[str, bool] = {}
_app_cache:      dict[str, dict]  = {}
_app_cache_time: dict[str, float] = {}
APP_CACHE_TTL = 30.0
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=32)

SCRIPT_DIR     = Path(__file__).parent
HTML_FILE      = SCRIPT_DIR / "dashboard.html"
PLAYSTORE_FILE = SCRIPT_DIR / "playstore.html"


# ── ADB helpers ──────────────────────────────────────────────────────────�[...]

def _adb(serial: str, *args, timeout: int = 15) -> str:
    try:
        return subprocess.run(
            ["adb", "-s", serial, *args],
            capture_output=True, text=True, timeout=timeout
        ).stdout.strip()
    except Exception as e:
        log.warning("adb error [%s]: %s", serial, e)
        return ""



def _adb_global(*args, timeout: int = 10) -> str:
    try:
        return subprocess.run(
            ["adb", *args],
            capture_output=True, text=True, timeout=timeout
        ).stdout.strip()
    except Exception as e:
        log.warning("adb error: %s", e)
        return ""


def auto_connect_emulators():
    """On startup, try connecting to well-known emulator ADB ports."""
    for host, port in EMULATOR_PORTS:
        try:
            s = socket.socket()
            s.settimeout(0.3)
            reachable = s.connect_ex((host, port)) == 0
            s.close()
        except Exception:
            reachable = False
        if reachable:
            _adb_global("connect", f"{host}:{port}", timeout=5)


def list_phones() -> list[dict]:
    """Return all ADB-connected Android devices, real or emulated."""
    raw = _adb_global("devices", "-l")
    phones = []
    for line in raw.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial = parts[0]
        status = parts[1]
        running = (status == "device")

        # Parse model name from -l output (model:Pixel_6 → Pixel 6)
        name = serial
        for part in parts[2:]:
            if part.startswith("model:"):
                name = part.split(":", 1)[1].replace("_", " ")
                break

        # Detect device type for display
        if serial.startswith("emulator-"):
            dtype = "emulator"
        elif re.match(r"^[\d.]+:\d+$", serial):
            dtype = "wifi"
        else:
            dtype = "real"

        phones.append({
            "serial":  serial,
            "name":    name,
            "running": running,
            "type":    dtype,
            "app":     _get_installed_app(serial) if running else {"package": "", "version": ""},
        })
    return phones


def _get_installed_app(serial: str) -> dict:
    now = time.time()
    if serial in _app_cache and now - _app_cache_time.get(serial, 0) < APP_CACHE_TTL:
        return _app_cache[serial]

    result = {"package": "", "version": ""}
    try:
        raw  = _adb(serial, "shell", "pm", "list", "packages", "-3")
        pkgs = [line.strip().replace("package:", "") for line in raw.splitlines() if line.strip()]
        if pkgs:
            pkg     = pkgs[0]
            ver_raw = _adb(serial, "shell", "dumpsys", "package", pkg)
            version = "?"
            for line in ver_raw.splitlines():
                if "versionName" in line:
                    version = line.strip().split("=")[-1]
                    break
            result = {"package": pkg, "version": version}
    except Exception:
        pass

    _app_cache[serial]      = result
    _app_cache_time[serial] = now
    return result


def invalidate_cache(serial: str):
    _app_cache.pop(serial, None)
    _app_cache_time.pop(serial, None)


def _stop_device(serial: str):
    """Stop/disconnect a device. Kills emulators, disconnects WiFi devices."""
    if serial.startswith("emulator-"):
        _adb(serial, "emu", "kill")
    elif re.match(r"^[\d.]+:\d+$", serial):
        _adb_global("disconnect", serial)
    # Real USB devices: can't be stopped remotely


def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "unknown"




def _phone_idx_from_serial(serial: str) -> int:
    """Derive phone index from serial.
    AVD:  emulator-5554 -> 0, emulator-5556 -> 1, ...
    MuMu: 127.0.0.1:16384 -> 0, 127.0.0.1:16416 -> 1, ...
    USB/other: 0
    """
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

def _get_resources() -> dict:
    try:
        import psutil
        mem = psutil.virtual_memory()
        max_phones = max(0, int((mem.available / 1024**3 - 2.0) / 1.5))
        return {
            "ram_used_pct":          mem.percent,
            "ram_used_gb":           round(mem.used       / 1024**3, 1),
            "ram_free_gb":           round(mem.available  / 1024**3, 1),
            "ram_total_gb":          round(mem.total      / 1024**3, 1),
            "max_phones_recommended": max_phones,
        }
    except ImportError:
        return {
            "ram_used_pct": 0, "ram_used_gb": 0,
            "ram_free_gb": 0, "ram_total_gb": 0,
            "max_phones_recommended": 4,
        }


# ── WebSocket broadcast ───────────────────────────────────────────────────────

async def broadcast(msg: dict):
    if not _ws_clients:
        return
    data = json.dumps(msg)
    clients = set(_ws_clients)
    await asyncio.gather(*[ws.send(data) for ws in clients], return_exceptions=True)


async def push_phones():
    await broadcast({"type": "phones_update", "phones": list_phones()})


# ── HTTP helpers ─────────────────────────────────────────────────────────��[...]

def _http(code: int, ctype: str, body: bytes, extra: str = "") -> bytes:
    status = {200: "OK", 204: "No Content", 400: "Bad Request", 404: "Not Found"}.get(code, "OK")
    headers = (
        f"HTTP/1.1 {code} {status}\r\n"
        f"Content-Type: {ctype}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Access-Control-Allow-Origin: *\r\n"
        "Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n"
        + extra + "\r\n"
    )
    return headers.encode() + body


def json_ok(data: dict, code: int = 200) -> bytes:
    return _http(code, "application/json", json.dumps(data).encode())


def json_err(msg: str, code: int = 400) -> bytes:
    return _http(code, "application/json", json.dumps({"ok": False, "error": msg}).encode(), "")


def html_resp(html: str) -> bytes:
    body = html.encode("utf-8")
    return _http(200, "text/html; charset=utf-8", body)


def file_resp(fpath: Path) -> bytes:
    if not fpath.exists():
        return _http(404, "text/plain", b"Not Found")
    mime, _ = mimetypes.guess_type(str(fpath))
    return _http(200, mime or "application/octet-stream", fpath.read_bytes(),
                 "Cache-Control: max-age=3600\r\n")


def cors_ok() -> bytes:
    return _http(204, "text/plain", b"")


def _load_html() -> str:
    if HTML_FILE.exists():
        return HTML_FILE.read_text(encoding="utf-8")
    return "<h1 style='color:#00e676;font-family:monospace'>dashboard.html not found</h1>"


# ── HTTP request handler ──────────────────────────────────────────────────────

async def handle_http(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
        header_bytes = b""
        while b"\r\n\r\n" not in header_bytes:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=5)
            if not chunk:
                break
            header_bytes += chunk

        header_text, _, body_start = header_bytes.partition(b"\r\n\r\n")
        lines = header_text.decode("utf-8", errors="replace").splitlines()
        if not lines:
            writer.close()
            return

        method, path_qs, *_ = lines[0].split(" ", 2)
        path = urllib.parse.urlparse(path_qs).path

        content_length = 0
        for line in lines[1:]:
            if line.lower().startswith("content-length:"):
                try:
                    content_length = int(line.split(":", 1)[1].strip())
                except (ValueError, IndexError):
                    pass
                break

        if content_length > 50_000_000:
            writer.close()
            return
        body_bytes = body_start
        remaining  = content_length - len(body_start)
        while remaining > 0:
            chunk = await asyncio.wait_for(reader.read(min(remaining, 65536)), timeout=10)
            if not chunk:
                break
            body_bytes += chunk
            remaining  -= len(chunk)

        if method == "GET":
            if path.startswith("/api/scheduler"):
                response = await handle_scheduler(path, body_bytes)
            else:
                response = await handle_get(path)
        elif method == "POST":
            response = await handle_post(path, body_bytes)
        elif method == "OPTIONS":
            response = cors_ok()
        else:
            response = _http(404, "text/plain", b"Not Found")

        writer.write(response)
        await writer.drain()

    except Exception as e:
        log.warning("HTTP handler error: %s", e)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def handle_get(path: str) -> bytes:
    static = {
        "/":              lambda: html_resp(_load_html()),
        "/index.html":    lambda: html_resp(_load_html()),
        "/manifest.json": lambda: file_resp(SCRIPT_DIR / "manifest.json"),
        "/sw.js":         lambda: file_resp(SCRIPT_DIR / "sw.js"),
        "/icon-192.png":  lambda: file_resp(SCRIPT_DIR / "icon-192.png"),
        "/icon-512.png":  lambda: file_resp(SCRIPT_DIR / "icon-512.png"),
    }
    if path in static:
        return static[path]()
    if path == "/playstore":
        if PLAYSTORE_FILE.exists():
            return html_resp(PLAYSTORE_FILE.read_text(encoding="utf-8"))
        return _http(404, "text/plain", b"playstore.html not found")
    if path == "/api/phones":
        return json_ok(list_phones())
    if path == "/api/ip":
        ip = get_local_ip()
        return json_ok({"ip": ip, "url": f"http://{ip}:{PORT}"})
    if path == "/api/apks":
        APK_DIR.mkdir(exist_ok=True)
        files = [{"name": f.name, "size_mb": round(f.stat().st_size / 1024 / 1024, 1)}
                 for f in sorted(APK_DIR.glob("*.apk"))]
        return json_ok(files)
    if path == "/api/recordings":
        return json_ok(teach_mod.list_recordings())
    if path == "/api/resources":
        return json_ok(_get_resources())
    return _http(404, "text/plain", b"Not Found")


async def handle_post(path: str, body: bytes) -> bytes:
    data = {}
    if body:
        try:
            data = json.loads(body)
        except Exception:
            pass

    # ── Connect / disconnect device ──
    if path == "/api/devices/connect":
        addr = data.get("address", "").strip()
        if not addr:
            return json_err("no address provided")
        if ":" not in addr:
            addr = addr + ":5555"
        # Allow IPv4, localhost, or simple hostnames with a port
        if not re.match(r"^[a-zA-Z0-9._-]+:\\d{2,5}$", addr):
            return json_err("invalid address")
        out = _adb_global("connect", addr, timeout=10)
        await push_phones()
        return json_ok({"ok": True, "result": out})

    if path == "/api/devices/disconnect":
        serial = data.get("serial", "").strip()
        if not serial or not re.match(r"^[a-zA-Z0-9._:\\-]+$", serial):
            return json_err("invalid serial")
        _adb_global("disconnect", serial, timeout=5)
        await push_phones()
        return json_ok({"ok": True})

    if path == "/api/devices/refresh":
        auto_connect_emulators()
        await push_phones()
        return json_ok({"ok": True})

    # ── APK upload ──
    if path == "/api/upload":
        filename = data.get("name", "").strip()
        file_b64 = data.get("data", "")
        if not filename or not file_b64:
            return json_err("missing name or data")
        safe_name = Path(filename).name
        if not re.match(r'^[A-Za-z0-9_\-. ]+\.apk$', safe_name) or not safe_name:
            return json_err("only .apk files with safe names allowed")
        if len(file_b64) > 200_000_000:
            return json_err("file too large")
        APK_DIR.mkdir(exist_ok=True)
        dest = APK_DIR / safe_name
        try:
            dest.write_bytes(base64.b64decode(file_b64))
        except Exception:
            return json_err("invalid base64 data")
        return json_ok({"ok": True, "name": safe_name})

    # ── APK install ──
    if path == "/api/install":
        apk_name = data.get("apk", "").strip()
        if not apk_name:
            return json_err("no apk specified")
        apk_path = (APK_DIR / Path(apk_name).name).resolve()
        if not str(apk_path).startswith(str(APK_DIR.resolve())):
            return json_err("invalid path")
        if not apk_path.exists():
            return json_err("file not found")
        phones = [p for p in list_phones() if p["running"]]

        async def install_all():
            loop = asyncio.get_running_loop()
            for p in phones:
                serial = p["serial"]
                await broadcast({"type": "install_progress",
                                 "serial": serial, "status": "installing"})
                await loop.run_in_executor(
                    None,
                    lambda s=serial, ap=str(apk_path): subprocess.run(
                        ["adb", "-s", s, "install", "-r", ap],
                        capture_output=True, timeout=120,
                    )
                )
                invalidate_cache(serial)
                await broadcast({"type": "install_progress",
                                 "serial": serial, "status": "done"})
            await push_phones()

        asyncio.create_task(install_all())
        return json_ok({"ok": True})

    # ── Phone stop (universal) ──
    if path == "/api/phone/stop":
        serial = data.get("serial", "").strip()
        if not serial:
            return json_err("no serial")
        _executor.submit(_stop_device, serial)
        await asyncio.sleep(1)
        await push_phones()
        return json_ok({"ok": True})

    # ── Stop all running devices ──
    if path == "/api/stop_all":
        phones = [p for p in list_phones() if p["running"]]
        def stop_all():
            for p in phones:
                _stop_device(p["serial"])
        _executor.submit(stop_all)
        await asyncio.sleep(1.5)
        await push_phones()
        return json_ok({"ok": True})

    # ── Refresh / re-scan ──
    if path == "/api/start_all":
        auto_connect_emulators()
        await push_phones()
        return json_ok({"ok": True})

    # ── URL launcher ──
    if path == "/api/open_url":
        url         = data.get("url", "").strip()
        auto_rotate = bool(data.get("auto_rotate", False))
        try:
            stagger    = min(int(data.get("stagger_secs", 0)), 3600)
            dwell_secs = min(int(data.get("dwell_secs", 30)), 3600)
        except (TypeError, ValueError):
            return json_err("stagger_secs and dwell_secs must be integers")
        try:
            _parsed = urllib.parse.urlparse(url)
            if _parsed.scheme not in ("http", "https") or not _parsed.netloc or " " in url:
                raise ValueError
        except Exception:
            return json_err("URL must be a valid http:// or https:// URL")
        phones = [p for p in list_phones() if p["running"]]
        loop   = asyncio.get_running_loop()

        def _rotate_after(p: dict, idx: int, delay: int):
            time.sleep(delay)
            _adb(p["serial"], "shell", "am", "force-stop", "com.android.browser")
            _adb(p["serial"], "shell", "am", "force-stop", "com.chrome.beta")
            _adb(p["serial"], "shell", "am", "force-stop", "com.android.chrome")
            result = tor_manager.rotate_identity_adb(p["serial"], idx)
            msg = (f"{p['name']}: identity rotated · "
                   f"new Tor circuit={'yes' if result.get('circuit_rotated') else 'no'}")
            asyncio.run_coroutine_threadsafe(broadcast({"type": "log", "msg": msg}), loop)

        async def open_all():
            for i, p in enumerate(phones):
                if i > 0 and stagger > 0:
                    await asyncio.sleep(stagger)
                # Use Chrome package explicitly — avoids browser chooser and
                # first-run ToS screens that swallow the URL on fresh emulators.
                _adb(p["serial"], "shell", "am", "start",
                     "-a", "android.intent.action.VIEW",
                     "-d", url,
                     "-n", "com.android.chrome/com.google.android.apps.chrome.Main",
                     "--ez", "create_new_tab", "true")
                await broadcast({"type": "log", "msg": f"Opened {url} on {p['name']}"})
                if auto_rotate:
                    threading.Thread(
                        target=_rotate_after, args=(p, i, dwell_secs), daemon=True
                    ).start()

        asyncio.create_task(open_all())
        return json_ok({"ok": True})

    # ── Teach mode ──
    if path == "/api/teach/start":
        phones = [p for p in list_phones() if p["running"]]
        if not phones:
            return json_err("no phones running")
        first_serial = phones[0]["serial"]
        _teach_state["state"]  = "recording"
        _teach_state["serial"] = first_serial
        rec_file = teach_mod.start_recording(first_serial)
        _teach_state["file"] = rec_file
        await broadcast({"type": "teach_status", **_teach_state})
        return json_ok({"ok": True})

    # ── Teach stop
