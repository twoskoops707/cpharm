import random
"""
Teach Mode - record taps on the first phone, replay on all others staggered.
Uses ADB device serials - works with any connected Android device.
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


def _adb(serial: str, *args) -> str:
    try:
        r = subprocess.run(
            ["adb", "-s", serial, *args],
            capture_output=True, text=True, timeout=20
        )
        return r.stdout.strip()
    except Exception:
        return ""



def start_recording(serial: str) -> str:
    global _recording_active, _recording_thread, _current_file

    ts = int(time.time())
    _current_file     = REC_DIR / f"session_{ts}.rec"
    _recording_active = True
    events: list[dict] = []
    start_time = time.time()

    def record():
        proc = subprocess.Popen(
            ["adb", "-s", serial, "shell", "getevent", "-lt"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
        )
        try:
            for line in proc.stdout:
                if not _recording_active:
                    break
                line = line.strip()
                if not line:
                    continue
                # Format: /dev/input/event5: EV_XXX TYPE XXX CODE XXX VALUE XXX
                # or:    /dev/input/event5: EV_XXX TYPE XXX CODE XXX
                # Capture: device, event_type, event_code, event_value
                parts = line.split()
                if len(parts) < 4:
                    continue
                device = parts[0].rstrip(":")
                event_type  = parts[1]
                event_code  = parts[2]
                event_value = parts[3]
                events.append({
                    "t":     round(time.time() - start_time, 3),
                    "d":     device,
                    "type":  event_type,
                    "code":  event_code,
                    "value": event_value,
                })
        finally:
            proc.terminate()
            _current_file.write_text(json.dumps(events))

    _recording_thread = threading.Thread(target=record, daemon=True)
    _recording_thread.start()
    return str(_current_file)


def stop_recording() -> str | None:
    global _recording_active, _recording_thread, _current_file
    _recording_active = False
    if _recording_thread:
        _recording_thread.join(timeout=3)
    return str(_current_file) if _current_file else None


def replay_on_phone(serial: str, recording_path: str):
    """Replay a recording on a specific phone using sendevent.

    Works without root on AVD emulators. Each event is sent with a 3-second
    timeout so a dead device never hangs the replay.
    """
    try:
        data = json.loads(Path(recording_path).read_text())
    except (ValueError, OSError):
        return

    for entry in data:
        try:
            subprocess.run(
                ["adb", "-s", serial, "shell", "sendevent",
                 entry.get("d",""), entry.get("type",""),
                 entry.get("code",""), entry.get("value","")],
                capture_output=True, timeout=3
            )
        except subprocess.TimeoutExpired:
            break  # Device went offline - stop replaying
        except Exception:
            pass


def replay_all(
    phones: list[dict],
    recording_path: str,
    delay_secs: int = 60,
    on_complete: Callable | None = None,
):
    def run():
        for i, phone in enumerate(phones):
            if i > 0:
                actual = max(0.1, delay_secs + random.uniform(-delay_secs * 0.3, delay_secs * 0.3))
                time.sleep(actual)
            replay_on_phone(phone["serial"], recording_path)
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
