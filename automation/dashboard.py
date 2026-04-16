#!/usr/bin/env python3
"""CPharm Web Dashboard — control LDPlayer phone farm from any browser."""

import http.server
import json
import os
import socket
import subprocess
import time
import urllib.parse
from pathlib import Path
from socketserver import ThreadingMixIn

LDPLAYER  = r"C:\LDPlayer\LDPlayer9\ldconsole.exe"
PORT      = 8080
SCRIPT_DIR = Path(__file__).parent
APK_DIR   = SCRIPT_DIR.parent / "apks"
HTML_FILE = SCRIPT_DIR / "dashboard.html"

# ── Threading HTTP server (non-blocking per request) ─────────────────────────
class ThreadedHTTPServer(ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

# ── LDPlayer helpers ──────────────────────────────────────────────────────────
def ld(*args: str) -> str:
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
        running_raw = ld("isrunning", "--index", str(idx))
        phones.append({
            "index":     idx,
            "name":      name,
            "running":   "running" in running_raw.lower(),
            "is_cpharm": name.lower().startswith("cpharm"),
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

# ── HTML loader ───────────────────────────────────────────────────────────────
def load_html() -> str:
    if HTML_FILE.exists():
        return HTML_FILE.read_text(encoding="utf-8").replace(
            "const DEMO_MODE = true;", "const DEMO_MODE = false;"
        )
    return "<h1 style='color:#00e676;font-family:monospace'>dashboard.html not found</h1>"

# ── HTTP handler ──────────────────────────────────────────────────────────────
class Handler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"  [{self.address_string()}] {fmt % args}")

    # ── helpers ──
    def send_json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html: str):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, fpath: Path, mime: str):
        try:
            body = fpath.read_bytes()
        except FileNotFoundError:
            self.send_error(404, f"File not found: {fpath.name}")
            return
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=3600")
        self.end_headers()
        self.wfile.write(body)

    def send_404(self):
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ── GET ──
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        routes = {
            "/":             lambda: self.send_html(load_html()),
            "/index.html":  lambda: self.send_html(load_html()),
            "/manifest.json": lambda: self.send_file(SCRIPT_DIR / "manifest.json", "application/manifest+json"),
            "/sw.js":        lambda: self.send_file(SCRIPT_DIR / "sw.js", "application/javascript"),
            "/icon-192.png": lambda: self.send_file(SCRIPT_DIR / "icon-192.png", "image/png"),
            "/icon-512.png": lambda: self.send_file(SCRIPT_DIR / "icon-512.png", "image/png"),
            "/api/phones":   lambda: self.send_json(list_phones()),
            "/api/ip":       lambda: self._api_ip(),
        }
        handler = routes.get(path)
        if handler:
            handler()
        else:
            self.send_404()

    def _api_ip(self):
        ip = get_local_ip()
        self.send_json({"ip": ip, "url": f"http://{ip}:{PORT}"})

    # ── POST ──
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path

        if path.startswith("/api/phone/start/"):
            idx = path.rsplit("/", 1)[-1]
            if idx.isdigit():
                ld("launch", "--index", idx)
                self.send_json({"ok": True})
            else:
                self.send_json({"ok": False, "error": "invalid index"}, 400)

        elif path.startswith("/api/phone/stop/"):
            idx = path.rsplit("/", 1)[-1]
            if idx.isdigit():
                ld("quit", "--index", idx)
                self.send_json({"ok": True})
            else:
                self.send_json({"ok": False, "error": "invalid index"}, 400)

        elif path == "/api/start_all":
            for p in list_phones():
                if p["is_cpharm"] and not p["running"]:
                    ld("launch", "--index", str(p["index"]))
            self.send_json({"ok": True})

        elif path == "/api/stop_all":
            for p in list_phones():
                if p["is_cpharm"] and p["running"]:
                    ld("quit", "--index", str(p["index"]))
            self.send_json({"ok": True})

        elif path == "/api/restart_all":
            phones = list_phones()
            for p in phones:
                if p["is_cpharm"] and p["running"]:
                    ld("quit", "--index", str(p["index"]))
            time.sleep(3)
            for p in phones:
                if p["is_cpharm"]:
                    ld("launch", "--index", str(p["index"]))
            self.send_json({"ok": True})

        elif path == "/api/clone":
            phones = list_phones()
            cpharm = [p for p in phones if p["is_cpharm"]]
            new_name = f"CPharm-{len(cpharm) + 1}"
            ld("copy", "--name", new_name, "--from", "0")
            self.send_json({"ok": True, "name": new_name})

        elif path == "/api/gpu_info":
            # Returns how many phones the system can handle based on free RAM
            try:
                import ctypes
                mem = ctypes.c_ulonglong(0)
                ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(
                    type('MEMORYSTATUSEX', (ctypes.Structure,), {
                        '_fields_': [('dwLength', ctypes.c_ulong),
                                     ('dwMemoryLoad', ctypes.c_ulong),
                                     ('ullTotalPhys', ctypes.c_ulonglong),
                                     ('ullAvailPhys', ctypes.c_ulonglong),
                                     ('ullTotalPageFile', ctypes.c_ulonglong),
                                     ('ullAvailPageFile', ctypes.c_ulonglong),
                                     ('ullTotalVirtual', ctypes.c_ulonglong),
                                     ('ullAvailVirtual', ctypes.c_ulonglong),
                                     ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
                    })()
                ))
            except Exception:
                pass
            # Simpler approach via wmic
            try:
                out = subprocess.check_output(
                    ["wmic", "OS", "get", "FreePhysicalMemory", "/Value"],
                    text=True, timeout=5
                )
                free_kb = int([l for l in out.splitlines() if "=" in l][0].split("=")[1])
                free_gb = free_kb / 1024 / 1024
                max_phones = max(0, int(free_gb / 1.5))
            except Exception:
                free_gb = 0
                max_phones = 0
            self.send_json({"free_gb": round(free_gb, 1), "max_phones": max_phones})

        else:
            self.send_404()

    def do_OPTIONS(self):
        """CORS preflight."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not os.path.exists(LDPLAYER):
        print(f"\n  [!] LDPlayer not found at {LDPLAYER}")
        print("  [!] Install LDPlayer 9 from ldplayer.net\n")
        input("Press Enter to exit...")
        raise SystemExit(1)

    ip = get_local_ip()
    server = ThreadedHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"\n  ╔══════════════════════════════════════╗")
    print(f"  ║  CPharm Dashboard  •  port {PORT}       ║")
    print(f"  ║  PC:    http://localhost:{PORT}        ║")
    print(f"  ║  Phone: http://{ip}:{PORT}   ║")
    print(f"  ╚══════════════════════════════════════╝")
    print(f"\n  Ctrl+C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Dashboard stopped.")
