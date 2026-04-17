"""
Teach Mode — record taps on Phone 1, replay on all others staggered.
"""

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from config import REC_DIR

REC_DIR.mkdir(exist_ok=True)

_recording_active = False
_recording_thread: threading.Thread | None = None
_current_file: Path | None = None


def _device(phone_idx: int) -> str:
    return f"emulator-{5554 + phone_idx * 2}"


def start_recording(phone_idx: int = 1) -> str:
    global _recording_active, _recording_thread, _current_file

    ts = int(time.time())
    _current_file    = REC_DIR / f"session_{ts}.rec"
    _recording_active = True
    events: list[dict] = []
    start_time = time.time()

    def record():
        proc = subprocess.Popen(
            ["adb", "-s", _device(phone_idx), "shell", "getevent", "-lt"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
        )
        try:
            for line in proc.stdout:
                if not _recording_active:
                    break
                line = line.strip()
                if line:
                    events.append({"t": round(time.time() - start_time, 3), "e": line})
        finally:
            proc.terminate()
            _current_file.write_text(json.dumps(events, indent=2))

    _recording_thread = threading.Thread(target=record, daemon=True)
    _recording_thread.start()
    return str(_current_file)


def stop_recording() -> str | None:
    global _recording_active
    _recording_active = False
    if _recording_thread:
        _recording_thread.join(timeout=3)
    return str(_current_file) if _current_file else None


def replay_on_phone(phone_idx: int, recording_path: str):
    data   = json.loads(Path(recording_path).read_text())
    device = _device(phone_idx)
    prev_t = 0.0
    for entry in data:
        delay = entry["t"] - prev_t
        if delay > 0:
            time.sleep(delay)
        prev_t = entry["t"]
        parts  = entry["e"].split()
        if len(parts) >= 4:
            dev, etype, ecode, evalue = parts[0].rstrip(":"), parts[1], parts[2], parts[3]
            subprocess.run(
                ["adb", "-s", device, "shell", "sendevent", dev, etype, ecode, evalue],
                capture_output=True, timeout=5
            )


def replay_all(
    phones: list[dict],
    recording_path: str,
    delay_secs: int = 60,
    on_complete: Callable | None = None,
):
    def run():
        for i, phone in enumerate(phones):
            if i > 0:
                time.sleep(delay_secs)
            replay_on_phone(phone["index"], recording_path)
        if on_complete:
            on_complete()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def list_recordings() -> list[dict]:
    out = []
    for f in sorted(REC_DIR.glob("session_*.rec"), reverse=True):
        try:
            data = json.loads(f.read_text())
            out.append({
                "name":     f.name,
                "path":     str(f),
                "steps":    len(data),
                "duration": round(data[-1]["t"], 1) if data else 0,
            })
        except Exception:
            continue
    return out
