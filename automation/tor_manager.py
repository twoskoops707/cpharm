"""
Tor Manager — one Tor circuit per phone.
Each phone gets its own SOCKS5 port so websites see a different country per phone.
Also sets a unique MAC address and IMEI on each phone via LDPlayer.
"""

import os
import random
import subprocess
import tempfile
import time
from pathlib import Path

from config import LDPLAYER, TOR_DIR

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


def _ld(*args) -> str:
    try:
        r = subprocess.run([LDPLAYER, *args], capture_output=True, text=True, timeout=20)
        return r.stdout.strip()
    except Exception as e:
        return f"ERROR: {e}"


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

    If Tor Browser is already running (port 9150), reuse it — no setup needed.
    Otherwise start a standalone Tor process per phone.
    """
    # Reuse Tor Browser if it's already running — simplest setup path
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


def apply_identity(phone_idx: int) -> dict:
    """Spoof MAC and IMEI on the phone via LDPlayer, return identity info."""
    mac  = _random_mac()
    imei = _random_imei()
    _ld("modify", "--index", str(phone_idx), "--imei", imei, "--mac", mac)
    return {"mac": mac, "imei": imei, "socks_port": BASE_PORT + phone_idx}


def apply_proxy(phone_idx: int):
    """Tell LDPlayer to route this phone through its Tor SOCKS5 port."""
    port = BASE_PORT + phone_idx
    _ld("modify", "--index", str(phone_idx),
        "--proxy-host", "127.0.0.1",
        "--proxy-port", str(port))


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
