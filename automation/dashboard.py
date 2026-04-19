"""
CPharm Dashboard — ADB-native backend.
Works with any Android device: AVD emulators, BlueStacks, Genymotion, MEmu, NOX, real phones.
"""

import asyncio
import base64
import json
import logging
import mimetypes
import os
import re
import socket
import subprocess
import sys
import time
import threading
import urllib.parse
from pathlib import Path

import websockets
from websockets.server import serve

import tor_manager
import teach as teach_mod
import playstore as ps_mod
from config import PORT, WS_PORT, APK_DIR, EMULATOR_PORTS

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

SCRIPT_DIR     = Path(__file__).parent
HTML_FILE      = SCRIPT_DIR / "dashboard.html"
PLAYSTORE_FILE = SCRIPT_DIR / "playstore.html"


# ── ADB helpers ───────────────────────────────────────────────────────────────

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
        pkgs = [l.strip().replace("package:", "") for l in raw.splitlines() if l.strip()]
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
    await asyncio.gather(*[ws.send(data) for ws in _ws_clients], return_exceptions=True)


async def push_phones():
    await broadcast({"type": "phones_update", "phones": list_phones()})


# ── HTTP helpers ──────────────────────────────────────────────────────────────

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
                content_length = int(line.split(":", 1)[1].strip())
                break

        body_bytes = body_start
        remaining  = content_length - len(body_start)
        while remaining > 0:
            chunk = await asyncio.wait_for(reader.read(min(remaining, 65536)), timeout=10)
            if not chunk:
                break
            body_bytes += chunk
            remaining  -= len(chunk)

        if method == "GET":
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
        if not re.match(r"^[a-zA-Z0-9._-]+:\d{2,5}$", addr):
            return json_err("invalid address")
        out = _adb_global("connect", addr, timeout=10)
        await push_phones()
        return json_ok({"ok": True, "result": out})

    if path == "/api/devices/disconnect":
        serial = data.get("serial", "").strip()
        if not serial or not re.match(r"^[a-zA-Z0-9._:\-]+$", serial):
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
        if not safe_name.lower().endswith(".apk"):
            return json_err("only .apk files allowed")
        APK_DIR.mkdir(exist_ok=True)
        dest = APK_DIR / safe_name
        dest.write_bytes(base64.b64decode(file_b64))
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
            for p in phones:
                serial = p["serial"]
                await broadcast({"type": "install_progress",
                                 "serial": serial, "status": "installing"})
                subprocess.run(
                    ["adb", "-s", serial, "install", "-r", str(apk_path)],
                    capture_output=True, timeout=120
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
        loop = asyncio.get_event_loop()
        threading.Thread(target=lambda: _stop_device(serial), daemon=True).start()
        await asyncio.sleep(1)
        await push_phones()
        return json_ok({"ok": True})

    # ── Stop all running devices ──
    if path == "/api/stop_all":
        phones = [p for p in list_phones() if p["running"]]
        loop = asyncio.get_event_loop()
        def stop_all():
            for p in phones:
                _stop_device(p["serial"])
        threading.Thread(target=stop_all, daemon=True).start()
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
        url          = data.get("url", "").strip()
        stagger      = int(data.get("stagger_secs", 0))
        auto_rotate  = bool(data.get("auto_rotate", False))
        dwell_secs   = int(data.get("dwell_secs", 30))
        if not url.startswith(("http://", "https://")):
            return json_err("URL must start with http:// or https://")
        phones = [p for p in list_phones() if p["running"]]
        loop   = asyncio.get_event_loop()

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

    if path == "/api/teach/stop":
        teach_mod.stop_recording()
        _teach_state["state"] = "idle"
        await broadcast({"type": "teach_status", **_teach_state})
        return json_ok({"ok": True})

    if path == "/api/teach/play":
        rec   = data.get("file") or _teach_state.get("file")
        delay = int(data.get("delay_secs", 60))
        if not rec:
            return json_err("no recording found")
        source_serial = _teach_state.get("serial", "")
        phones = [p for p in list_phones() if p["running"] and p["serial"] != source_serial]
        _teach_state["state"] = "playing"
        await broadcast({"type": "teach_status", **_teach_state})
        loop = asyncio.get_event_loop()

        def on_done():
            _teach_state["state"] = "idle"
            asyncio.run_coroutine_threadsafe(broadcast({"type": "teach_status", **_teach_state}), loop)
            asyncio.run_coroutine_threadsafe(broadcast({"type": "log", "msg": "Teach Mode playback complete"}), loop)

        teach_mod.replay_all(phones, rec, delay_secs=delay, on_complete=on_done)
        return json_ok({"ok": True})

    # ── Tor / identity ──
    if path == "/api/proxy/setup":
        phones = [p for p in list_phones() if p["running"]]
        loop   = asyncio.get_event_loop()

        def setup():
            for i, p in enumerate(phones):
                socks_port = tor_manager.start_tor_for_phone(i)
                tor_manager.apply_identity_adb(p["serial"], i)
                ok = tor_manager.wait_for_tor(i, timeout=30)
                msg = f"{p['name']}: {'ready' if ok else 'timeout'} · port {socks_port}"
                asyncio.run_coroutine_threadsafe(broadcast({"type": "log", "msg": msg}), loop)
            asyncio.run_coroutine_threadsafe(
                broadcast({"type": "log", "msg": "All phones now look different!"}), loop
            )

        threading.Thread(target=setup, daemon=True).start()
        return json_ok({"ok": True})

    if path == "/api/proxy/rotate":
        phones = [p for p in list_phones() if p["running"]]
        loop   = asyncio.get_event_loop()

        def rotate_all():
            for i, p in enumerate(phones):
                result = tor_manager.rotate_identity_adb(p["serial"], i)
                msg = (f"{p['name']}: rotated · "
                       f"Tor={'yes' if result.get('circuit_rotated') else 'no'}")
                asyncio.run_coroutine_threadsafe(broadcast({"type": "log", "msg": msg}), loop)
            asyncio.run_coroutine_threadsafe(
                broadcast({"type": "log", "msg": "All phones have new identities!"}), loop
            )

        threading.Thread(target=rotate_all, daemon=True).start()
        return json_ok({"ok": True})

    if path == "/api/proxy/teardown":
        tor_manager.stop_all()
        await broadcast({"type": "log", "msg": "Tor turned off — phones using normal connection"})
        return json_ok({"ok": True})

    # ── Groups: run different sequences on different phone sets in parallel ──
    if path == "/api/groups/run":
        raw_groups = data.get("groups")
        if not raw_groups:
            cfg_path = Path(__file__).parent / "recordings" / "groups_config.json"
            if cfg_path.exists():
                raw_groups = json.loads(cfg_path.read_text()).get("groups", [])
        if not raw_groups:
            return json_err("no groups provided and no saved groups_config.json found")

        all_phones = {p["serial"]: p for p in list_phones() if p["running"]}
        loop = asyncio.get_event_loop()
        _running_groups.clear()

        def _run_steps_adb(steps: list, serial: str, group_name: str):
            for step in steps:
                if not _running_groups.get(group_name, True):
                    break
                t = step.get("type", "")
                if t == "open_url":
                    _adb(serial, "shell", "am", "start",
                         "-a", "android.intent.action.VIEW", "-d", step.get("url", ""))
                elif t == "tap":
                    _adb(serial, "shell", "input", "tap",
                         str(step.get("x", 0)), str(step.get("y", 0)))
                elif t == "wait":
                    deadline = time.time() + int(step.get("seconds", 1))
                    while time.time() < deadline:
                        if not _running_groups.get(group_name, True):
                            break
                        time.sleep(0.25)
                elif t == "swipe":
                    _adb(serial, "shell", "input", "swipe",
                         str(step.get("x1", 0)), str(step.get("y1", 0)),
                         str(step.get("x2", 0)), str(step.get("y2", 0)),
                         str(step.get("ms", 400)))
                elif t == "keyevent":
                    _adb(serial, "shell", "input", "keyevent", step.get("key", "BACK"))
                elif t == "close_app":
                    _adb(serial, "shell", "am", "force-stop",
                         step.get("package", "com.android.chrome"))
                elif t == "clear_cookies":
                    _adb(serial, "shell", "pm", "clear", "com.android.chrome")
                elif t == "type_text":
                    text = step.get("text", "").replace(" ", "%s").replace("'", "")
                    _adb(serial, "shell", "input", "text", text)
                elif t == "rotate_identity":
                    idx = list(all_phones.keys()).index(serial) if serial in all_phones else 0
                    tor_manager.rotate_identity_adb(serial, idx)
                time.sleep(0.3)

        def _run_group(group: dict):
            name    = group.get("name", "Group")
            steps   = group.get("steps", [])
            serials = [s for s in group.get("phones", []) if s in all_phones]
            stagger = int(group.get("stagger_secs", 0))
            repeat  = int(group.get("repeat", 1))
            forever = bool(group.get("repeat_forever", False))

            _running_groups[name] = True
            asyncio.run_coroutine_threadsafe(
                broadcast({"type": "log", "msg": f"[{name}] Starting on {len(serials)} phone(s)"}),
                loop)

            iteration = 0
            while _running_groups.get(name) and (forever or iteration < repeat):
                for i, serial in enumerate(serials):
                    if not _running_groups.get(name):
                        break
                    if i > 0 and stagger > 0:
                        deadline = time.time() + stagger
                        while time.time() < deadline:
                            if not _running_groups.get(name):
                                break
                            time.sleep(0.5)
                    phone_name = all_phones[serial]["name"]
                    asyncio.run_coroutine_threadsafe(
                        broadcast({"type": "log",
                                   "msg": f"[{name}] Running on {phone_name}"}), loop)
                    _run_steps_adb(steps, serial, name)
                iteration += 1

            asyncio.run_coroutine_threadsafe(
                broadcast({"type": "log",
                           "msg": f"[{name}] Done ✓" if _running_groups.get(name)
                                  else f"[{name}] Stopped"}), loop)
            _running_groups.pop(name, None)
            asyncio.run_coroutine_threadsafe(
                broadcast({"type": "groups_status", "running": list(_running_groups.keys())}), loop)

        for group in raw_groups:
            threading.Thread(target=_run_group, args=(group,), daemon=True).start()

        await broadcast({"type": "groups_status", "running": [g.get("name") for g in raw_groups]})
        return json_ok({"ok": True, "groups": len(raw_groups)})

    if path == "/api/groups/stop":
        name = data.get("name", "")
        if name:
            _running_groups[name] = False
            await broadcast({"type": "log", "msg": f"Stopping group: {name}"})
        else:
            for k in list(_running_groups.keys()):
                _running_groups[k] = False
            await broadcast({"type": "log", "msg": "Stopping all groups…"})
        return json_ok({"ok": True})

    if path == "/api/groups/load":
        cfg_path = Path(__file__).parent / "recordings" / "groups_config.json"
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text())
            return json_ok(cfg)
        return json_err("no groups_config.json found — run the Setup Wizard first")

    # ── App tester: launch app ──
    if path == "/api/launch_app":
        package = data.get("package", "").strip()
        target  = data.get("target", "all")
        stagger = int(data.get("stagger_secs", 0))
        if not package:
            return json_err("no package specified")
        if not re.match(r'^[a-zA-Z][a-zA-Z0-9_.]*$', package):
            return json_err("invalid package name")
        phones  = list_phones()
        targets = [p for p in phones if p["running"] and (target == "all" or p["serial"] == target)]
        loop    = asyncio.get_event_loop()

        def do_launch():
            for i, p in enumerate(targets):
                if i > 0 and stagger > 0:
                    time.sleep(stagger)
                _adb(p["serial"], "shell", "monkey", "-p", package,
                     "-c", "android.intent.category.LAUNCHER", "1")
                asyncio.run_coroutine_threadsafe(
                    broadcast({"type": "log", "msg": f"{p['name']}: launched {package}"}), loop
                )

        threading.Thread(target=do_launch, daemon=True).start()
        return json_ok({"ok": True})

    # ── App tester: type text ──
    if path == "/api/input_text":
        text   = data.get("text", "")
        target = data.get("target", "all")
        safe   = text.replace(" ", "%s").replace("'", "").replace('"', "")
        phones  = list_phones()
        targets = [p for p in phones if p["running"] and (target == "all" or p["serial"] == target)]
        loop    = asyncio.get_event_loop()

        def do_type():
            for p in targets:
                _adb(p["serial"], "shell", "input", "text", safe)
            asyncio.run_coroutine_threadsafe(
                broadcast({"type": "log", "msg": f"Typed on {len(targets)} phone(s)"}), loop
            )

        threading.Thread(target=do_type, daemon=True).start()
        return json_ok({"ok": True})

    # ── App tester: key event ──
    if path == "/api/input_key":
        try:
            keycode = int(data.get("keycode", 66))
        except (TypeError, ValueError):
            return json_err("invalid keycode")
        if not (0 <= keycode <= 300):
            return json_err("keycode out of range")
        target  = data.get("target", "all")
        phones  = list_phones()
        targets = [p for p in phones if p["running"] and (target == "all" or p["serial"] == target)]
        for p in targets:
            _adb(p["serial"], "shell", "input", "keyevent", str(keycode))
        return json_ok({"ok": True})

    # ── App tester: quick action ──
    if path == "/api/quick_action":
        action = data.get("action", "")
        target = data.get("target", "all")
        if action not in _QUICK_ACTIONS and action != "screenshot":
            return json_err("unknown action")
        phones  = list_phones()
        targets = [p for p in phones if p["running"] and (target == "all" or p["serial"] == target)]
        loop    = asyncio.get_event_loop()

        def do_action():
            cmd = _QUICK_ACTIONS.get(action)
            if action == "screenshot":
                for p in targets:
                    ts  = int(time.time())
                    out = f"/sdcard/cpharm_{ts}.png"
                    _adb(p["serial"], "shell", "screencap", "-p", out)
                    asyncio.run_coroutine_threadsafe(
                        broadcast({"type": "log", "msg": f"{p['name']}: screenshot → {out}"}), loop
                    )
            elif cmd:
                for p in targets:
                    _adb(p["serial"], *cmd)

        threading.Thread(target=do_action, daemon=True).start()
        return json_ok({"ok": True})

    # ── Playstore run ──
    if path == "/api/playstore/run":
        package = data.get("package", "").strip()
        query   = data.get("query", "").strip()
        stars   = int(data.get("stars", 0))
        review  = data.get("review", "").strip()
        delay   = int(data.get("delay_secs", 60))
        if package and not re.match(r'^[a-zA-Z][a-zA-Z0-9_.]*$', package):
            return json_err("invalid package name")
        phones = list_phones()
        loop   = asyncio.get_event_loop()

        def on_log(msg):
            asyncio.run_coroutine_threadsafe(broadcast({"type": "log", "msg": msg}), loop)

        def on_done():
            asyncio.run_coroutine_threadsafe(broadcast({"type": "log", "msg": "Play Store run complete"}), loop)
            asyncio.run_coroutine_threadsafe(broadcast({"type": "playstore_done"}), loop)

        ps_mod.run_full_sequence(phones, package, query, stars, review,
                                 delay_secs=delay, on_log=on_log, on_complete=on_done)
        return json_ok({"ok": True})

    if path == "/api/playstore/open":
        package = data.get("package", "").strip()
        if not package or not re.match(r'^[a-zA-Z][a-zA-Z0-9_.]*$', package):
            return json_err("invalid package name")
        phones = [p for p in list_phones() if p["running"]]
        loop   = asyncio.get_event_loop()

        def _open():
            for p in phones:
                ps_mod.open_store_page_serial(p["serial"], package)
                asyncio.run_coroutine_threadsafe(
                    broadcast({"type": "log", "msg": f"{p['name']}: opened {package}"}), loop
                )

        threading.Thread(target=_open, daemon=True).start()
        return json_ok({"ok": True})

    return _http(404, "text/plain", b"Not Found")


# ── WebSocket ─────────────────────────────────────────────────────────────────

async def ws_handler(websocket):
    _ws_clients.add(websocket)
    try:
        await push_phones()
        async for _ in websocket:
            pass
    except Exception:
        pass
    finally:
        _ws_clients.discard(websocket)


async def auto_refresh():
    while True:
        await asyncio.sleep(10)
        try:
            await push_phones()
        except Exception:
            pass


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    APK_DIR.mkdir(exist_ok=True)
    ip = get_local_ip()

    # Try to connect to any emulators already running
    log.info("Scanning for connected devices…")
    threading.Thread(target=auto_connect_emulators, daemon=True).start()

    http_server = await asyncio.start_server(handle_http, "0.0.0.0", PORT)
    ws_server   = await serve(ws_handler, "0.0.0.0", WS_PORT)

    print(f"\n  ╔══════════════════════════════════════════╗")
    print(f"  ║   CPharm  •  ready                       ║")
    print(f"  ║                                          ║")
    print(f"  ║   On this PC:  http://localhost:{PORT}    ║")
    print(f"  ║   On phone:    http://{ip}:{PORT}   ║")
    print(f"  ║                                          ║")
    print(f"  ║   Press Ctrl+C to stop                   ║")
    print(f"  ╚══════════════════════════════════════════╝\n")

    async with http_server, ws_server:
        await asyncio.gather(
            http_server.serve_forever(),
            ws_server.wait_closed(),
            auto_refresh(),
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n  CPharm stopped.")
