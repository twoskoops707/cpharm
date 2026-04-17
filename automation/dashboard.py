"""
CPharm v2 Dashboard — asyncio + WebSocket backend.
Serves dashboard.html and handles all phone farm control.
"""

import asyncio
import json
import mimetypes
import os
import socket
import subprocess
import tempfile
import time
import threading
import urllib.parse
from pathlib import Path

import websockets
from websockets.server import serve

import tor_manager
import teach as teach_mod

LDPLAYER   = r"C:\LDPlayer\LDPlayer9\ldconsole.exe"
PORT       = 8080
SCRIPT_DIR = Path(__file__).parent
APK_DIR    = SCRIPT_DIR.parent / "apks"
HTML_FILE  = SCRIPT_DIR / "dashboard.html"
RAM_PER_PH = 1.5

_ws_clients: set = set()
_stagger_task: asyncio.Task | None = None
_teach_state = {"state": "idle", "file": None, "current_phone": None}


# ── LDPlayer helpers ──────────────────────────────────────────────────────────

def ld(*args) -> str:
    try:
        r = subprocess.run([LDPLAYER, *args], capture_output=True, text=True, timeout=20)
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
        running = "running" in ld("isrunning", "--index", str(idx)).lower()
        phones.append({
            "index":     idx,
            "name":      name,
            "running":   running,
            "is_cpharm": name.lower().startswith("cpharm"),
            "app":       _get_installed_app(idx),
        })
    return [p for p in phones if p["is_cpharm"]]


def _get_installed_app(idx: int) -> dict:
    try:
        raw = subprocess.run(
            ["adb", "-s", f"emulator-{5554 + idx*2}", "shell",
             "pm", "list", "packages", "-3"],
            capture_output=True, text=True, timeout=8
        ).stdout
        pkgs = [l.strip().replace("package:", "") for l in raw.splitlines() if l.strip()]
        if pkgs:
            pkg = pkgs[0]
            ver_raw = subprocess.run(
                ["adb", "-s", f"emulator-{5554 + idx*2}", "shell",
                 "dumpsys", "package", pkg],
                capture_output=True, text=True, timeout=8
            ).stdout
            for line in ver_raw.splitlines():
                if "versionName" in line:
                    ver = line.strip().split("=")[-1]
                    return {"package": pkg, "version": ver}
            return {"package": pkg, "version": "?"}
    except Exception:
        pass
    return {"package": "", "version": ""}


def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "unknown"


def _adb(idx: int, *args) -> str:
    device = f"emulator-{5554 + idx * 2}"
    try:
        return subprocess.run(
            ["adb", "-s", device, *args],
            capture_output=True, text=True, timeout=15
        ).stdout.strip()
    except Exception as e:
        return f"ERROR: {e}"


# ── WebSocket broadcast ───────────────────────────────────────────────────────

async def broadcast(msg: dict):
    if not _ws_clients:
        return
    data = json.dumps(msg)
    await asyncio.gather(
        *[ws.send(data) for ws in _ws_clients],
        return_exceptions=True
    )


async def push_phones():
    await broadcast({"type": "phones_update", "phones": list_phones()})


# ── HTTP request handler ──────────────────────────────────────────────────────

async def handle_http(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
        raw = await asyncio.wait_for(reader.read(8192), timeout=5)
        request = raw.decode("utf-8", errors="replace")
        lines   = request.splitlines()
        if not lines:
            writer.close()
            return

        method, path_qs, *_ = lines[0].split(" ", 2)
        parsed  = urllib.parse.urlparse(path_qs)
        path    = parsed.path
        body    = ""
        if "\r\n\r\n" in request:
            body = request.split("\r\n\r\n", 1)[1]

        if method == "GET":
            response = await handle_get(path)
        elif method == "POST":
            response = await handle_post(path, body)
        elif method == "OPTIONS":
            response = cors_ok()
        else:
            response = r404()

        writer.write(response)
        await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()


def _http_response(code: int, content_type: str, body: bytes, extra: str = "") -> bytes:
    status = {200: "OK", 204: "No Content", 404: "Not Found", 400: "Bad Request"}.get(code, "OK")
    headers = (
        f"HTTP/1.1 {code} {status}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Access-Control-Allow-Origin: *\r\n"
        f"Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n"
        + extra +
        "\r\n"
    )
    return headers.encode() + body


def json_response(data: dict, code: int = 200) -> bytes:
    body = json.dumps(data).encode()
    return _http_response(code, "application/json", body)


def html_response(html: str) -> bytes:
    body = html.encode("utf-8")
    return _http_response(200, "text/html; charset=utf-8", body)


def file_response(fpath: Path) -> bytes:
    if not fpath.exists():
        return r404()
    mime, _ = mimetypes.guess_type(str(fpath))
    body = fpath.read_bytes()
    return _http_response(200, mime or "application/octet-stream", body,
                          "Cache-Control: max-age=3600\r\n")


def cors_ok() -> bytes:
    return _http_response(204, "text/plain", b"")


def r404() -> bytes:
    return _http_response(404, "text/plain", b"Not Found")


def _load_html() -> str:
    if HTML_FILE.exists():
        return HTML_FILE.read_text(encoding="utf-8")
    return "<h1 style='color:#00e676;font-family:monospace'>dashboard.html not found</h1>"


async def handle_get(path: str) -> bytes:
    routes = {
        "/":              lambda: html_response(_load_html()),
        "/index.html":    lambda: html_response(_load_html()),
        "/manifest.json": lambda: file_response(SCRIPT_DIR / "manifest.json"),
        "/sw.js":         lambda: file_response(SCRIPT_DIR / "sw.js"),
        "/icon-192.png":  lambda: file_response(SCRIPT_DIR / "icon-192.png"),
        "/icon-512.png":  lambda: file_response(SCRIPT_DIR / "icon-512.png"),
    }

    if path in routes:
        return routes[path]()

    if path == "/api/phones":
        return json_response(list_phones())
    if path == "/api/ip":
        ip = get_local_ip()
        return json_response({"ip": ip, "url": f"http://{ip}:{PORT}"})
    if path == "/api/recordings":
        return json_response(teach_mod.list_recordings())
    if path.startswith("/api/ram"):
        try:
            out = subprocess.check_output(
                ["wmic", "OS", "get", "FreePhysicalMemory", "/Value"],
                text=True, timeout=5
            )
            kb  = int([l for l in out.splitlines() if "=" in l][0].split("=")[1])
            return json_response({"free_gb": round(kb / 1024 / 1024, 1)})
        except Exception:
            return json_response({"free_gb": 0})

    return r404()


async def handle_post(path: str, body: str) -> bytes:
    data = {}
    if body:
        try:
            data = json.loads(body)
        except Exception:
            pass

    # ── Phone control ──
    if path == "/api/start_all":
        for p in list_phones():
            if not p["running"]:
                ld("launch", "--index", str(p["index"]))
        await push_phones()
        return json_response({"ok": True})

    if path == "/api/stop_all":
        for p in list_phones():
            if p["running"]:
                ld("quit", "--index", str(p["index"]))
        await push_phones()
        return json_response({"ok": True})

    if path == "/api/clone":
        phones = list_phones()
        new_name = f"CPharm-{len(phones) + 1}"
        ld("copy", "--name", new_name, "--from", "0")
        await push_phones()
        return json_response({"ok": True, "name": new_name})

    start_m = path.removeprefix("/api/phone/start/")
    if start_m != path and start_m.isdigit():
        ld("launch", "--index", start_m)
        await push_phones()
        return json_response({"ok": True})

    stop_m = path.removeprefix("/api/phone/stop/")
    if stop_m != path and stop_m.isdigit():
        ld("quit", "--index", stop_m)
        await push_phones()
        return json_response({"ok": True})

    # ── APK install ──
    if path == "/api/install":
        apk_name = data.get("apk")
        if not apk_name:
            return json_response({"ok": False, "error": "no apk"}, 400)
        apk_path = APK_DIR / apk_name
        if not apk_path.exists():
            return json_response({"ok": False, "error": "file not found"}, 400)

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
                await broadcast({"type": "install_progress",
                                 "phone_idx": p["index"], "status": "done"})
            await push_phones()

        asyncio.create_task(install_all())
        return json_response({"ok": True})

    # ── URL launcher ──
    if path == "/api/open_url":
        url     = data.get("url", "").strip()
        stagger = int(data.get("stagger_secs", 0))
        if not url:
            return json_response({"ok": False, "error": "no url"}, 400)
        phones = [p for p in list_phones() if p["running"]]

        async def open_on_all():
            for i, p in enumerate(phones):
                if i > 0 and stagger > 0:
                    await asyncio.sleep(stagger)
                _adb(p["index"], "shell", "am", "start",
                     "-a", "android.intent.action.VIEW",
                     "-d", url)
                await broadcast({"type": "log",
                                 "msg": f"Opened {url} on {p['name']}"})

        asyncio.create_task(open_on_all())
        return json_response({"ok": True})

    # ── Teach mode ──
    if path == "/api/teach/start":
        _teach_state["state"] = "recording"
        rec_file = teach_mod.start_recording(1)
        _teach_state["file"] = rec_file
        await broadcast({"type": "teach_status", **_teach_state})
        return json_response({"ok": True, "file": rec_file})

    if path == "/api/teach/stop":
        teach_mod.stop_recording()
        _teach_state["state"] = "idle"
        await broadcast({"type": "teach_status", **_teach_state})
        return json_response({"ok": True, "file": _teach_state["file"]})

    if path == "/api/teach/play":
        rec   = data.get("file") or _teach_state.get("file")
        delay = int(data.get("delay_secs", 60))
        if not rec:
            return json_response({"ok": False, "error": "no recording"}, 400)
        phones = [p for p in list_phones() if p["index"] != 1]
        _teach_state["state"] = "playing"
        await broadcast({"type": "teach_status", **_teach_state})

        def play():
            teach_mod.replay_all(phones, rec, delay_secs=delay)

        threading.Thread(target=play, daemon=True).start()
        return json_response({"ok": True})

    # ── Make phones look different (Tor + MAC) ──
    if path == "/api/proxy/setup":
        phones = list_phones()

        async def setup_all():
            await broadcast({"type": "log", "msg": "Setting up unique location per phone..."})
            for p in phones:
                identity = tor_manager.get_identity(p["index"])
                socks_port = tor_manager.start_tor_for_phone(p["index"])
                ld("modify", "--index", str(p["index"]),
                   "--imei", identity["imei"])
                ok = tor_manager.wait_for_tor(p["index"], timeout=30)
                await broadcast({
                    "type": "log",
                    "msg": f"{p['name']}: {'ready' if ok else 'Tor timeout'} · port {socks_port}"
                })
            await broadcast({"type": "log", "msg": "All phones now look different!"})

        asyncio.create_task(setup_all())
        return json_response({"ok": True})

    return r404()


# ── WebSocket handler ─────────────────────────────────────────────────────────

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


# ── Auto-refresh loop ─────────────────────────────────────────────────────────

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
        print(f"\n  [!] LDPlayer not found at {LDPLAYER}")
        print("  [!] Install LDPlayer 9 from ldplayer.net\n")
        input("Press Enter to exit...")
        raise SystemExit(1)

    ip = get_local_ip()
    ws_port = PORT + 1

    http_server = await asyncio.start_server(handle_http, "0.0.0.0", PORT)
    ws_server   = await serve(ws_handler, "0.0.0.0", ws_port)

    print(f"\n  ╔══════════════════════════════════════════╗")
    print(f"  ║   CPharm v2  ·  ready                    ║")
    print(f"  ║   PC:    http://localhost:{PORT}           ║")
    print(f"  ║   Phone: http://{ip}:{PORT}       ║")
    print(f"  ╚══════════════════════════════════════════╝\n")

    async with http_server, ws_server:
        await asyncio.gather(
            http_server.serve_forever(),
            ws_server.wait_closed(),
            auto_refresh(),
        )


if __name__ == "__main__":
    asyncio.run(main())
