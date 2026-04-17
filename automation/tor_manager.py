"""
Tor Manager — one Tor circuit per phone.
Each phone gets its own SOCKS5 port (9050, 9051, 9052...).
Websites see a different country for every phone.
"""

import os
import subprocess
import random
import tempfile
import time
from pathlib import Path

TOR_DIR  = Path(__file__).parent / "tor"
BASE_PORT = 9050

_tor_procs: dict[int, subprocess.Popen] = {}


def _tor_exe() -> str:
    candidates = [
        str(TOR_DIR / "tor.exe"),
        r"C:\Tor\tor.exe",
        r"C:\Program Files\Tor Browser\Browser\TorBrowser\Tor\tor.exe",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return "tor"


def _random_mac() -> str:
    parts = [random.randint(0x00, 0xFF) for _ in range(6)]
    parts[0] &= 0xFE
    parts[0] |= 0x02
    return ":".join(f"{b:02X}" for b in parts)


def _random_imei() -> str:
    digits = [random.randint(0, 9) for _ in range(14)]
    total = 0
    for i, d in enumerate(digits):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    check = (10 - (total % 10)) % 10
    return "".join(map(str, digits)) + str(check)


def start_tor_for_phone(phone_idx: int) -> int:
    """Start a Tor process for this phone. Returns the SOCKS5 port."""
    socks_port  = BASE_PORT + phone_idx
    ctrl_port   = BASE_PORT + 1000 + phone_idx

    if phone_idx in _tor_procs and _tor_procs[phone_idx].poll() is None:
        return socks_port

    data_dir = TOR_DIR / f"data_{phone_idx}"
    data_dir.mkdir(parents=True, exist_ok=True)

    cfg = tempfile.NamedTemporaryFile(
        mode="w", suffix=".cfg", delete=False,
        dir=str(TOR_DIR)
    )
    cfg.write(f"SocksPort {socks_port}\n")
    cfg.write(f"ControlPort {ctrl_port}\n")
    cfg.write(f"DataDirectory {data_dir}\n")
    cfg.write("ExitNodes {us},{gb},{de},{fr},{jp},{au},{ca},{nl},{se},{br}\n")
    cfg.write("StrictNodes 0\n")
    cfg.close()

    proc = subprocess.Popen(
        [_tor_exe(), "-f", cfg.name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    _tor_procs[phone_idx] = proc
    return socks_port


def stop_tor_for_phone(phone_idx: int):
    proc = _tor_procs.pop(phone_idx, None)
    if proc and proc.poll() is None:
        proc.terminate()


def stop_all():
    for idx in list(_tor_procs.keys()):
        stop_tor_for_phone(idx)


def get_identity(phone_idx: int) -> dict:
    return {
        "mac":  _random_mac(),
        "imei": _random_imei(),
        "socks_port": BASE_PORT + phone_idx,
    }


def wait_for_tor(phone_idx: int, timeout: int = 30) -> bool:
    """Wait until the Tor SOCKS5 port is listening."""
    import socket
    port = BASE_PORT + phone_idx
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(1)
    return False
