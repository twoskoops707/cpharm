# CPharm — Virtual Android Phone Farm

Run many Android phones at once and test your app or website across all of them.
Control everything from your PC browser or your phone.

---

## What You Need

- A Windows 10 or 11 PC **or** a Kali Linux machine
- At least 8 GB RAM (16 GB+ for more phones)
- Internet connection for the one-time setup

---

## Step 1 — Get the Code

Open a terminal (or Command Prompt) and run:

```
git clone https://github.com/twoskoops707/cpharm.git
cd cpharm
git checkout v2
```

This downloads CPharm and switches to the latest version.

---

## Step 2 — Install LDPlayer (Windows only)

> Kali Linux users — skip to the Kali section below.

1. Go to **ldplayer.net**
2. Download **LDPlayer 9** and run the installer
3. Open LDPlayer once, let it finish loading, then close it

---

## Step 3 — Install Python

**Windows:**
1. Go to **python.org/downloads**
2. Download Python 3.11 or newer
3. Run the installer — check the box that says **"Add Python to PATH"**

**Kali Linux:**
```bash
sudo apt update && sudo apt install python3 python3-pip -y
```

---

## Step 4 — Install the Required Packages

**Windows** — double-click `START_HERE.bat` and it handles this automatically.

**Kali Linux:**
```bash
pip3 install -r requirements.txt
```

---

## Step 5 — Set Up Tor (for the "look different" feature)

You have two options — pick the easiest one:

### Option A — Use Tor Browser (easiest, already have it)
If Tor Browser is already installed and open on your machine, CPharm will use it automatically. Nothing else needed.

### Option B — Download Tor Expert Bundle (Windows, no Tor Browser)
1. Go to **torproject.org/download** → scroll to **Tor Expert Bundle**
2. Download and unzip it
3. Copy the contents into the `automation\tor\` folder inside CPharm

### Option C — Kali Linux (Tor is already installed)
```bash
sudo apt install tor -y
sudo service tor start
```
CPharm will detect it automatically.

---

## Step 6 — Start CPharm

**Windows:**
```
Double-click START_HERE.bat
```

**Kali Linux:**
```bash
cd automation
python3 dashboard.py
```

---

## Step 7 — Open the Dashboard

Once it's running, open your browser and go to:

- **On this PC:** `http://localhost:8080`
- **On your phone:** `http://YOUR-PC-IP:8080`

To find your PC's IP address: run `scripts\find_my_ip.bat` on Windows, or run `ip a` on Kali.

---

## What You Can Do

### Your Phones
All your virtual phones appear as cards. Green = on, grey = off.
Tap **Start** or **Stop** on any card. Use **New Phone** to clone another one.

### Install an App
Drop an APK file onto the box (or tap to pick one). Tap **Install All** — it uploads the file and installs it on every phone. The dashboard tracks which version is on each phone.

### Open a Website
Paste any web address and tap **Open**. Choose:
- **All at once** — every phone opens it at the same time
- **1 min / 5 min / Custom** — phones open it one by one with a gap between each

### Teach Mode
Show the farm what to do by doing it once on Phone 1:
1. Tap **Start Recording**
2. Do your steps on Phone 1 (open a site, tap around, fill in forms, anything)
3. Tap **Stop**
4. Tap **Play on All Phones** — every other phone copies those steps one after another

Set how many seconds to wait between each phone.

### Make Each Phone Look Different
Tap **Set Up Now** — each phone gets routed through Tor so every phone appears to come from a different country. Each phone also gets a unique device ID. To websites, they look like completely different people.

---

## Folder Layout

```
cpharm/
  apks/              ← Drop APK files here (or use the dashboard)
  automation/
    dashboard.py     ← The server
    dashboard.html   ← The page you see in the browser
    tor/             ← Tor Expert Bundle goes here (if using Option B above)
    recordings/      ← Teach Mode recordings are saved here
    config.py        ← Settings (LDPlayer path, ports)
  setup/             ← First-time setup scripts
  scripts/           ← Helper scripts (clone phones, find IP, etc.)
  START_HERE.bat     ← Start here every time (Windows)
  requirements.txt   ← Python packages
```

---

## Kali Linux Notes

LDPlayer does not run on Linux. On Kali, CPharm works as a **controller and test runner** — you point it at an Android device connected via ADB (USB or network) or use an Android emulator like **Waydroid**:

```bash
# Install Waydroid (Android on Linux)
sudo apt install waydroid -y
sudo waydroid init
waydroid session start
```

Then ADB connects to it at `localhost:5555`:
```bash
adb connect localhost:5555
```

CPharm will detect and control it the same way it controls LDPlayer phones.

---

## Branches

| Branch | What it is |
|--------|------------|
| `master` | Original version |
| `v2` | Current version — use this one |
