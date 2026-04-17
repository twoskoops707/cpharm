"""
Teach Mode — record taps/actions on Phone 1, replay on all others.
Uses ADB input events for recording and replay.
"""

import json
import subprocess
import threading
import time
from pathlib import Path

RECORDINGS_DIR = Path(__file__).parent / "recordings"
RECORDINGS_DIR.mkdir(exist_ok=True)

LDPLAYER = r"C:\LDPlayer\LDPlayer9\ldconsole.exe"

_recording_thread: threading.Thread | None = None
_recording_active  = False
_current_file: Path | None = None


def _adb_device(phone_idx: int) -> str:
    port = 5554 + phone_idx * 2
    return f"emulator-{port}"


def _run_adb(phone_idx: int, *args) -> subprocess.Popen | str:
    device = _adb_device(phone_idx)
    return subprocess.run(
        ["adb", "-s", device, *args],
        capture_output=True, text=True, timeout=10
    ).stdout.strip()


def _run_adb_popen(phone_idx: int, *args) -> subprocess.Popen:
    device = _adb_device(phone_idx)
    return subprocess.Popen(
        ["adb", "-s", device, *args],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True
    )


def start_recording(phone_idx: int = 1) -> str:
    """Start recording input events on the given phone. Returns the file path."""
    global _recording_active, _recording_thread, _current_file

    ts = int(time.time())
    _current_file = RECORDINGS_DIR / f"session_{ts}.rec"
    _recording_active = True

    events: list[dict] = []
    start_time = time.time()

    def record():
        proc = _run_adb_popen(phone_idx, "shell", "getevent", "-lt")
        try:
            for line in proc.stdout:
                if not _recording_active:
                    break
                line = line.strip()
                if line:
                    events.append({
                        "t": round(time.time() - start_time, 3),
                        "e": line,
                    })
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


def replay_on_phone(phone_idx: int, recording_path: str, speed: float = 1.0):
    """Replay a recording on a single phone."""
    data = json.loads(Path(recording_path).read_text())
    prev_t = 0.0
    device = _adb_device(phone_idx)
    for entry in data:
        delay = (entry["t"] - prev_t) / speed
        if delay > 0:
            time.sleep(delay)
        prev_t = entry["t"]
        line = entry["e"]
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 4:
            dev  = parts[0].rstrip(":")
            etype = parts[1]
            ecode = parts[2]
            evalue = parts[3]
            subprocess.run(
                ["adb", "-s", device, "shell", "sendevent", dev, etype, ecode, evalue],
                capture_output=True, timeout=5
            )


def replay_all(phones: list[dict], recording_path: str, delay_secs: int = 60):
    """Replay on all phones staggered by delay_secs between each."""
    def run():
        for i, phone in enumerate(phones):
            if i > 0:
                time.sleep(delay_secs)
            replay_on_phone(phone["index"], recording_path)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def list_recordings() -> list[dict]:
    files = sorted(RECORDINGS_DIR.glob("session_*.rec"), reverse=True)
    out = []
    for f in files:
        try:
            data = json.loads(f.read_text())
            duration = data[-1]["t"] if data else 0
            out.append({
                "name": f.name,
                "path": str(f),
                "steps": len(data),
                "duration": round(duration, 1),
            })
        except Exception:
            continue
    return out
