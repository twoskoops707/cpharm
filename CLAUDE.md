# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build

The `.exe` is built automatically by GitHub Actions on any push to `wizard/**`:

```
.github/workflows/build-wizard.yml
```

Trigger a build: push any change inside `wizard/`. The workflow uses `pyinstaller --onefile --windowed --name CPharmSetup setup_wizard.py` on a Windows runner. Artifacts are uploaded as `CPharmSetup-exe` with 30-day retention.

There is no local build command — builds run on GitHub Actions only (Windows runner required).

## Syntax check (quick local validation)

```bash
python3 -m py_compile wizard/setup_wizard.py
python3 -m py_compile automation/dashboard.py
python3 -m py_compile automation/tor_manager.py
python3 -m py_compile automation/scheduler.py
```

## Run the dashboard server locally

```bash
cd automation && python dashboard.py
```

Dashboard serves on port `8080` (HTTP) and `8081` (WebSocket), defined in `automation/config.py`.

## Architecture

### Two separate programs

**1. Setup Wizard** (`wizard/setup_wizard.py`, ~4100 lines)
- Single-file tkinter GUI wizard that walks users through first-time setup
- 8 pages: Welcome → Install Tools → Android SDK → Create Phones → Start Phones → Play Store → Groups → Launch
- All pages extend `PageBase(tk.Frame)` and are shown by `CPharmWizard(tk.Tk)`
- Phone sequences are stored **per-phone** inside groups: `group["phones"][serial]["steps"]` — not at the group level
- Downloads: Java, Python, Android cmdline-tools, Tor expert bundle, CPharm repo ZIP
- `_urlretrieve(url, dest, hook)` is a wrapper around `urllib.request.urlretrieve` that adds a timeout
- Per-phone sequence editing uses `PerPhoneSequenceEditor` (defined at top of file, line ~114)
- ARM64 Windows detection: checks `PROCESSOR_ARCHITEW6432` env var + registry; ARM64 emulator needs arm64-v8a system image

**2. Automation Server** (`automation/dashboard.py`)
- Raw asyncio TCP HTTP server (no Flask/Django) + WebSocket broadcast
- All automation uses ADB device serials (not indices)
- Groups of phones run in parallel threads; phones within a group stagger via `stagger_secs`
- `state["groups"]` is a list of group dicts; each group has `phones: {serial: {"steps": [...]}}`

### Key automation modules

| File | Purpose |
|------|---------|
| `automation/config.py` | Ports (8080/8081), path constants (APK_DIR, REC_DIR, TOR_DIR), EMULATOR_PORTS list |
| `automation/tor_manager.py` | One Tor process per phone; SOCKS port = 9050 + phone_idx; Android HTTP proxy via `settings put global http_proxy` |
| `automation/scheduler.py` | Daily hit quota per phone — generates N random fire-times across 24h; step types: open_url, tap, wait, swipe, keyevent, close_app, rotate_identity, clear_cookies, type_text, full_reset |
| `automation/teach.py` | Record `getevent -lt` from one phone; replay via `sendevent` on others |
| `automation/playstore.py` | Play Store automation: search, install, launch, leave review; coordinates scale from 1280×720 baseline |

### State schema

Shared `state` dict in wizard:
```python
state = {
    "phones":     [{"serial": str, "name": str, ...}],   # booted phones
    "groups":     [{"name": str, "phones": {serial: {"steps": [...]}},
                    "stagger_secs": int, "repeat": int, "repeat_forever": bool}],
    "cpharm_dir": str,    # path to cloned repo root
    "python_cmd": str,    # "python" or "python3"
    "sdk_path":   str,    # Android SDK root
    "avds":       [...],  # list of AVD names
}
```

### GitHub Actions / branch notes

- Main branch: `master`
- Feature work: `arm64-emulator-fix` (current)
- Build triggers: any push to `wizard/**`
