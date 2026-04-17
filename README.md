# CPharm — Virtual Android Phone Farm

Run many Android phones at once and test your app or website across all of them. Control everything from your PC browser or your phone.

---

## What You Need

- A Windows 10 or 11 PC
- At least 8GB RAM (16GB+ if you want to run more phones)
- LDPlayer 9 installed — get it free at **ldplayer.net**
- Python 3.10 or newer — get it free at **python.org**

---

## One-Time Setup

### 1. Install LDPlayer

1. Go to **ldplayer.net** and download LDPlayer 9
2. Run the installer
3. Open LDPlayer once, let it finish loading, then close it

### 2. Install Tor (for the "look different" feature)

1. Go to **torproject.org/download** → scroll down to **Tor Expert Bundle**
2. Download and unzip it
3. Copy the contents into the `automation\tor\` folder inside CPharm

> Skip this step if you don't need each phone to appear from a different location.

### 3. Install Python packages

Open a command prompt in the CPharm folder and run:

```
pip install -r requirements.txt
```

### 4. Set up your master phone

```
Double-click: setup\1_setup_master.bat
```

This opens LDPlayer and configures Phone 0 as the base phone everything else copies from.

---

## Every Day Use

### Start the dashboard

```
Double-click: START_HERE.bat
```

Then open your browser and go to:

- **On this PC:** `http://localhost:8080`
- **On your phone:** `http://YOUR-PC-IP:8080`

To find your PC's IP address, run `scripts\find_my_ip.bat`

---

## What You Can Do From the Dashboard

### Your Phones
See all your phones at a glance — green means on, grey means off. Start or stop any phone with one tap. Use **New Phone** to clone another copy.

### Install an App
Drop an APK file onto the box (or tap to pick one). Hit **Install All** and it installs on every phone. The dashboard tracks which version is on each phone.

### Open a Website
Paste any web address and tap **Open**. Choose to open it on all phones at the same time, or stagger it — one phone every 1 minute, 5 minutes, or a custom delay you set.

### Teach Mode
Show the farm what to do by doing it once on Phone 1:

1. Tap **Start Recording**
2. Do your steps on Phone 1 — open a site, tap around, fill in forms, anything
3. Tap **Stop**
4. Tap **Play on All Phones** — every other phone follows the same steps, one after another

Set the wait time between each phone using the seconds field.

### Make Each Phone Look Different
Tap **Set Up Now** and each phone gets its own internet address (routed through Tor) so websites see them as separate people from different countries. Each phone also gets a unique device ID automatically.

---

## Folder Structure

```
CPharm/
  apks/              ← Drop your APK files here
  automation/
    dashboard.py     ← The server (runs in background)
    dashboard.html   ← The dashboard you see in your browser
    tor/             ← Put Tor Expert Bundle files here
    recordings/      ← Teach Mode recordings are saved here
  setup/             ← First-time setup scripts
  scripts/           ← Handy scripts (clone, launch, find IP)
  gui/               ← Desktop app (optional, Windows only)
  START_HERE.bat     ← Start here every time
  requirements.txt   ← Python packages needed
```

---

## Branches

| Branch | Description |
|--------|-------------|
| `master` | Original version |
| `v2` | Current version — real-time dashboard, Teach Mode, Tor, URL launcher |
