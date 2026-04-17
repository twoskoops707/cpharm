"""
Play Store Tester — ADB-based organic Play Store activity per phone.
Opens the Play Store, searches for an app, installs it, and can submit a review.
Each action runs via ADB shell intents and input commands.
"""

import subprocess
import time
import threading
from typing import Callable

from config import LDPLAYER


def _adb(idx: int, *args, timeout: int = 20) -> str:
    device = f"emulator-{5554 + idx * 2}"
    try:
        r = subprocess.run(
            ["adb", "-s", device, *args],
            capture_output=True, text=True, timeout=timeout
        )
        return r.stdout.strip()
    except Exception as e:
        return f"ERROR: {e}"


def _intent(idx: int, url: str) -> str:
    return _adb(idx, "shell", "am", "start",
                "-a", "android.intent.action.VIEW",
                "-d", url)


def _input_text(idx: int, text: str):
    safe = text.replace(" ", "%s").replace("'", "").replace('"', "")
    _adb(idx, "shell", "input", "text", safe)


def _keyevent(idx: int, code: int):
    _adb(idx, "shell", "input", "keyevent", str(code))


def _tap(idx: int, x: int, y: int):
    _adb(idx, "shell", "input", "tap", str(x), str(y))


def _swipe(idx: int, x1: int, y1: int, x2: int, y2: int, ms: int = 400):
    _adb(idx, "shell", "input", "swipe",
         str(x1), str(y1), str(x2), str(y2), str(ms))


def _wake(idx: int):
    _adb(idx, "shell", "input", "keyevent", "224")
    time.sleep(0.4)
    _adb(idx, "shell", "input", "keyevent", "82")
    time.sleep(0.3)


def open_store_page(idx: int, package: str) -> bool:
    """Open the Play Store listing for a specific package."""
    _wake(idx)
    out = _intent(idx, f"market://details?id={package}")
    time.sleep(2)
    return "Error" not in out


def search_store(idx: int, query: str) -> bool:
    """Open Play Store search results for a query string."""
    _wake(idx)
    out = _intent(idx, f"market://search?q={query.replace(' ', '+')}")
    time.sleep(2)
    return "Error" not in out


def install_from_store(idx: int, package: str, on_log: Callable | None = None) -> bool:
    """
    Open the Play Store page for `package` and tap Install.
    Uses screen coordinates calibrated for LDPlayer 9 at 1280x720.
    """
    if on_log:
        on_log(f"Phone {idx}: opening Play Store for {package}")
    _wake(idx)
    _intent(idx, f"market://details?id={package}")
    time.sleep(3)

    # Tap the Install button area (top-right of app page in Play Store)
    # LDPlayer 9 default 1280×720: Install button ~x=1100, y=200
    _tap(idx, 1100, 200)
    time.sleep(1)

    # Dismiss any account picker if it appears (Accept / Continue)
    _tap(idx, 640, 450)
    time.sleep(2)

    if on_log:
        on_log(f"Phone {idx}: tapped Install")
    return True


def launch_app(idx: int, package: str, on_log: Callable | None = None) -> bool:
    """Launch an installed app."""
    if on_log:
        on_log(f"Phone {idx}: launching {package}")
    _wake(idx)
    _adb(idx, "shell", "monkey", "-p", package,
         "-c", "android.intent.category.LAUNCHER", "1")
    time.sleep(2)
    return True


def leave_review(
    idx: int,
    package: str,
    stars: int,
    review_text: str,
    on_log: Callable | None = None,
) -> bool:
    """
    Navigate to the app's Play Store page, tap the review section,
    select star rating, type the review, and submit.
    All coordinates are for LDPlayer 9 at 1280×720.
    """
    if on_log:
        on_log(f"Phone {idx}: opening review page for {package}")
    _wake(idx)

    # Open review intent directly
    _intent(idx, f"market://details?id={package}")
    time.sleep(3)

    # Scroll down to reach the reviews section (~3 swipes)
    for _ in range(3):
        _swipe(idx, 640, 500, 640, 200, 500)
        time.sleep(0.6)

    # Tap "Write a Review" button area
    _tap(idx, 640, 550)
    time.sleep(1.5)

    # Tap the star corresponding to rating (stars are equally spaced ~100px apart, starts ~x=320)
    star_x = 270 + (stars - 1) * 100
    _tap(idx, star_x, 400)
    time.sleep(0.8)

    # Tap the review text area
    _tap(idx, 640, 500)
    time.sleep(0.6)

    # Type the review text
    _input_text(idx, review_text)
    time.sleep(0.4)

    # Tap Submit / Post
    _tap(idx, 1050, 620)
    time.sleep(1)

    if on_log:
        on_log(f"Phone {idx}: review submitted ({stars}★)")
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
    Run the full Play Store test sequence on each phone, staggered by delay_secs.
    Sequence per phone: search → open page → install → launch → review.
    """
    def run():
        for i, phone in enumerate(phones):
            if i > 0 and delay_secs > 0:
                time.sleep(delay_secs)
            idx = phone["index"]
            try:
                if search_query:
                    search_store(idx, search_query)
                    time.sleep(2)
                if package:
                    install_from_store(idx, package, on_log=on_log)
                    time.sleep(5)
                    launch_app(idx, package, on_log=on_log)
                    time.sleep(3)
                if review_text and stars:
                    leave_review(idx, package, stars, review_text, on_log=on_log)
            except Exception as e:
                if on_log:
                    on_log(f"Phone {idx}: error — {e}")

        if on_complete:
            on_complete()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t
