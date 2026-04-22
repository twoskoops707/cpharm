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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import dashboard
import tor_manager

log = logging.getLogger("scheduler")

PHONE_SCHED = {}   # serial -> {"hits": N, "times": [epoch], "idx": N}
RUNNING = {}        # serial -> bool
_main_loop = None   # event loop captured from async context


def _name(serial: str) -> str:
    try:
        return next(p["name"] for p in dashboard.list_phones() if p["serial"] == serial)
    except StopIteration:
        return serial


def _gen_today(hits: int) -> list[float]:
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
        _time.sleep(random.uniform(0.35, 0.55))


def _broadcast_from_thread(msg: dict):
    if _main_loop is not None:
        asyncio.run_coroutine_threadsafe(dashboard.broadcast(msg), _main_loop)


def _sched_loop(serial: str, steps: list, hits_per_day: int):
    log.info("[scheduler] %s started (%s hits/day)", _name(serial), hits_per_day)
    while RUNNING.get(serial, False):
        info = PHONE_SCHED.get(serial, {})
        times = info.get("times", [])
        idx = info.get("idx", 0)
        now = _time.time()
        mn_today = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        if not times or (times and times[0] < mn_today):
            times = _gen_today(hits_per_day)
            PHONE_SCHED[serial] = {"hits": hits_per_day, "times": times, "idx": 0}
            log.info("[scheduler] %s new day schedule: %s hits", _name(serial), len(times))
            _broadcast_from_thread({
                "type": "scheduler_update", "serial": serial,
                "hits_per_day": hits_per_day, "times": times})
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
        PHONE_SCHED[serial]["idx"] = idx + 1
        _time.sleep(1)
    log.info("[scheduler] %s stopped", _name(serial))
    RUNNING.pop(serial, None)


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
        hits = int(data.get("hits_per_day", 0))
        result = {}
        for s in serials:
            times = _gen_today(hits)
            PHONE_SCHED[s] = {"hits": hits, "times": times, "idx": 0}
            result[s] = [datetime.datetime.fromtimestamp(t).strftime("%H:%M") for t in times[:12]]
        return dashboard.json_ok({"schedule": result})

    if path == "/api/scheduler/start":
        serials = data.get("serials", [])
        steps = data.get("steps", [])
        hits = int(data.get("hits_per_day", 0))
        started = []
        for s in serials:
            if RUNNING.get(s, False):
                continue
            RUNNING[s] = True
            t = threading.Thread(target=_sched_loop, args=(s, steps, hits), daemon=True)
            t.start()
            started.append(s)
        return dashboard.json_ok({"started": started})

    if path == "/api/scheduler/stop":
        for k in list(RUNNING):
            RUNNING[k] = False
        return dashboard.json_ok({"ok": True})

    if path == "/api/scheduler/status":
        out = {}
        for s, info in PHONE_SCHED.items():
            times = info.get("times", [])
            idx = info.get("idx", 0)
            out[s] = {
                "hits_per_day": info.get("hits", 0),
                "fired_today": idx,
                "remaining": max(0, len(times) - idx),
                "running": RUNNING.get(s, False),
            }
        return dashboard.json_ok(out)

    return dashboard.json_ok({"ok": False, "error": "unknown route"})
