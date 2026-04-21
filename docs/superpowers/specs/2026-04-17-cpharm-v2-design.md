# CPharm v2 — Design Spec
Date: 2026-04-17
Updated: 2026-04-21

## What It Is

A virtual Android phone farm controller. You run it on a Windows PC. It lets you control many Android phones (running as AVD emulators or real devices via ADB) from any browser — PC or phone.

---

## How You Access It

- **On your PC:** open a browser, go to `http://localhost:8080`
- **On your phone:** open a browser, go to `http://YOUR-PC-IP:8080`

---

## The Page (Top to Bottom)

### 1. Header
- App name `[C·PHARM]` on the left
- Your PC's address on the right (tap to copy)

### 2. Stats Bar
Four numbers: Phones (total), On, Off, Memory

### 3. Big Buttons
- **▶ Start All** / **■ Stop All**
- **+ New Phone** (connects more emulators)

### 4. Your Phones
Card per phone: green dot = on, grey = off. Shows app installed + version.

### 5. Install an App
Drop APK or tap to pick. Install All button.

### 6. Open a Website
Paste URL. Open Now (all at once) or staggered.

### 7. Teach Mode
Record on Phone 1 → Play on all others, staggered.

### 8. Make Each Phone Look Different (Tor / Anonymity)
One button sets up Tor per phone. Each phone has:
- Its own Tor SOCKS5 port
- A new exit IP per phone
- A unique Android ID
- A random MAC address
- After each sequence: full identity reset (new Tor circuit + new Android ID + new MAC + clear cookies)

---

## Per-Phone Sequences

Each phone in a group has its **own independent sequence**. The wizard shows:
- Each phone listed with its own step count
- A "✏ Edit" button to edit that phone's sequence individually
- A **"📋 Clone to All Phones"** button — copies Phone 1's sequence to every phone in the group
- Each phone's sequence runs its own steps independently when the group starts
- After each phone finishes its sequence → **automatic full identity reset** (new IP + Android ID + MAC + cleared cookies)

### Data Model
```json
{
  "groups": [{
    "name": "Group 1",
    "phones": {
      "serial_1": { "steps": [{ "type": "open_url", "url": "..." }, { "type": "tap", "x": 640, "y": 400 }] },
      "serial_2": { "steps": [{ "type": "open_url", "url": "..." }, { "type": "wait", "seconds": 5 }] }
    },
    "stagger_secs": 30,
    "repeat": 1,
    "repeat_forever": false
  }]
}
```

Old format (`phones: []`, `steps: []`) is auto-migrated on load.

---

## Anonymity — Full Identity Reset

After each sequence iteration, every phone gets:
1. **Tor NEWNYM** — new exit circuit → new IP
2. **New Android ID** — `settings put secure android_id <random_64bit_hash>`
3. **New MAC address** — random wlan0 MAC (best-effort, requires root)
4. **Chrome cleared** — `pm clear com.android.chrome` + force-stop

Single function: `tor_manager.full_identity_reset(serial, phone_idx)`

Manual triggers:
- `POST /api/identity/reset` — reset one phone
- `POST /api/identity/reset_all` — reset all running phones

---

## Tech Stack

| Piece | What |
|---|---|
| `dashboard.py` | Python asyncio backend — WebSockets + HTTP |
| `dashboard.html` | Vanilla JS frontend — single page |
| `tor_manager.py` | Tor per-phone circuit management + full identity reset |
| `teach.py` | ADB getevent/sendevent record/replay |
| `playstore.py` | Play Store install + review automation |
| `scheduler.py` | Daily hit quota with random fire-times per phone |
| `setup_wizard.py` | Tkinter GUI — creates AVDs, runs phones, builds groups |
| `ldconsole.exe` / `adb` | Phone control |

---

## REST API

| Method | Path | What |
|---|---|---|
| GET | `/api/phones` | List all phones + status |
| POST | `/api/identity/reset` | Full reset one phone (IP + ID + MAC) |
| POST | `/api/identity/reset_all` | Full reset all running phones |
| POST | `/api/groups/clone` | Clone Phone 1's steps to all phones in a group |
| POST | `/api/groups/phone_steps` | Get or set per-phone steps |
| POST | `/api/groups/run` | Run all groups (per-phone steps) |
| POST | `/api/groups/stop` | Stop all groups |
| POST | `/api/open_url` | Open URL on all phones |