"""
Tor Manager — one Tor circuit per phone.
Each phone gets its own SOCKS5 port so websites see a different IP per phone.
Works with any ADB-connected device (real phone, AVD, BlueStacks, etc.).
"""

import os
import random
import subprocess
import tempfile
import time
from pathlib import Path

from config import TOR_DIR

BASE_PORT = 9050
_tor_procs: dict[int, subprocess.Popen] = {}


def _tor_exe() -> str:
    for c in [str(TOR_DIR / "tor.exe"), r"C:\Tor\tor.exe"]:
        if Path(c).exists():
            return c
    return "tor"


def _random_mac() -> str:
    parts = [random.randint(0x00, 0xFF) for _ in range(6)]
    parts[0] = (parts[0] & 0xFE) | 0x02
    return ":".join(f"{b:02X}" for b in parts)


def _random_imei() -> str:
    digits = [random.randint(0, 9) for _ in range(14)]
    total = sum((d * 2 - 9 if d * 2 > 9 else d * 2) if i % 2 == 1 else d
                for i, d in enumerate(digits))
    check = (10 - (total % 10)) % 10
    return "".join(map(str, digits)) + str(check)


def _tor_browser_running() -> bool:
    """Check if Tor Browser's Tor is already running on 9150."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", 9150), timeout=1):
            return True
    except OSError:
        return False


def start_tor_for_phone(phone_idx: int) -> int:
    """Start a Tor process for this phone. Returns the SOCKS5 port.

    If Tor Browser is already running (port 9150), reuse it.
    Otherwise start a standalone Tor process per phone.
    """
    if _tor_browser_running():
        return 9150

    socks_port = BASE_PORT + phone_idx
    ctrl_port  = BASE_PORT + 1000 + phone_idx

    if phone_idx in _tor_procs and _tor_procs[phone_idx].poll() is None:
        return socks_port

    data_dir = TOR_DIR / f"data_{phone_idx}"
    data_dir.mkdir(parents=True, exist_ok=True)

    cfg = tempfile.NamedTemporaryFile(
        mode="w", suffix=".cfg", delete=False, dir=str(TOR_DIR)
    )
    cfg.write(f"SocksPort {socks_port}\n")
    cfg.write(f"ControlPort {ctrl_port}\n")
    cfg.write(f"DataDirectory {data_dir}\n")
    cfg.write("ExitNodes {us},{gb},{de},{fr},{jp},{au},{ca},{nl},{se},{br}\n")
    cfg.write("StrictNodes 0\n")
    cfg.close()

    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.Popen(
        [_tor_exe(), "-f", cfg.name],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    _tor_procs[phone_idx] = proc
    return socks_port


def _send_tor_newnym(ctrl_port: int) -> bool:
    """Send NEWNYM signal to Tor control port to get a fresh circuit."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", ctrl_port), timeout=3) as s:
            s.sendall(b'AUTHENTICATE ""\r\n')
            s.recv(256)
            s.sendall(b"SIGNAL NEWNYM\r\n")
            resp = s.recv(256)
            return b"250" in resp
    except OSError:
        return False


def apply_identity_adb(serial: str, phone_idx: int) -> dict:
    """
    Route this phone's traffic through its Tor SOCKS5 port via Android global proxy.
    Note: Android global proxy only covers HTTP/HTTPS traffic.
    Full SOCKS5 routing requires the app to support proxies natively.
    """
    port = BASE_PORT + phone_idx
    try:
        subprocess.run(
            ["adb", "-s", serial, "shell", "settings", "put", "global",
             "http_proxy", f"127.0.0.1:{port}"],
            capture_output=True, timeout=10
        )
    except Exception:
        pass
    return {"socks_port": port}


def rotate_identity_adb(serial: str, phone_idx: int) -> dict:
    """
    Rotate this phone's identity:
    1. Request a new Tor circuit (new exit IP).
    2. Clear and re-apply the proxy so Android picks up the new circuit.
    """
    ctrl_port = BASE_PORT + 1000 + phone_idx
    rotated   = _send_tor_newnym(ctrl_port)
    time.sleep(1)
    result = apply_identity_adb(serial, phone_idx)
    result["circuit_rotated"] = rotated
    return result


def wait_for_tor(phone_idx: int, timeout: int = 30) -> bool:
    import socket
    port     = BASE_PORT + phone_idx
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(1)
    return False


def stop_all():
    for idx in list(_tor_procs.keys()):
        proc = _tor_procs.pop(idx, None)
        if proc and proc.poll() is None:
            proc.terminate()
