# CPharm v2 — Design Spec
Date: 2026-04-17

## What It Is

A virtual Android phone farm controller. You run it on a Windows PC. It lets you control many Android phones (running inside LDPlayer) from any browser — your PC or your phone.

---

## Who It's For

Anyone. No tech experience needed. Every button, label, and message uses plain everyday words.

---

## How You Access It

- **On your PC:** open a browser, go to `http://localhost:8080`
- **On your phone:** open a browser, go to `http://YOUR-PC-IP:8080`

Both show the exact same page. Both can control the farm.

---

## The Page (Top to Bottom)

Everything lives on one scrollable page. No menus to dig through.

### 1. Header
- App name `[C·PHARM]` on the left
- Your PC's address on the right (tap to copy)
- Updates live — no need to refresh

### 2. Stats Bar
Four numbers across the top:
- **Phones** — total count
- **On** — how many are running
- **Off** — how many are stopped
- **Memory** — estimated RAM in use

### 3. Big Buttons
- **▶ Start All** — turns on every phone
- **■ Stop All** — turns off every phone
- **+ New Phone** — makes a copy of Phone 1

### 4. Your Phones
A card for every phone. Each card shows:
- Green dot (on) or grey dot (off)
- Phone name
- Status: running or off
- Which app is installed and what version
- A **Start** or **Stop** button

### 5. Install an App
- Drop an APK file onto the box, or tap to pick one
- Shows the file name, version, and how many phones have it
- **Install All** button — installs on every phone at once

### 6. Open a Website
- Paste any web address
- **Open Now** — all phones open it at the same time
- **Stagger** — phones open it one by one with a delay between each (choose: 1 min, 5 min, or custom)

### 7. Teach Mode
Teach the farm what to do by showing it once:
1. Tap **Start Recording** — Phone 1 is now being watched
2. Do your steps: open a site, tap buttons, scroll, type — anything
3. Tap **Stop Recording**
4. Tap **Play on All Phones** — every other phone does the same steps, one after another with your chosen delay

### 8. Make Each Phone Look Different
- One tap sets this up — no configuration needed
- Each phone gets routed through **Tor** automatically
- Every phone appears to come from a completely different country
- Each phone also gets a unique fake device ID (MAC address)
- To websites and apps, they look like totally different people

---

## Real-Time Updates

The page updates itself instantly. When a phone turns on or off, the card changes right away. No need to refresh.

---

## Tech Stack

| Piece | What it does |
|---|---|
| `dashboard.py` | Python backend — asyncio + WebSockets |
| `dashboard.html` | Single-page frontend — vanilla HTML/JS/CSS |
| `ldconsole.exe` | Controls LDPlayer (start, stop, clone phones) |
| `adb.exe` | Installs APKs, records/replays touches |
| Tor | One circuit per phone — automatic IP per phone |
| `stem` (Python) | Controls Tor circuits from Python |

---

## Backend API

### WebSocket: `ws://localhost:8080/ws`
Pushes live updates to the browser:
- `phones_update` — current state of all phones
- `install_progress` — APK install progress per phone
- `teach_status` — recording/playback state

### REST endpoints
| Method | Path | What it does |
|---|---|---|
| GET | `/api/phones` | List all phones + status |
| POST | `/api/phone/start/:id` | Start one phone |
| POST | `/api/phone/stop/:id` | Stop one phone |
| POST | `/api/start_all` | Start all phones |
| POST | `/api/stop_all` | Stop all phones |
| POST | `/api/clone` | Clone Phone 1 |
| POST | `/api/install` | Install APK on all phones |
| POST | `/api/open_url` | Open URL on all phones (with optional stagger) |
| POST | `/api/teach/start` | Start recording on Phone 1 |
| POST | `/api/teach/stop` | Stop recording |
| POST | `/api/teach/play` | Play recording on all phones (staggered) |
| POST | `/api/proxy/setup` | Assign a Tor circuit to each phone |
| GET | `/api/ip` | Get this PC's local IP |

---

## Tor Setup (Auto)

- On first run, system checks if Tor is installed; if not, downloads it silently
- Spins up one Tor SOCKS5 port per phone (starting at port 9050)
- Each LDPlayer phone is configured to route through its assigned port
- Each phone also gets a randomised MAC address via `ldconsole modify`
- User sees one button: **Make Each Phone Look Different** — everything else is automatic

---

## Teach Mode (Record + Replay)

- Uses `adb shell getevent` to record raw touch/key events on Phone 1
- Saves the event stream to `automation/recordings/session_<timestamp>.rec`
- Replay uses `adb shell sendevent` on each target phone
- Stagger delay (user-chosen) is inserted between each phone's playback start

---

## APK Tracking

- After each install, reads `adb shell pm dump <package>` to get version name
- Stores per-phone version in memory; shown on each phone card
- If a phone is off when install runs, it's marked "pending" and installs when it next starts

---

## File Structure

```
CPharm/
  automation/
    dashboard.py        ← backend (rewritten)
    dashboard.html      ← frontend (rewritten)
    manifest.json
    sw.js
    icon-192.png
    icon-512.png
    recordings/         ← teach mode sessions saved here
  apks/                 ← drop APK files here
  gui/
    cpharm_gui.py       ← existing desktop GUI (kept, not changed)
  scripts/              ← existing .bat files (kept)
  setup/                ← existing setup scripts (kept)
  docs/
    superpowers/
      specs/
        2026-04-17-cpharm-v2-design.md
  START_HERE.bat        ← existing (kept)
  requirements.txt      ← updated: add websockets, stem
```

---

## Language Rules

Every visible string follows these rules:
- No jargon (no APK, ADB, SOCKS, circuit, instance, emulator)
- Short sentences
- Action words on buttons ("Start", "Stop", "Install", "Open", "Record", "Play")
- Descriptions explain what happens in plain terms
