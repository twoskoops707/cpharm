"""
CPharm Scheduler — daily hit quota per phone with random fire-times.

Per-phone: given ``hits_per_day``, generates ``N`` fire-times spread across the
local day using a **stable** RNG per (serial, local day index, quota) so
restarts do not reshuffle today's schedule. Between automation steps, gaps come
from :mod:`human_variation` when enabled; after each scheduled run, a bounded
random pause reduces perfectly periodic spacing.
"""
import asyncio
import datetime
import json
import logging
import re
import random
import sys
import threading
import time as _time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import dashboard
import human_variation as hv
import tor_manager
from sequence_normalize import normalize_automation_steps

log = logging.getLogger("scheduler")

# When JSON body omits hits_per_day, match wizard / groups default (720/day)
DEFAULT_HITS_PER_DAY = 720

PHONE_SCHED = {}    # serial -> {"hits": N, "times": [epoch], "idx": N}
RUNNING = {}         # serial -> bool
# Per-serial sequence replacement while scheduler is running ("true up").
SCHEDULER_STEP_OVERRIDE = {}  # serial -> list of steps
_main_loop = None    # event loop captured from async context
_sched_lock = threading.Lock()


def _name(serial: str) -> str:
    try:
        return next(p["name"] for p in dashboard.list_phones() if p["serial"] == serial)
    except StopIteration:
        return serial


def _gen_today(hits: int, rng: random.Random) -> list:
    """Return sorted epoch fire-times from local midnight, spread via ``rng``."""
    mn = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    offsets = sorted(rng.uniform(0, 86400) for _ in range(hits))
    return [mn + o for o in offsets]


_ALLOWED_STEP_TYPES = frozenset({
    "open_url", "tap", "wait", "swipe", "keyevent",
    "close_app", "clear_cookies", "type_text",
    "rotate_identity", "full_reset",
})


def _effective_steps(serial: str, initial: list) -> list:
    with _sched_lock:
        if serial in SCHEDULER_STEP_OVERRIDE:
            return list(SCHEDULER_STEP_OVERRIDE[serial])
    return list(initial or [])


def _run_steps(serial: str, steps: list, variation_cycle: int = 0):
    eff = _effective_steps(serial, steps)
    rng = hv.rng_for_run(serial, variation_cycle) if hv.enabled() else None
    for step in eff:
        t = step.get("type", "")
        if t not in _ALLOWED_STEP_TYPES:
            continue
        dashboard.run_sequence_step(serial, step, hv_rng=rng)
        if rng is not None:
            _time.sleep(hv.step_gap_seconds(rng))
        else:
            _time.sleep(random.uniform(hv.FALLBACK_STEP_GAP_MIN, hv.FALLBACK_STEP_GAP_MAX))


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
            day_index = int(mn_today // 86400)
            sched_rng = hv.schedule_rng(serial, day_index, hits_per_day)
            new_times = _gen_today(hits_per_day, sched_rng)
            with _sched_lock:
                PHONE_SCHED[serial] = {"hits": hits_per_day, "times": new_times, "idx": 0}
            log.info("[scheduler] %s new day schedule: %s hits", _name(serial), len(new_times))
            _broadcast_from_thread({
                "type": "scheduler_update", "serial": serial,
                "hits_per_day": hits_per_day, "times": new_times})
            continue

        if idx >= len(times):
            _broadcast_from_thread({"type": "scheduler_done_today", "serial": serial})
            srng = hv.schedule_rng(serial, int(mn_today // 86400), hits_per_day)
            _time.sleep(srng.uniform(hv.POST_DAY_IDLE_MIN_SEC, hv.POST_DAY_IDLE_MAX_SEC))
            continue

        wait = times[idx] - now
        if wait > 0:
            _time.sleep(min(wait, 30))
            continue

        log.info("[scheduler] %s firing [%s/%s]", _name(serial), idx + 1, len(times))
        _broadcast_from_thread({
            "type": "scheduler_tick", "serial": serial,
            "count": idx + 1, "total": len(times)})
        day_o = int(_time.time() // 86400)
        cycle = day_o * 100_000 + idx
        _run_steps(serial, steps, variation_cycle=cycle)
        with _sched_lock:
            if serial in PHONE_SCHED:
                PHONE_SCHED[serial]["idx"] = idx + 1
        if hv.enabled():
            _gap_rng = hv.rng_for_run(serial, day_o * 100_000 + idx)
            _time.sleep(hv.inter_scheduled_hit_seconds(_gap_rng))
        else:
            _time.sleep(random.uniform(hv.INTER_SCHEDULED_HIT_MIN, hv.INTER_SCHEDULED_HIT_MAX))

    with _sched_lock:
        RUNNING.pop(serial, None)
        SCHEDULER_STEP_OVERRIDE.pop(serial, None)
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

    _SERIAL_RE = re.compile(r'^[A-Za-z0-9._:\-]+$')

    if path == "/api/scheduler/generate":
        serials = [s for s in data.get("serials", []) if _SERIAL_RE.match(str(s))]
        try:
            hits = max(0, min(int(data.get("hits_per_day", DEFAULT_HITS_PER_DAY)), 1440))
        except (TypeError, ValueError):
            return dashboard.json_err("hits_per_day must be an integer")
        mn_local = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        day_index = int(mn_local // 86400)
        result = {}
        for s in serials:
            times = _gen_today(hits, hv.schedule_rng(s, day_index, hits))
            with _sched_lock:
                PHONE_SCHED[s] = {"hits": hits, "times": times, "idx": 0}
            result[s] = [datetime.datetime.fromtimestamp(t).strftime("%H:%M") for t in times[:12]]
        return dashboard.json_ok({"schedule": result})

    if path == "/api/scheduler/start":
        serials = [s for s in data.get("serials", []) if _SERIAL_RE.match(str(s))]
        default_steps = data.get("steps", [])
        steps_per_serial = data.get("steps_per_serial") or {}
        hits_per_serial = data.get("hits_per_serial") or {}
        try:
            default_hits = max(0, min(int(data.get("hits_per_day", DEFAULT_HITS_PER_DAY)), 1440))
        except (TypeError, ValueError):
            return dashboard.json_err("hits_per_day must be an integer")
        started = []
        with _sched_lock:
            for s in serials:
                if RUNNING.get(s, False):
                    continue
                steps = steps_per_serial.get(s, default_steps)
                if not isinstance(steps, list):
                    steps = default_steps
                try:
                    hits = hits_per_serial.get(s, default_hits)
                    hits = max(0, min(int(hits), 1440))
                except (TypeError, ValueError):
                    hits = default_hits
                RUNNING[s] = True
                t = threading.Thread(target=_sched_loop, args=(s, steps, hits), daemon=True)
                t.start()
                started.append(s)
        return dashboard.json_ok({"ok": True, "started": started})

    if path == "/api/scheduler/stop":
        with _sched_lock:
            for k in list(RUNNING):
                RUNNING[k] = False
            SCHEDULER_STEP_OVERRIDE.clear()
        return dashboard.json_ok({"ok": True})

    if path == "/api/scheduler/true_up":
        serial = str(data.get("serial", "")).strip()
        new_steps = data.get("steps")
        if not serial or not _SERIAL_RE.match(serial):
            return dashboard.json_err("serial required")
        if not isinstance(new_steps, list):
            return dashboard.json_err("steps must be a JSON array")
        norm = normalize_automation_steps(new_steps)
        with _sched_lock:
            if not RUNNING.get(serial, False):
                return dashboard.json_err(
                    "scheduler is not running for this phone — start the daily schedule first")
            SCHEDULER_STEP_OVERRIDE[serial] = norm
        return dashboard.json_ok({"ok": True, "serial": serial, "steps_n": len(norm)})

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
