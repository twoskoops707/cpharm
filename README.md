# CPharm — Virtual Phone Farm

Run a bunch of fake Android phones on your Windows PC and control them all from your browser — even from your real phone.

---

## Before You Start — What You Need

- A Windows 10 or 11 PC
- At least 8 GB of RAM (16 GB is better — each fake phone uses about 1.5 GB)
- An internet connection for the one-time setup

---

## Step 1 — Install Git (so you can download the code)

1. Go to **git-scm.com/download/win**
2. Click the big download button
3. Run the installer — just keep clicking **Next** on every screen, don't change anything
4. When it's done, close the installer

---

## Step 2 — Download CPharm

1. Press the **Windows key** on your keyboard
2. Type **cmd** and press **Enter** — a black window opens
3. Copy and paste this exactly, then press **Enter**:

```
git clone https://github.com/twoskoops707/cpharm.git
```

4. Wait for it to finish — you'll see files downloading
5. Now type this and press **Enter**:

```
cd cpharm
```

You're now inside the CPharm folder.

---

## Step 3 — Install LDPlayer (the fake phone software)

1. Open your browser and go to **ldplayer.net**
2. Click the big green **Download** button — download **LDPlayer 9**
3. Run the file that downloaded and click through the installer
4. LDPlayer will open by itself — wait for it to finish loading (you'll see a home screen)
5. Close LDPlayer — you don't need it open right now

---

## Step 4 — Install Python (runs the dashboard)

1. Go to **python.org/downloads**
2. Click the big yellow **Download Python** button
3. Run the installer
4. **IMPORTANT:** Before clicking Install, check the box that says **"Add Python to PATH"** at the bottom — if you miss this, nothing will work
5. Click **Install Now** and wait for it to finish

---

## Step 5 — Install the Required Pieces

Still in that black cmd window from Step 2? Good.

Type this and press **Enter**:

```
pip install -r requirements.txt
```

Wait for it to finish. You'll see a bunch of text scrolling — that's normal.

---

## Step 6 — Set Up Tor (makes each phone look like a different person)

Pick ONE of these. Option A is easiest.

### Option A — Tor Browser (use this one)

1. Go to **torproject.org**
2. Download and install **Tor Browser**
3. Open Tor Browser and click **Connect**
4. Leave it open and running in the background — CPharm uses it automatically

### Option B — No browser? Use the Expert Bundle

1. Go to **torproject.org/download**
2. Scroll down to **Tor Expert Bundle** — download the Windows version
3. Unzip the file
4. Copy everything inside into the `automation\tor\` folder inside cpharm
   - The file `tor.exe` should end up at `cpharm\automation\tor\tor.exe`

---

## Step 7 — Start CPharm

In the black cmd window, type this and press **Enter**:

```
cd automation
python dashboard.py
```

You'll see something like:

```
  ╔══════════════════════════════════════════╗
  ║   CPharm  •  ready                       ║
  ║                                          ║
  ║   On this PC:  http://localhost:8080     ║
  ║   On phone:    http://192.168.1.x:8080  ║
  ╚══════════════════════════════════════════╝
```

**Do not close this window.** It needs to stay open the whole time CPharm is running.

---

## Step 8 — Open the Dashboard

Open any browser (Chrome, Edge, Firefox) and go to:

```
http://localhost:8080
```

You'll see the CPharm dashboard.

**From your phone:** Connect your phone to the same WiFi as your PC, then open your phone's browser and type the address shown on the "On phone:" line in the cmd window.

---

## What You Can Do

### Create Phones
Tap **New Phone** to add a virtual phone. Each one shows as a card.
Green = on. Grey = off. Tap **Start** / **Stop** to control each one.

### Install an App
Drop your `.apk` file onto the box (or tap it to pick a file), then tap **Install All**.
The app installs on every phone. The dashboard tracks which version each phone has.

### Open a Website on All Phones
Paste a web address and tap **Open**. Choose:
- **All at once** — every phone opens it at the same time
- **1 min / 5 min / Custom** — phones open it one by one with a gap in between

### Teach Mode — Show it Once, it Does the Rest
1. Make sure Phone 1 is on
2. Tap **Start Recording**
3. Do your steps on Phone 1 — tap around, fill in forms, open things
4. Tap **Stop**
5. Tap **Play on All Phones** — every other phone copies what you did, one at a time

### Make Each Phone Look Different
Tap **Set Up Now** in the locations section.
Each phone gets routed through Tor and gets a unique device ID — to websites, they look like completely different people from different countries.

---

## App Tester (Pre-Launch Testing)

Tap **App Tester** in the top bar. This opens a separate page for testing your app before you publish it to the Play Store.

**Launch App** — type your app's package name and open it on all phones at once.

**Type on Phones** — send keyboard input to all phones or just one. Great for filling in forms, typing search queries, anything that needs a keyboard.

**Quick Actions** — one-tap buttons: swipe up/down/left/right, take a screenshot, go home, lock/wake the screen, clear recent apps. Runs on all phones at once.

**Test Loop** — phones automatically switch between your website and your app on a timer, repeating as many times as you want. Good for testing ad impressions and app behavior at the same time.

**Pre-Launch Checklist** — a list of things Google checks before approving your app. Tap each item to mark it done. Your progress saves automatically.

**RAM Bar** — shows how much of your computer's memory is in use. Yellow = getting full. Red = stop some phones now before your computer freezes.

> **To find your package name:** Open Android Studio and look in your `build.gradle` for `applicationId`. It looks like `com.yourname.app`.

---

## How Many Phones Can You Run?

| PC RAM | Safe limit |
|--------|-----------|
| 8 GB   | 3 phones  |
| 16 GB  | 8 phones  |
| 32 GB  | 18 phones |

The App Tester page shows a live RAM bar and tells you your exact safe limit. Stop some phones if it turns yellow.

---

## Every Time You Come Back

You don't reinstall anything. Just do this:

1. Open a cmd window
2. Type:
```
cd cpharm\automation
python dashboard.py
```
3. Open your browser to `http://localhost:8080`

---

## Folder Layout

```
cpharm/
  apks/              ← Drop APK files here
  automation/
    dashboard.py     ← The server — run this every time
    dashboard.html   ← Main dashboard page
    playstore.html   ← App Tester page
    playstore.py     ← App testing backend
    tor_manager.py   ← Tor and identity spoofing
    teach.py         ← Teach Mode recording
    config.py        ← Paths and ports
    tor/             ← Tor Expert Bundle goes here (Option B only)
    recordings/      ← Saved Teach Mode sessions
  requirements.txt   ← Python packages
  README.md          ← This file
```

---

## Something Went Wrong?

| Problem | Fix |
|---------|-----|
| `python` not found | Reinstall Python — check "Add Python to PATH" this time |
| `pip` not found | Same fix as above |
| Dashboard won't open in browser | Make sure `python dashboard.py` is still running in the cmd window |
| No phones showing up | Open LDPlayer first, then refresh the dashboard |
| Can't reach it from your phone | Your phone and PC must be on the same WiFi |
| Everything is slow or freezing | Too many phones on — stop some from the dashboard |
