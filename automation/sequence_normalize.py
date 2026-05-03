"""
Normalize automation step lists for CPharm.

MuMu Player ARM keeps ``default_sequence*.json`` under the install ``manager\\``
folder. Those files use the same ``type`` values as CPharm (``open_url``, ``tap``,
``wait``, ``swipe``, ``close_app``, ``rotate_identity``, …). MuMu often stores
``wait.seconds`` as a string (``"3.5"``, ``".75"``); the runtime already coerces
via ``float()``, but normalizing produces stable JSON when saving from the wizard
or API.

CPharm-only optional fields on ``tap`` (``retries``, ``jitter``, ``pre_wait_ms``)
may be absent in MuMu exports — ``dashboard.run_sequence_step`` applies defaults.
"""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any


def _as_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def normalize_automation_steps(steps: list) -> list:
    """Return a new list with coerced numeric fields; unknown step keys are preserved."""
    if not isinstance(steps, list):
        raise TypeError("steps must be a JSON array")
    out: list = []
    for raw in steps:
        if not isinstance(raw, dict):
            out.append(raw)
            continue
        step = deepcopy(raw)
        t = step.get("type", "")
        if t == "wait":
            step["seconds"] = min(max(0.0, _as_float(step.get("seconds", 1), 1.0)), 300.0)
        elif t == "tap":
            step["x"] = max(0, _as_int(step.get("x", 0), 0))
            step["y"] = max(0, _as_int(step.get("y", 0), 0))
            if "retries" in step:
                step["retries"] = max(1, min(8, _as_int(step.get("retries", 1), 1)))
            if "jitter" in step:
                step["jitter"] = max(0, min(120, _as_int(step.get("jitter", 0), 0)))
            if "pre_wait_ms" in step:
                step["pre_wait_ms"] = min(max(0.0, _as_float(step.get("pre_wait_ms", 0), 0.0)), 10000.0)
        elif t == "swipe":
            for k in ("x1", "y1", "x2", "y2"):
                step[k] = max(0, _as_int(step.get(k, 0), 0))
            step["ms"] = max(1, min(5000, _as_int(step.get("ms", 400), 400)))
        out.append(step)
    return out


def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Normalize a MuMu/CPharm JSON sequence file (array of step objects)."
    )
    p.add_argument("input", type=Path, help="Source JSON file")
    p.add_argument(
        "output",
        nargs="?",
        type=Path,
        help="Output JSON (default: stdout)",
    )
    args = p.parse_args(argv)
    text = args.input.read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, list):
        print("Error: root JSON value must be an array.", file=sys.stderr)
        return 1
    normalized = normalize_automation_steps(data)
    out_txt = json.dumps(normalized, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(out_txt, encoding="utf-8")
    else:
        sys.stdout.write(out_txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
