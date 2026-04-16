# CPharm - Virtual Android Phone Farm

Test your app and ads across many virtual Android phones at once.
Simple enough for anyone to set up in under 10 minutes.

---

## What You Need (One-Time Setup)

- A Windows PC (Windows 10 or 11)
- At least 8GB RAM (16GB+ recommended for more phones)
- Your app's APK file

---

## Step 1 — Install LDPlayer

1. Go to **ldplayer.net** and download LDPlayer 9
2. Run the installer and follow the prompts
3. Open LDPlayer once — let it finish setting up
4. Close it when done

---

## Step 2 — Set Up Your Master Phone

Run this script to configure the master phone:

```
Double-click: setup\1_setup_master.bat
```

This will:
- Open LDPlayer
- Set it to landscape/portrait mode
- Enable ADB (needed for automation)

---

## Step 3 — Install Your App

1. Put your APK file in the `apks\` folder
2. Double-click: `scripts\install_app.bat`
3. Your app will install on the master phone

---

## Step 4 — Clone as Many Phones as You Want

```
Double-click: scripts\clone_phones.bat
```

Enter how many copies you want when asked.
Each clone is a full copy of your master phone with your app already installed.

---

## Step 5 — Launch All Phones & Start Testing

```
Double-click: scripts\launch_all.bat
```

All phones launch and your app starts automatically on each one.

---

## Access From Your Phone (Remote Dashboard)

Open your phone browser and go to:
```
http://YOUR-PC-IP:8080
```

You'll see a dashboard to start/stop phones from your phone.

To find your PC's IP: run `scripts\find_my_ip.bat`

---

## Folder Structure

```
CPharm/
  apks/          <- Put your APK here
  setup/         <- First-time setup scripts
  scripts/       <- Daily use scripts
  automation/    <- Advanced automation
```
