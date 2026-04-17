"""
CPharm v2 Dashboard — asyncio + WebSocket backend.
"""

import asyncio
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
from config import LDPLAYER, PORT, WS_PORT, APK_DIR

logging.basicConfig(level=logging.INFO, format="  %(message)s")
log = logging.getLogger("cpharm")

# Calibrated for LDPlayer 9 default resolution 1280×720
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
_teach_state = {"state": "idle", "file": None}
_app_cache: dict[int, dict] = {}
_app_cache_time: dict[int, float] = {}
APP_CACHE_TTL = 30.0

SCRIPT_DIR       = Path(__file__).parent
HTML_FILE        = SCRIPT_DIR / "dashboard.html"
PLAYSTORE_FILE   = SCRIPT_DIR / "playstore.html"


# ── LDPlayer helpers ──────────────────────────────────────────────────────────

def ld(*args) -> str:
    try:
        r = subprocess.run([LDPLAYER, *args], capture_output=True, text=True, timeout=20)
        return r.stdout.strip()
    except Exception as e:
        log.error("ldconsole error: %s", e)
        return ""


def _adb(idx: int, *args) -> str:
    device = f"emulator-{5554 + idx * 2}"
    try:
        return subprocess.run(
            ["adb", "-s", device, *args],
            capture_output=True, text=True, timeout=15
        ).stdout.strip()
    except Exception as e:
        log.warning("adb error on phone %d: %s", idx, e)
        return ""


def _get_installed_app(idx: int) -> dict:
    now = time.time()
    if idx in _app_cache and now - _app_cache_time.get(idx, 0) < APP_CACHE_TTL:
        return _app_cache[idx]

    result = {"package": "", "version": ""}
    try:
        raw = _adb(idx, "shell", "pm", "list", "packages", "-3")
        pkgs = [l.strip().replace("package:", "") for l in raw.splitlines() if l.strip()]
        if pkgs:
            pkg     = pkgs[0]
            ver_raw = _adb(idx, "shell", "dumpsys", "package", pkg)
            version = "?"
            for line in ver_raw.splitlines():
                if "versionName" in line:
                    version = line.strip().split("=")[-1]
                    break
            result = {"package": pkg, "version": version}
    except Exception:
        pass

    _app_cache[idx]      = result
    _app_cache_time[idx] = now
    return result


def list_phones() -> list[dict]:
    raw    = ld("list2")
    phones = []
    for line in raw.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2 or not parts[0].isdigit():
            continue
        idx, name = int(parts[0]), parts[1]
        if not name.lower().startswith("cpharm"):
            continue
        running = "running" in ld("isrunning", "--index", str(idx)).lower()
        phones.append({
            "index":   idx,
            "name":    name,
            "running": running,
            "app":     _get_installed_app(idx),
        })
    return phones


def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "unknown"


def invalidate_app_cache(idx: int):
    _app_cache.pop(idx, None)
    _app_cache_time.pop(idx, None)


def _get_resources() -> dict:
    try:
        import psutil
        mem = psutil.virtual_memory()
        used_gb  = round(mem.used  / 1024**3, 1)
        free_gb  = round(mem.available / 1024**3, 1)
        total_gb = round(mem.total / 1024**3, 1)
        pct      = mem.percent
        # Each LDPlayer phone uses ~1.5 GB; leave 2 GB for the OS
        max_phones = max(0, int((mem.available / 1024**3 - 2.0) / 1.5))
        return {
            "ram_used_pct": pct,
            "ram_used_gb": used_gb,
            "ram_free_gb": free_gb,
            "ram_total_gb": total_gb,
            "max_phones_recommended": max_phones,
        }
    except ImportError:
        return {
            "ram_used_pct": 0, "ram_used_gb": 0,
            "ram_free_gb": 0, "ram_total_gb": 0,
            "max_phones_recommended": 4,
            "note": "Install psutil for live RAM stats",
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


# ── HTTP handler ──────────────────────────────────────────────────────────────

async def handle_http(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
        # Read headers first
        header_bytes = b""
        while b"\r\n\r\n" not in header_bytes:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=5)
            if not chunk:
                break
            header_bytes += chunk

        header_text, _, body_start = header_bytes.partition(b"\r\n\r\n")
        lines  = header_text.decode("utf-8", errors="replace").splitlines()
        if not lines:
            writer.close()
            return

        method, path_qs, *_ = lines[0].split(" ", 2)
        path = urllib.parse.urlparse(path_qs).path

        # Read body up to Content-Length
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
    if path == "/api/resources":
        return json_ok(_get_resources())
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
    return _http(404, "text/plain", b"Not Found")


async def handle_post(path: str, body: bytes) -> bytes:
    # ── JSON body ──
    data = {}
    content_type = ""
    if body:
        try:
            data = json.loads(body)
        except Exception:
            pass

    # ── APK upload ──
    if path == "/api/upload":
        filename = data.get("name", "").strip()
        file_b64 = data.get("data", "")
        if not filename or not file_b64:
            return json_err("missing name or data")
        safe_name = Path(filename).name
        if not safe_name.lower().endswith(".apk"):
            return json_err("only .apk files allowed")
        import base64
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
        phones = list_phones()

        async def install_all():
            for p in phones:
                await broadcast({"type": "install_progress",
                                 "phone_idx": p["index"], "status": "installing"})
                device = f"emulator-{5554 + p['index'] * 2}"
                subprocess.run(
                    ["adb", "-s", device, "install", "-r", str(apk_path)],
                    capture_output=True, timeout=120
                )
                invalidate_app_cache(p["index"])
                await broadcast({"type": "install_progress",
                                 "phone_idx": p["index"], "status": "done"})
            await push_phones()

        asyncio.create_task(install_all())
        return json_ok({"ok": True})

    # ── Phone control ──
    if path == "/api/start_all":
        for p in list_phones():
            if not p["running"]:
                ld("launch", "--index", str(p["index"]))
        await push_phones()
        return json_ok({"ok": True})

    if path == "/api/stop_all":
        for p in list_phones():
            if p["running"]:
                ld("quit", "--index", str(p["index"]))
        await push_phones()
        return json_ok({"ok": True})

    if path == "/api/clone":
        phones   = list_phones()
        new_name = f"CPharm-{len(phones) + 1}"
        ld("copy", "--name", new_name, "--from", "0")
        await push_phones()
        return json_ok({"ok": True, "name": new_name})

    start_idx = path.removeprefix("/api/phone/start/")
    if start_idx != path and start_idx.isdigit():
        ld("launch", "--index", start_idx)
        await push_phones()
        return json_ok({"ok": True})

    stop_idx = path.removeprefix("/api/phone/stop/")
    if stop_idx != path and stop_idx.isdigit():
        ld("quit", "--index", stop_idx)
        await push_phones()
        return json_ok({"ok": True})

    # ── URL launcher ──
    if path == "/api/open_url":
        url     = data.get("url", "").strip()
        stagger = int(data.get("stagger_secs", 0))
        if not url.startswith(("http://", "https://")):
            return json_err("URL must start with http:// or https://")
        phones = [p for p in list_phones() if p["running"]]

        async def open_all():
            for i, p in enumerate(phones):
                if i > 0 and stagger > 0:
                    await asyncio.sleep(stagger)
                _adb(p["index"], "shell", "am", "start",
                     "-a", "android.intent.action.VIEW", "-d", url)
                await broadcast({"type": "log", "msg": f"Opened on {p['name']}"})

        asyncio.create_task(open_all())
        return json_ok({"ok": True})

    # ── Teach mode ──
    if path == "/api/teach/start":
        _teach_state["state"] = "recording"
        rec_file = teach_mod.start_recording(1)
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
        phones = [p for p in list_phones() if p["index"] != 1]
        _teach_state["state"] = "playing"
        await broadcast({"type": "teach_status", **_teach_state})

        loop = asyncio.get_event_loop()

        def on_done():
            _teach_state["state"] = "idle"
            asyncio.run_coroutine_threadsafe(
                broadcast({"type": "teach_status", **_teach_state}), loop
            )
            asyncio.run_coroutine_threadsafe(
                broadcast({"type": "log", "msg": "Teach Mode playback complete"}), loop
            )

        teach_mod.replay_all(phones, rec, delay_secs=delay, on_complete=on_done)
        return json_ok({"ok": True})

    # ── Tor setup ──
    if path == "/api/proxy/setup":
        phones = list_phones()
        loop   = asyncio.get_event_loop()

        def setup():
            for p in phones:
                socks_port = tor_manager.start_tor_for_phone(p["index"])
                identity   = tor_manager.apply_identity(p["index"])
                ok = tor_manager.wait_for_tor(p["index"], timeout=30)
                if ok:
                    tor_manager.apply_proxy(p["index"])
                msg = (f"{p['name']}: ready · port {socks_port} · "
                       f"{'connected' if ok else 'timeout'}")
                asyncio.run_coroutine_threadsafe(
                    broadcast({"type": "log", "msg": msg}), loop
                )
            asyncio.run_coroutine_threadsafe(
                broadcast({"type": "log", "msg": "All phones now look different!"}), loop
            )

        threading.Thread(target=setup, daemon=True).start()
        return json_ok({"ok": True})

    if path == "/api/proxy/teardown":
        tor_manager.stop_all()
        await broadcast({"type": "log", "msg": "Tor turned off — phones using normal connection"})
        return json_ok({"ok": True})

    # ── Play Store tester ──
    if path == "/api/playstore/run":
        import re
        package    = data.get("package", "").strip()
        query      = data.get("query", "").strip()
        stars      = int(data.get("stars", 0))
        review     = data.get("review", "").strip()
        delay      = int(data.get("delay_secs", 60))
        if package and not re.match(r'^[a-zA-Z][a-zA-Z0-9_.]*$', package):
            return json_err("invalid package name")
        phones     = list_phones()
        loop       = asyncio.get_event_loop()

        def on_ps_log(msg):
            asyncio.run_coroutine_threadsafe(
                broadcast({"type": "log", "msg": msg}), loop
            )

        def on_ps_done():
            asyncio.run_coroutine_threadsafe(
                broadcast({"type": "log", "msg": "Play Store run complete"}), loop
            )
            asyncio.run_coroutine_threadsafe(
                broadcast({"type": "playstore_done"}), loop
            )

        ps_mod.run_full_sequence(
            phones, package, query, stars, review,
            delay_secs=delay, on_log=on_ps_log, on_complete=on_ps_done
        )
        return json_ok({"ok": True})

    # ── App tester endpoints ──
    if path == "/api/launch_app":
        package = data.get("package", "").strip()
        target  = data.get("target", "all")
        stagger = int(data.get("stagger_secs", 0))
        if not package:
            return json_err("no package specified")
        if not re.match(r'^[a-zA-Z][a-zA-Z0-9_.]*$', package):
            return json_err("invalid package name")
        phones  = list_phones()
        targets = [p for p in phones if p["running"] and (target == "all" or str(p["index"]) == target)]
        loop    = asyncio.get_event_loop()

        def do_launch():
            for i, p in enumerate(targets):
                if i > 0 and stagger > 0:
                    time.sleep(stagger)
                _adb(p["index"], "shell", "monkey", "-p", package,
                     "-c", "android.intent.category.LAUNCHER", "1")
                asyncio.run_coroutine_threadsafe(
                    broadcast({"type": "log", "msg": f"{p['name']}: launched {package}"}), loop
                )

        threading.Thread(target=do_launch, daemon=True).start()
        return json_ok({"ok": True})

    if path == "/api/input_text":
        text   = data.get("text", "")
        target = data.get("target", "all")
        safe   = text.replace(" ", "%s").replace("'", "").replace('"', "")
        phones  = list_phones()
        targets = [p for p in phones if p["running"] and (target == "all" or str(p["index"]) == target)]
        loop    = asyncio.get_event_loop()

        def do_type():
            for p in targets:
                _adb(p["index"], "shell", "input", "text", safe)
            asyncio.run_coroutine_threadsafe(
                broadcast({"type": "log", "msg": f"Typed on {len(targets)} phone(s)"}), loop
            )

        threading.Thread(target=do_type, daemon=True).start()
        return json_ok({"ok": True})

    if path == "/api/input_key":
        try:
            keycode = int(data.get("keycode", 66))
        except (TypeError, ValueError):
            return json_err("invalid keycode")
        if not (0 <= keycode <= 300):
            return json_err("keycode out of range")
        keycode = str(keycode)
        target  = data.get("target", "all")
        phones  = list_phones()
        targets = [p for p in phones if p["running"] and (target == "all" or str(p["index"]) == target)]
        for p in targets:
            _adb(p["index"], "shell", "input", "keyevent", keycode)
        return json_ok({"ok": True})

    if path == "/api/quick_action":
        action = data.get("action", "")
        target = data.get("target", "all")
        if action not in _QUICK_ACTIONS and action != "screenshot":
            return json_err("unknown action")
        phones  = list_phones()
        targets = [p for p in phones if p["running"] and (target == "all" or str(p["index"]) == target)]
        loop    = asyncio.get_event_loop()

        def do_action():
            cmd = _QUICK_ACTIONS.get(action)
            if action == "screenshot":
                for p in targets:
                    out_path = f"/sdcard/cpharm_ss_{p['index']}_{int(time.time())}.png"
                    _adb(p["index"], "shell", "screencap", "-p", out_path)
                    asyncio.run_coroutine_threadsafe(
                        broadcast({"type": "log", "msg": f"{p['name']}: screenshot saved to {out_path}"}), loop
                    )
            elif cmd:
                for p in targets:
                    _adb(p["index"], *cmd)

        threading.Thread(target=do_action, daemon=True).start()
        return json_ok({"ok": True})

    if path == "/api/playstore/open":
        package = data.get("package", "").strip()
        if not package or not re.match(r'^[a-zA-Z][a-zA-Z0-9_.]*$', package):
            return json_err("invalid package name")
        phones  = [p for p in list_phones() if p["running"]]
        loop    = asyncio.get_event_loop()
        def _open():
            for p in phones:
                ps_mod.open_store_page(p["index"], package)
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
    if not os.path.exists(LDPLAYER):
        print("\n  LDPlayer is not installed.")
        print("  Download it free from  ldplayer.net  then run this again.\n")
        input("Press Enter to close...")
        raise SystemExit(1)

    APK_DIR.mkdir(exist_ok=True)
    ip = get_local_ip()

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
