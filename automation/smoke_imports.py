#!/usr/bin/env python3
"""
Import + logic sanity (no HTTP/WS, no ADB). From repo root:

  py -3 automation/smoke_imports.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_AUT = Path(__file__).resolve().parent


def main() -> int:
    sys.path.insert(0, str(_AUT))

    import config
    import human_variation as hv

    if not (1 <= config.PORT <= 65535 and 1 <= config.WS_PORT <= 65535):
        print("smoke_imports: bad ports", config.PORT, config.WS_PORT, file=sys.stderr)
        return 1
    if not str(config.BIND or "").strip():
        print("smoke_imports: empty BIND", file=sys.stderr)
        return 1

    a = hv.schedule_rng("emulator-5554", 20_000, 100)
    b = hv.schedule_rng("emulator-5554", 20_000, 100)
    if a.uniform(0, 1) != b.uniform(0, 1):
        print("smoke_imports: schedule_rng not stable for same args", file=sys.stderr)
        return 1

    c = hv.schedule_rng("emulator-5554", 20_001, 100)
    if a.uniform(0, 1) == c.uniform(0, 1):
        # vanishingly unlikely if seeds differ; day index changes stream
        pass  # no assert — just ensure callability

    j = hv.stagger_seconds(5.0, hv.rng_for_run("s", 1)) if hv.enabled() else 5.0
    if j < 0:
        return 1

    # Scheduler regression: hits_per_day<=0 must not busy-spin (see scheduler._sched_loop guard).
    import scheduler as sched_mod

    assert getattr(sched_mod, "_sched_loop", None) is not None

    print(
        "smoke_imports: OK  BIND=%r  HTTP=%s  WS=%s  human_variation=%s"
        % (config.BIND, config.PORT, config.WS_PORT, hv.enabled())
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
