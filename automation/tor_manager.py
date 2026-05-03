"""
Tor Manager — one Tor circuit per phone.
Each phone gets its own SOCKS5 port so websites see a different IP per phone.
Works with any ADB-connected device (real phone, AVD, BlueStacks, etc.).
"""

import os
import random
import re
import socket
import subprocess
import tempfile
import time
from pathlib import Path

from config import TOR_DIR

BASE_PORT = 9050
TOR_BROWSER_SOCKS = 9150
TOR_BROWSER_CONTROL = 9151
_tor_procs: dict[int, subprocess.Popen] = {}

# Set during /api/proxy/setup — aligns Tor ports with dashboard.enumerate order (fixes USB idx=0 bug).
_serial_tor_slot: dict[str, int] = {}


def register_tor_slot(serial: str, slot: int) -> None:
    """Bind this device serial to Tor SOCKS/control ports for ``slot``."""
    _serial_tor_slot[str(serial)] = int(slot)


def clear_tor_slots() -> None:
    _serial_tor_slot.clear()


def tor_slot_for_serial(serial: str, fallback_idx: int) -> int:
    return _serial_tor_slot.get(serial, fallback_idx)


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
    try:
        with socket.create_connection(("127.0.0.1", TOR_BROWSER_SOCKS), timeout=1):
            return True
    except OSError:
        return False


def socks_port_for_slot(slot: int) -> int:
    """SOCKS port that matches ``start_tor_for_phone(slot)``."""
    if _tor_browser_running():
        return TOR_BROWSER_SOCKS
    return BASE_PORT + slot


def control_port_for_slot(slot: int) -> int:
    """Tor control port for NEWNYM / auth."""
    if _tor_browser_running():
        return TOR_BROWSER_CONTROL
    return BASE_PORT + 1000 + slot


def _control_cookie_path(slot: int) -> Path:
    """Cookie file for Tor control authentication."""
    if _tor_browser_running():
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        tb = local / "TorBrowser" / "Data" / "Tor" / "control_auth_cookie"
        if tb.exists():
            return tb
    return TOR_DIR / f"data_{slot}" / "control_auth_cookie"


def _proxy_host_for_serial(serial: str) -> str:
    """
    Target address *as seen from the Android device* for the PC's Tor SOCKS port.

    - AVD / most emulators: 10.0.2.2 maps to host loopback (127.0.0.1 on PC).
    - adb TCP to 127.0.0.1 (MuMu etc.): same loopback alias as AVD.
    - Wireless debugging (ip:port): use this PC's LAN address so the phone can route to Tor.
    - USB: use 127.0.0.1 together with ``adb reverse`` (see ``_ensure_usb_reverse``).
    """
    if serial.startswith("emulator-"):
        return "10.0.2.2"
    m = re.match(r"^([\d.]+):(\d+)$", serial)
    if m:
        ip = m.group(1)
        if ip == "127.0.0.1":
            return "10.0.2.2"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            lan = s.getsockname()[0]
            s.close()
            return lan
        except OSError:
            return ip
    return "127.0.0.1"


def _needs_usb_reverse(serial: str) -> bool:
    if serial.startswith("emulator-"):
        return False
    if re.match(r"^[\d.]+:\d+$", serial):
        return False
    return True


def _ensure_usb_reverse(serial: str, port: int) -> None:
    """Forward host Tor port to device localhost (USB debugging)."""
    if not _needs_usb_reverse(serial):
        return
    try:
        subprocess.run(
            ["adb", "-s", serial, "reverse", f"tcp:{port}", f"tcp:{port}"],
            capture_output=True,
            timeout=12,
        )
    except Exception:
        pass


def _adb_clear_global_proxy(serial: str) -> None:
    """Remove broken proxy settings so the browser works without Tor."""
    try:
        subprocess.run(
            ["adb", "-s", serial, "shell", "settings", "delete", "global", "http_proxy"],
            capture_output=True,
            timeout=10,
        )
    except Exception:
        pass
    try:
        subprocess.run(
            ["adb", "-s", serial, "shell", "settings", "put", "global", "global_http_proxy_host", ""],
            capture_output=True,
            timeout=10,
        )
    except Exception:
        pass


def clear_proxy_adb(serial: str) -> None:
    """Clear Android global HTTP proxy on one device."""
    _adb_clear_global_proxy(serial)


def clear_all_proxies_on_devices(serials: list[str]) -> None:
    for s in serials:
        clear_proxy_adb(s)


def start_tor_for_phone(phone_idx: int) -> int:
    """Start a Tor process for this phone. Returns the SOCKS5 port.

    If Tor Browser is already running (port 9150), reuse it.
    Otherwise start a standalone Tor process per phone.
    """
    if _tor_browser_running():
        return TOR_BROWSER_SOCKS

    socks_port = BASE_PORT + phone_idx
    ctrl_port = BASE_PORT + 1000 + phone_idx

    if phone_idx in _tor_procs and _tor_procs[phone_idx].poll() is None:
        return socks_port

    data_dir = TOR_DIR / f"data_{phone_idx}"
    data_dir.mkdir(parents=True, exist_ok=True)

    cfg = tempfile.NamedTemporaryFile(
        mode="w", suffix=".cfg", delete=False, dir=str(TOR_DIR)
    )
    cookie_path = data_dir / "control_auth_cookie"
    try:
        cfg.write(f"SocksPort {socks_port}\n")
        cfg.write(f"ControlPort {ctrl_port}\n")
        cfg.write(f"DataDirectory {data_dir}\n")
        cfg.write(f"CookieAuthentication 1\n")
        cfg.write(f"CookieAuthFile {cookie_path}\n")
        cfg.write("ExitNodes {us}\n")
        cfg.write("StrictNodes 0\n")
    finally:
        cfg.close()

    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.Popen(
        [_tor_exe(), "-f", cfg.name],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    _tor_procs[phone_idx] = proc
    return socks_port


def send_newnym(slot: int) -> bool:
    """Send NEWNYM to the Tor control port for this slot (Tor Browser or standalone)."""
    ctrl_port = control_port_for_slot(slot)
    cookie_path = _control_cookie_path(slot)
    try:
        cookie = cookie_path.read_bytes() if cookie_path.exists() else b""
        with socket.create_connection(("127.0.0.1", ctrl_port), timeout=3) as s:
            auth = b"AUTHENTICATE " + cookie.hex().encode() + b"\r\n"
            s.sendall(auth)
            s.recv(256)
            s.sendall(b"SIGNAL NEWNYM\r\n")
            resp = s.recv(256)
            return b"250" in resp
    except OSError:
        return False


def apply_identity_adb(serial: str, phone_idx: int) -> dict:
    """
    Route this phone's traffic through its Tor SOCKS port via Android global proxy.
    Note: Android global proxy only covers HTTP/HTTPS traffic for many apps.
    Full SOCKS5 routing requires the app to support proxies natively.
    """
    slot = tor_slot_for_serial(serial, phone_idx)
    port = socks_port_for_slot(slot)
    host = _proxy_host_for_serial(serial)
    _ensure_usb_reverse(serial, port)
    try:
        subprocess.run(
            ["adb", "-s", serial, "shell", "settings", "put", "global",
             "http_proxy", f"{host}:{port}"],
            capture_output=True,
            timeout=10,
        )
    except Exception:
        pass
    return {"socks_port": port, "proxy_host": host, "slot": slot}


def rotate_identity_adb(serial: str, phone_idx: int) -> dict:
    """
    Rotate this phone's identity:
    1. Request a new Tor circuit (new exit IP).
    2. Re-apply the proxy so Android picks up the new circuit.
    """
    slot = tor_slot_for_serial(serial, phone_idx)
    rotated = send_newnym(slot)
    time.sleep(1)
    result = apply_identity_adb(serial, phone_idx)
    result["circuit_rotated"] = rotated
    return result


def wait_for_tor(phone_idx: int, timeout: int = 30) -> bool:
    port = socks_port_for_slot(phone_idx)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(1)
    return False


def randomize_mac_adb(serial: str) -> dict:
    """
    Randomize the WiFi MAC address of the device.
    Uses a locally-administered unicast MAC (starts with 02:...).
    Requires root (su) on the device.
    """
    mac = _random_mac()
    import re as _re
    if not _re.match(r'^([0-9A-F]{2}:){5}[0-9A-F]{2}$', mac, _re.IGNORECASE):
        return {"mac": mac, "applied": False}
    try:
        subprocess.run(
            ["adb", "-s", serial, "shell", "su", "-c",
             f"ip link set wlan0 address {mac}"],
            capture_output=True, timeout=10
        )
    except Exception:
        pass
    # Fallback: ip link set without su
    try:
        result = subprocess.run(
            ["adb", "-s", serial, "shell", "ip", "link", "set", "wlan0", "address", mac],
            capture_output=True, text=True, timeout=10
        )
        applied = result.returncode == 0
    except Exception:
        applied = False
    return {"mac": mac, "applied": applied}


def randomize_android_id_adb(serial: str) -> str:
    """
    Generate and set a new random Android ID.
    Android ID is stored at settings/secure/android_id.
    """
    new_id = os.urandom(8).hex()
    try:
        subprocess.run(
            ["adb", "-s", serial, "shell", "settings", "put", "secure",
             "android_id", new_id],
            capture_output=True, timeout=10
        )
    except Exception:
        pass
    return new_id


def full_identity_reset(serial: str, phone_idx: int) -> dict:
    """
    Complete anonymity reset for a phone after a sequence completes.
    1. New Tor circuit (new exit IP)
    2. New Android ID
    3. New MAC address
    4. Clear browser cookies / app data
    """
    slot = tor_slot_for_serial(serial, phone_idx)
    tor_rotated = send_newnym(slot)
    time.sleep(0.5)

    # Re-apply proxy to pick up new Tor circuit (same host:port; fresh circuit).
    apply_identity_adb(serial, phone_idx)

    new_android_id = randomize_android_id_adb(serial)

    mac_result = randomize_mac_adb(serial)

    try:
        subprocess.run(
            ["adb", "-s", serial, "shell", "pm", "clear", "com.android.chrome"],
            capture_output=True, timeout=10
        )
        subprocess.run(
            ["adb", "-s", serial, "shell", "am", "force-stop", "com.android.chrome"],
            capture_output=True, timeout=10
        )
    except Exception:
        pass

    return {
        "tor_circuit_rotated": tor_rotated,
        "new_android_id": new_android_id,
        "new_mac": mac_result.get("mac", ""),
        "mac_applied": mac_result.get("applied", False),
    }


def stop_all():
    """Stop and clean up all Tor processes."""
    for idx in list(_tor_procs.keys()):
        proc = _tor_procs.pop(idx, None)
        if proc and proc.poll() is None:
            proc.terminate()
