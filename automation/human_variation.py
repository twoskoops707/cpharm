"""
Per-device human-like variance: same step semantics, stable RNG per (serial, cycle).

Enable via config/env (see ``config.HUMAN_VARIATION`` / ``CPHARM_HUMAN_VARIATION``).
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
# Pre-tap delay scaling
PRE_WAIT_SPREAD = 0.06


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


def scaled_wait_seconds(base: float, rng: random.Random) -> float:
    lo, hi = 1.0 - WAIT_SPREAD, 1.0 + WAIT_SPREAD
    return max(0.0, base * rng.uniform(lo, hi))


def scaled_pre_wait_ms(base_ms: float, rng: random.Random) -> float:
    if base_ms <= 0:
        return 0.0
    lo, hi = 1.0 - PRE_WAIT_SPREAD, 1.0 + PRE_WAIT_SPREAD
    return min(10000.0, base_ms * rng.uniform(lo, hi))


def effective_tap_jitter(step_jitter: int, rng: Optional[random.Random]) -> int:
    """Clamp step jitter to [0,120]; if human mode and rng set, add bounded variance."""
    j = max(0, min(120, step_jitter))
    if rng is None or not enabled():
        return j
    if j == 0:
        return rng.randint(AUTO_JITTER_MIN, AUTO_JITTER_MAX)
    return min(120, j + rng.randint(0, EXTRA_ON_TOP))


def step_gap_seconds(rng: random.Random) -> float:
    return rng.uniform(STEP_GAP_MIN, STEP_GAP_MAX)
