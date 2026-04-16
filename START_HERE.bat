@echo off
title CPharm - Phone Farm Setup
color 0A
cls
echo.
echo  =====================================================
echo    CPharm  ^|  Virtual Android Phone Farm
echo    Setup ^& Launch
echo  =====================================================
echo.

:: ── Step 1: Python ──────────────────────────────────────────────────────────
echo  [1/4] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo  [!] Python not found. Installing via winget...
    winget install --id Python.Python.3.12 -e --silent --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo  [!] Auto-install failed. Please go to python.org and install Python 3.
        echo      Then run this file again.
        pause
        exit /b 1
    )
    echo  [+] Python installed.
    :: Refresh PATH
    set "PATH=%PATH%;%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts"
)
echo  [+] Python OK

:: ── Step 2: Python packages ─────────────────────────────────────────────────
echo  [2/4] Installing required packages...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet customtkinter
echo  [+] Packages OK

:: ── Step 3: LDPlayer check ──────────────────────────────────────────────────
echo  [3/4] Checking LDPlayer 9...
if not exist "C:\LDPlayer\LDPlayer9\ldconsole.exe" (
    echo.
    echo  [!] LDPlayer 9 is not installed.
    echo.
    echo      LDPlayer is the free Android emulator this app uses.
    echo      It runs Android phones on your PC.
    echo.
    echo      STEP: Install LDPlayer 9 from  ldplayer.net
    echo            ^(free, ~500MB download^)
    echo.
    echo      After installing, run START_HERE.bat again.
    echo.
    start "" "https://www.ldplayer.net/"
    pause
    exit /b 1
)
echo  [+] LDPlayer OK

:: ── Step 4: Create master phone if not exists ────────────────────────────────
echo  [4/4] Preparing master phone...
set LD="C:\LDPlayer\LDPlayer9\ldconsole.exe"
%LD% list2 2>nul | find "0," >nul 2>&1
if errorlevel 1 (
    echo  Creating master phone...
    %LD% modify --index 0 --resolution 540,960,240 --cpu 2 --memory 1024
)
echo  [+] Master phone ready

:: ── Create apks folder ───────────────────────────────────────────────────────
if not exist "%~dp0apks" mkdir "%~dp0apks"

:: ── Launch GUI ───────────────────────────────────────────────────────────────
echo.
echo  =====================================================
echo    All done!  Launching CPharm...
echo  =====================================================
echo.
cd /d "%~dp0"
start "" pythonw gui\cpharm_gui.py
timeout /t 2 /nobreak >nul
exit
