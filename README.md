# CPharm — Virtual Phone Farm

Run a bunch of virtual Android phones on your Windows PC and automate them all from one dashboard — no Android Studio, no emulator app, no manual setup.

---

## Requirements

- Windows 10 or 11 (64-bit)
- 16 GB RAM minimum — each virtual phone uses ~2 GB RAM and ~4 GB disk
- Internet connection for the one-time setup (~1.5 GB download total)

---

## Setup — Run the Wizard

Download **CPharmSetup.exe** from the [Releases](../../releases) page and run it.

The wizard walks you through 8 steps:

| Step | What it does |
|------|-------------|
| 1 — Install Android SDK | Downloads the Android SDK command-line tools automatically. No Android Studio needed. |
| 2 — Create Virtual Phones | Downloads Android 14 (~1 GB) and creates your virtual phones. One-time setup. |
| 3 — Start Phones | Boots the virtual phones. First boot takes 2–5 minutes per phone. |
| 4 — Google Play | How to set up closed testing so your app appears in the Play Store on each phone. |
| 5 — Groups | Split phones into groups, each with its own step sequence. |
| 6 — Launch | Start the automation server and run all groups from one screen. |

Everything is downloaded and configured automatically. Nothing needs to be installed manually before running the wizard.

---

## How Many Phones Can You Run?

| RAM | Safe limit |
|-----|-----------|
| 16 GB | 5 phones |
| 32 GB | 10 phones |

Start with 3. You can add more later.

---

## The Dashboard

Once the wizard completes, the automation server runs in the background and the wizard becomes your control panel. You can also open the web dashboard in any browser:

```
http://localhost:8080
```

From your phone: connect to the same WiFi as your PC and use the address shown in the wizard.

---

## Identity Rotation (Tor)

Each phone gets its own Tor circuit so every session looks like a different person from a different location.

**Option A — Tor Browser (easiest)**
1. Download and install Tor Browser from **torproject.org**
2. Open it and click Connect
3. Leave it running — CPharm uses it automatically

**Option B — Expert Bundle (no browser)**
1. Download the Tor Expert Bundle for Windows from **torproject.org/download**
2. Unzip it and copy everything into `automation\tor\` inside the CPharm folder
   - `tor.exe` should end up at `automation\tor\tor.exe` under your clone folder

---

## Folder Layout

```
cpharm-clone/          ← your project folder (name may vary)
  automation/
    dashboard.py      ← Automation server — started by the wizard
    tor_manager.py    ← Tor identity rotation
    scheduler.py      ← Daily hit scheduler (random fire-times per phone)
    teach.py          ← Record on one phone, replay on all
    playstore.py      ← Play Store automation
    config.py         ← Ports and paths
    tor/              ← Tor Expert Bundle (Option B)
    recordings/       ← Saved teach sessions and group configs
  wizard/
    setup_wizard.py   ← Setup wizard source
  requirements.txt
  README.md
```

---

## Running the Server Without the Wizard

The automation server file is **`automation\dashboard.py`** (there is no file named `dash`).

**Windows:** double-click **`automation\run_dashboard.bat`** or:

```bash
cd automation
py -3 dashboard.py
```

If `py` is not on your PATH, use `python dashboard.py` instead.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Wizard says "SDK not found" | Click the install button on Step 1 and wait for it to finish |
| Phones won't start | Make sure Step 1 (SDK) completed — emulator requires it |
| Dashboard won't open | Make sure the server was started from the wizard's Launch step |
| Can't reach from phone | PC and phone must be on the same WiFi |
| Out of memory | Stop some phones from the wizard and reduce your phone count |
