"""
CPharm Scheduler — daily hit quota per phone with random fire-times.
Per-phone: given hits_per_day, generates N fire-times spread randomly across 24h and executes the sequence.
"""
import asyncio
import datetime
import json
import logging
import random
import sys
import threading
import time as _time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import dashboard
import tor_manager

log = logging.getLogger("scheduler")

PHONE_SCHED = {}    # serial -> {"hits": N, "times": [epoch], "idx": N}
RUNNING = {}         # serial -> bool
_main_loop = None    # event loop captured from async context
_sched_lock = threading.Lock()


def _name(serial: str) -> str:
    try:
        return next(p["name"] for p in dashboard.list_phones() if p["serial"] == serial)
    except StopIteration:
        return serial


def _gen_today(hits: int) -> list:
    mn = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    offsets = sorted(random.uniform(0, 86400) for _ in range(hits))
    return [mn + o for o in offsets]


def _run_steps(serial: str, steps: list):
    for step in (steps or []):
        t = step.get("type", "")
        if t == "open_url":
            url = step.get("url", "https://google.com")
            dashboard._adb(serial, "shell", "am", "start",
                "-a", "android.intent.action.VIEW", "-d", url,
                "-n", "com.android.chrome/com.google.android.apps.chrome.Main",
                "--ez", "create_new_tab", "true")
        elif t == "tap":
            dashboard._adb(serial, "shell", "input", "tap",
                str(step.get("x", 0)), str(step.get("y", 0)))
        elif t == "wait":
            _time.sleep(float(step.get("seconds", 1)))
        elif t == "swipe":
            dashboard._adb(serial, "shell", "input", "swipe",
                str(step.get("x1", 0)), str(step.get("y1", 0)),
                str(step.get("x2", 0)), str(step.get("y2", 0)),
                str(step.get("ms", 400)))
        elif t == "keyevent":
            dashboard._adb(serial, "shell", "input", "keyevent", step.get("key", "BACK"))
        elif t == "close_app":
            dashboard._adb(serial, "shell", "am", "force-stop",
                step.get("package", "com.android.chrome"))
        elif t == "rotate_identity":
            idx = dashboard._phone_idx_from_serial(serial)
            tor_manager.rotate_identity_adb(serial, idx)
        elif t == "clear_cookies":
            pkg = step.get("package", "com.android.chrome")
            dashboard._adb(serial, "shell", "pm", "clear", pkg)
            dashboard._adb(serial, "shell", "am", "force-stop", pkg)
        elif t == "type_text":
            raw_text = step.get("text", "")
            text = urllib.parse.quote(raw_text, safe="").replace("%20", "%s")
            dashboard._adb(serial, "shell", "input", "text", text)
        elif t == "full_reset":
            idx = dashboard._phone_idx_from_serial(serial)
            tor_manager.full_identity_reset(serial, idx)
        _time.sleep(random.uniform(0.35, 0.55))


def _broadcast_from_thread(msg: dict):
    if _main_loop is not None:
        asyncio.run_coroutine_threadsafe(dashboard.broadcast(msg), _main_loop)


def _sched_loop(serial: str, steps: list, hits_per_day: int):
    log.info("[scheduler] %s started (%s hits/day)", _name(serial), hits_per_day)
    while True:
        with _sched_lock:
            if not RUNNING.get(serial, False):
                break
            info  = PHONE_SCHED.get(serial, {})
            times = list(info.get("times", []))
            idx   = info.get("idx", 0)

        now = _time.time()
        mn_today = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

        if not times or (times and times[0] < mn_today):
            new_times = _gen_today(hits_per_day)
            with _sched_lock:
                PHONE_SCHED[serial] = {"hits": hits_per_day, "times": new_times, "idx": 0}
            log.info("[scheduler] %s new day schedule: %s hits", _name(serial), len(new_times))
            _broadcast_from_thread({
                "type": "scheduler_update", "serial": serial,
                "hits_per_day": hits_per_day, "times": new_times})
            continue

        if idx >= len(times):
            _broadcast_from_thread({"type": "scheduler_done_today", "serial": serial})
            _time.sleep(random.uniform(60, 300))
            continue

        wait = times[idx] - now
        if wait > 0:
            _time.sleep(min(wait, 30))
            continue

        log.info("[scheduler] %s firing [%s/%s]", _name(serial), idx + 1, len(times))
        _broadcast_from_thread({
            "type": "scheduler_tick", "serial": serial,
            "count": idx + 1, "total": len(times)})
        _run_steps(serial, steps)
        with _sched_lock:
            if serial in PHONE_SCHED:
                PHONE_SCHED[serial]["idx"] = idx + 1
        _time.sleep(1)

    with _sched_lock:
        RUNNING.pop(serial, None)
    log.info("[scheduler] %s stopped", _name(serial))


async def handle_scheduler(path: str, body_bytes: bytes, method: str = "POST"):
    global _main_loop
    _main_loop = asyncio.get_running_loop()

    data = {}
    if body_bytes and method != "GET":
        try:
            data = json.loads(body_bytes)
        except Exception:
            pass

    if path == "/api/scheduler/generate":
        serials = data.get("serials", [])
        try:
            hits = int(data.get("hits_per_day", 0))
        except (TypeError, ValueError):
            return dashboard.json_err("hits_per_day must be an integer")
        result = {}
        for s in serials:
            times = _gen_today(hits)
            with _sched_lock:
                PHONE_SCHED[s] = {"hits": hits, "times": times, "idx": 0}
            result[s] = [datetime.datetime.fromtimestamp(t).strftime("%H:%M") for t in times[:12]]
        return dashboard.json_ok({"schedule": result})

    if path == "/api/scheduler/start":
        serials = data.get("serials", [])
        steps   = data.get("steps", [])
        try:
            hits = int(data.get("hits_per_day", 0))
        except (TypeError, ValueError):
            return dashboard.json_err("hits_per_day must be an integer")
        started = []
        with _sched_lock:
            for s in serials:
                if RUNNING.get(s, False):
                    continue
                RUNNING[s] = True
                t = threading.Thread(target=_sched_loop, args=(s, steps, hits), daemon=True)
                t.start()
                started.append(s)
        return dashboard.json_ok({"started": started})

    if path == "/api/scheduler/stop":
        with _sched_lock:
            for k in list(RUNNING):
                RUNNING[k] = False
        return dashboard.json_ok({"ok": True})

    if path == "/api/scheduler/status":
        with _sched_lock:
            snapshot = {s: dict(info) for s, info in PHONE_SCHED.items()}
            running_snap = dict(RUNNING)
        out = {}
        for s, info in snapshot.items():
            times = info.get("times", [])
            idx   = info.get("idx", 0)
            out[s] = {
                "hits_per_day": info.get("hits", 0),
                "fired_today":  idx,
                "remaining":    max(0, len(times) - idx),
                "running":      running_snap.get(s, False),
            }
        return dashboard.json_ok(out)

    return dashboard.json_ok({"ok": False, "error": "unknown route"})
