"""
Play Store Tester — ADB-based organic Play Store activity per phone.
All functions use device serials (consistent with dashboard.py / teach.py).
"""

import subprocess
import time
import threading
from typing import Callable


def _adb(serial: str, *args, timeout: int = 20) -> str:
    try:
        r = subprocess.run(
            ["adb", "-s", serial, *args],
            capture_output=True, text=True, timeout=timeout
        )
        return r.stdout.strip()
    except Exception as e:
        return f"ERROR: {e}"


def _screen_size(serial: str) -> tuple[int, int]:
    """Return (width, height) of the device screen."""
    raw = _adb(serial, "shell", "wm", "size")
    for part in (raw or "").split():
        if "x" in part and part.replace("x", "").isdigit():
            try:
                w, h = part.split("x")
                return int(w), int(h)
            except ValueError:
                pass
    return 1280, 720


def _scale(serial: str, x: int, y: int, base_w: int = 1280, base_h: int = 720) -> tuple[int, int]:
    """Scale coordinates from base resolution to actual device resolution."""
    w, h = _screen_size(serial)
    return round(x * w / base_w), round(y * h / base_h)


def _intent(serial: str, url: str) -> str:
    return _adb(serial, "shell", "am", "start",
                "-a", "android.intent.action.VIEW",
                "-d", url)


def _input_text(serial: str, text: str):
    safe = text.replace(" ", "%s").replace("'", "").replace('"', "")
    _adb(serial, "shell", "input", "text", safe)


def _keyevent(serial: str, code: int):
    _adb(serial, "shell", "input", "keyevent", str(code))


def _tap(serial: str, x: int, y: int):
    _adb(serial, "shell", "input", "tap", str(x), str(y))


def _swipe(serial: str, x1: int, y1: int, x2: int, y2: int, ms: int = 400):
    _adb(serial, "shell", "input", "swipe",
         str(x1), str(y1), str(x2), str(y2), str(ms))


def _wake(serial: str):
    _adb(serial, "shell", "input", "keyevent", "224")
    time.sleep(0.4)
    _adb(serial, "shell", "input", "keyevent", "82")
    time.sleep(0.3)


def open_store_page_serial(serial: str, package: str) -> bool:
    """Open the Play Store listing for a specific package."""
    _wake(serial)
    _intent(serial, f"market://details?id={package}")
    time.sleep(2)
    return True


def search_store(serial: str, query: str) -> bool:
    """Open Play Store search results for a query string."""
    _wake(serial)
    _intent(serial, f"market://search?q={query.replace(' ', '+')}")
    time.sleep(2)
    return True


def install_from_store(serial: str, package: str, on_log: Callable | None = None) -> bool:
    """
    Open the Play Store page and tap Install.
    Coordinates are scaled dynamically from 1280×720 baseline.
    Install button is top-right of the app page in Play Store.
    """
    if on_log:
        on_log(f"{serial}: opening Play Store for {package}")
    _wake(serial)
    _intent(serial, f"market://details?id={package}")
    time.sleep(3)

    ix, iy = _scale(serial, 1100, 200)
    _tap(serial, ix, iy)
    time.sleep(1)

    cx, cy = _scale(serial, 640, 450)
    _tap(serial, cx, cy)
    time.sleep(2)

    if on_log:
        on_log(f"{serial}: tapped Install")
    return True


def launch_app(serial: str, package: str, on_log: Callable | None = None) -> bool:
    """Launch an installed app via monkey launcher intent."""
    if on_log:
        on_log(f"{serial}: launching {package}")
    _wake(serial)
    _adb(serial, "shell", "monkey", "-p", package,
         "-c", "android.intent.category.LAUNCHER", "1")
    time.sleep(2)
    return True


def leave_review(
    serial: str,
    package: str,
    stars: int,
    review_text: str,
    on_log: Callable | None = None,
) -> bool:
    """
    Navigate to app's Play Store page, tap review section,
    select star rating, type the review, and submit.
    Coordinates scale dynamically from 1280×720 baseline.
    """
    if on_log:
        on_log(f"{serial}: opening review page for {package}")
    _wake(serial)

    _intent(serial, f"market://details?id={package}")
    time.sleep(3)

    for _ in range(3):
        x1, y1 = _scale(serial, 640, 500)
        x2, y2 = _scale(serial, 640, 200)
        _swipe(serial, x1, y1, x2, y2, 500)
        time.sleep(0.6)

    wx, wy = _scale(serial, 640, 550)
    _tap(serial, wx, wy)
    time.sleep(1.5)

    star_x_base = 270 + (stars - 1) * 100
    sx, sy = _scale(serial, star_x_base, 400)
    _tap(serial, sx, sy)
    time.sleep(0.8)

    tx, ty = _scale(serial, 640, 500)
    _tap(serial, tx, ty)
    time.sleep(0.6)

    _input_text(serial, review_text)
    time.sleep(0.4)

    sbx, sby = _scale(serial, 1050, 620)
    _tap(serial, sbx, sby)
    time.sleep(1)

    if on_log:
        on_log(f"{serial}: review submitted ({stars}★)")
    return True


def run_full_sequence(
    phones: list[dict],
    package: str,
    search_query: str,
    stars: int,
    review_text: str,
    delay_secs: int,
    on_log: Callable | None = None,
    on_complete: Callable | None = None,
):
    """
    Run the full Play Store sequence on each phone, staggered by delay_secs.
    Sequence per phone: search → open page → install → launch → review.
    phones: list of dicts with at least {"serial": str, "name": str, "running": bool}
    """
    def run():
        running = [p for p in phones if p.get("running")]
        for i, phone in enumerate(running):
            serial = phone["serial"]
            name = phone.get("name", serial)
            if i > 0 and delay_secs > 0:
                time.sleep(delay_secs)
            try:
                if search_query:
                    if on_log:
                        on_log(f"{name}: searching for '{search_query}'")
                    search_store(serial, search_query)
                    time.sleep(2)
                if package:
                    install_from_store(serial, package, on_log=on_log)
                    time.sleep(5)
                    launch_app(serial, package, on_log=on_log)
                    time.sleep(3)
                if review_text and stars:
                    leave_review(serial, package, stars, review_text, on_log=on_log)
            except Exception as e:
                if on_log:
                    on_log(f"{name}: error — {e}")

        if on_complete:
            on_complete()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t
