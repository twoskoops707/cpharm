"""
Per-device human-like variance: same step semantics, stable RNG per (serial, cycle).

Enable via config/env (see ``config.HUMAN_VARIATION`` / ``CPHARM_HUMAN_VARIATION``).

Scheduling uses a separate stable RNG (see :func:`schedule_rng`) so a given phone's
fire times for a calendar day are reproducible across process restarts when the
quota is unchanged—better for unattended runs than reshuffling on every boot.
"""
from __future__ import annotations

import hashlib
import random
from typing import Optional

from config import HUMAN_VARIATION

# Tune: wait duration multiplier is uniform in [1 - WAIT_SPREAD, 1 + WAIT_SPREAD]
WAIT_SPREAD = 0.08
# Extra tap jitter (px): when step omits jitter, use AUTO_JITTER range; else add up to EXTRA_ON_TOP
AUTO_JITTER_MIN = 2
AUTO_JITTER_MAX = 7
EXTRA_ON_TOP = 4
# Between-step pause (seconds) when human variation is on
STEP_GAP_MIN = 0.28
STEP_GAP_MAX = 0.52
# Same intent when human variation is off (scheduler / non-HV path)
FALLBACK_STEP_GAP_MIN = 0.35
FALLBACK_STEP_GAP_MAX = 0.55
# Pre-tap delay scaling
PRE_WAIT_SPREAD = 0.06
# Cap scaled pre-tap wait (ms) so bad configs cannot stall taps unbounded
PRE_WAIT_MS_CAP = 10000.0
# Multi-phone stagger: scale base delay by ± this fraction (human mode only)
STAGGER_JITTER_FRAC = 0.15
# After each scheduled "hit" completes, pause before the next wait loop (seconds)
INTER_SCHEDULED_HIT_MIN = 0.72
INTER_SCHEDULED_HIT_MAX = 1.38
# When all hits for the day are done, idle until next local midnight (bounds only)
POST_DAY_IDLE_MIN_SEC = 60.0
POST_DAY_IDLE_MAX_SEC = 300.0
# Blake2b key so schedule RNG stream never collides with :func:`stable_seed` mixes
_SCHED_RNG_KEY = b"cpharm.sched.firetimes.v1"


def enabled() -> bool:
    return bool(HUMAN_VARIATION)


def stable_seed(serial: str, cycle: int) -> int:
    """Deterministic 64-bit seed from device id + run cycle (day/index/iteration)."""
    h = hashlib.blake2b(serial.encode("utf-8"), digest_size=8).digest()
    base = int.from_bytes(h, "little")
    mixed = base ^ (cycle * 0x9E3779B97F4A7C15) ^ (len(serial) << 48)
    return mixed & 0xFFFFFFFFFFFFFFFF


def rng_for_run(serial: str, cycle: int) -> random.Random:
    return random.Random(stable_seed(serial, cycle))


def schedule_rng(serial: str, local_day_index: int, hits_per_day: int) -> random.Random:
    """
    Stable RNG for generating one day's fire offsets.

    Same ``serial``, ``local_day_index`` (e.g. ``int(local_midnight_ts // 86400)``),
    and ``hits_per_day`` always produce the same sequence, so the schedule does not
    change on scheduler restart. Changing the daily quota or the next local day
    produces a new spread.
    """
    payload = f"{serial}\x00{local_day_index}\x00{hits_per_day}".encode("utf-8")
    h = hashlib.blake2b(payload, digest_size=8, key=_SCHED_RNG_KEY).digest()
    seed = int.from_bytes(h, "little") & 0xFFFFFFFFFFFFFFFF
    return random.Random(seed)


def scaled_wait_seconds(base: float, rng: random.Random) -> float:
    """Scale automation ``wait`` duration by uniform(1−WAIT_SPREAD, 1+WAIT_SPREAD)."""
    lo, hi = 1.0 - WAIT_SPREAD, 1.0 + WAIT_SPREAD
    return max(0.0, base * rng.uniform(lo, hi))


def scaled_pre_wait_ms(base_ms: float, rng: random.Random) -> float:
    """Scale pre-tap delay like ``scaled_wait_seconds``, capped at ``PRE_WAIT_MS_CAP``."""
    if base_ms <= 0:
        return 0.0
    lo, hi = 1.0 - PRE_WAIT_SPREAD, 1.0 + PRE_WAIT_SPREAD
    return min(PRE_WAIT_MS_CAP, base_ms * rng.uniform(lo, hi))


def effective_tap_jitter(step_jitter: int, rng: Optional[random.Random]) -> int:
    """Clamp step jitter to [0,120]; if human mode and rng set, add bounded variance."""
    j = max(0, min(120, step_jitter))
    if rng is None or not enabled():
        return j
    if j == 0:
        return rng.randint(AUTO_JITTER_MIN, AUTO_JITTER_MAX)
    return min(120, j + rng.randint(0, EXTRA_ON_TOP))


def step_gap_seconds(rng: random.Random) -> float:
    """Random pause (seconds) after each automation step when human variation is on."""
    return rng.uniform(STEP_GAP_MIN, STEP_GAP_MAX)


def inter_scheduled_hit_seconds(rng: random.Random) -> float:
    """Bounded delay after a scheduled run finishes before the worker goes back to sleep."""
    return rng.uniform(INTER_SCHEDULED_HIT_MIN, INTER_SCHEDULED_HIT_MAX)


def stagger_seconds(base: float, rng: Optional[random.Random]) -> float:
    """
    Delay between devices when staggering launches/actions.

    With human variation enabled, scales ``base`` by uniform(1−j, 1+j) where
    j = :data:`STAGGER_JITTER_FRAC` so multi-phone traffic is less perfectly aligned.
    """
    if base <= 0:
        return 0.0
    b = float(base)
    if rng is None or not enabled():
        return b
    lo, hi = 1.0 - STAGGER_JITTER_FRAC, 1.0 + STAGGER_JITTER_FRAC
    return max(0.0, b * rng.uniform(lo, hi))
