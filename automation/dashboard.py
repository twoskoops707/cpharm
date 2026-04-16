#!/usr/bin/env python3
"""CPharm Web Dashboard — control LDPlayer phone farm from any browser."""

import http.server
import json
import os
import subprocess
import urllib.parse
from pathlib import Path

LDPLAYER = r"C:\LDPlayer\LDPlayer9\ldconsole.exe"
PORT = 8080
SCRIPT_DIR = Path(__file__).parent
APK_DIR = SCRIPT_DIR.parent / "apks"

# ── LDPlayer helpers ──────────────────────────────────────────────────────────

def ld(args: list[str]) -> str:
    """Run ldconsole and return stdout."""
    try:
        result = subprocess.run(
            [LDPLAYER] + args,
            capture_output=True, text=True, timeout=15
        )
        return result.stdout.strip()
    except Exception as e:
        return f"ERROR: {e}"


def list_phones() -> list[dict]:
    """Return all CPharm instances with status."""
    raw = ld(["list2"])
    phones = []
    for line in raw.splitlines():
        parts = line.split(",")
        if len(parts) < 2:
            continue
        index, name = parts[0].strip(), parts[1].strip()
        if not index.isdigit():
            continue
        running_raw = ld(["isrunning", "--index", index])
        running = "running" in running_raw.lower()
        phones.append({
            "index": int(index),
            "name": name,
            "running": running,
            "is_cpharm": name.lower().startswith("cpharm"),
        })
    return phones


def find_apk() -> str | None:
    apks = list(APK_DIR.glob("*.apk"))
    return str(apks[0]) if apks else None


# ── HTML page ─────────────────────────────────────────────────────────────────

HTML_FILE = SCRIPT_DIR / "dashboard.html"

def load_html() -> str:
    """Load dashboard HTML, injecting the real server host for the IP badge."""
    if HTML_FILE.exists():
        return HTML_FILE.read_text(encoding="utf-8").replace(
            "const DEMO_MODE = true;", "const DEMO_MODE = false;"
        )
    return "<h1>dashboard.html not found</h1>"


# ── HTTP handler ──────────────────────────────────────────────────────────────

class Handler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"  {self.address_string()} {fmt % args}")

    def send_json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html: str):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, fpath: Path, mime: str):
        body = fpath.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/" or path == "/index.html":
            self.send_html(load_html())
        elif path == "/api/phones":
            self.send_json(list_phones())
        elif path == "/manifest.json":
            self.send_file(SCRIPT_DIR / "manifest.json", "application/manifest+json")
        elif path == "/sw.js":
            self.send_file(SCRIPT_DIR / "sw.js", "application/javascript")
        elif path in ("/icon-192.png", "/icon-512.png"):
            size = 192 if "192" in path else 512
            self.send_file(SCRIPT_DIR / f"icon-{size}.png", "image/png")
        elif path == "/api/ip":
            import socket
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                ip = s.getsockname()[0]
                s.close()
            except Exception:
                ip = "unknown"
            self.send_json({"ip": ip, "url": f"http://{ip}:{PORT}"})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path

        # POST /api/phone/start/<index>
        if path.startswith("/api/phone/start/"):
            idx = path.split("/")[-1]
            ld(["launch", "--index", idx])
            self.send_json({"ok": True})

        # POST /api/phone/stop/<index>
        elif path.startswith("/api/phone/stop/"):
            idx = path.split("/")[-1]
            ld(["quit", "--index", idx])
            self.send_json({"ok": True})

        # POST /api/start_all
        elif path == "/api/start_all":
            for p in list_phones():
                if p["is_cpharm"] and not p["running"]:
                    ld(["launch", "--index", str(p["index"])])
            self.send_json({"ok": True})

        # POST /api/stop_all
        elif path == "/api/stop_all":
            for p in list_phones():
                if p["is_cpharm"] and p["running"]:
                    ld(["quit", "--index", str(p["index"])])
            self.send_json({"ok": True})

        # POST /api/restart_all
        elif path == "/api/restart_all":
            for p in list_phones():
                if p["is_cpharm"] and p["running"]:
                    ld(["quit", "--index", str(p["index"])])
            import time; time.sleep(2)
            for p in list_phones():
                if p["is_cpharm"]:
                    ld(["launch", "--index", str(p["index"])])
            self.send_json({"ok": True})

        # POST /api/clone
        elif path == "/api/clone":
            phones = list_phones()
            cpharm = [p for p in phones if p["is_cpharm"]]
            new_name = f"CPharm-{len(cpharm) + 1}"
            ld(["copy", "--name", new_name, "--from", "0"])
            self.send_json({"ok": True, "name": new_name})

        else:
            self.send_response(404)
            self.end_headers()


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not os.path.exists(LDPLAYER):
        print(f"[!] LDPlayer not found at {LDPLAYER}")
        print("[!] Install LDPlayer 9 from ldplayer.net first.")
        input("Press Enter to exit...")
        raise SystemExit(1)

    server = http.server.HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"\n  CPharm Dashboard running on port {PORT}")
    print(f"  http://localhost:{PORT}")
    print(f"\n  Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Dashboard stopped.")
