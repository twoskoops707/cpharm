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
echo  [1/3] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo  [!] Python not found. Installing via winget...
    winget install --id Python.Python.3.12 -e --silent --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo  [!] Auto-install failed. Please install Python 3 from python.org
        echo      then run this file again.
        pause
        exit /b 1
    )
    echo  [+] Python installed.
    set "PATH=%PATH%;%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts"
)
echo  [+] Python OK

:: ── Step 2: Required packages ────────────────────────────────────────────────
echo  [2/3] Installing required packages...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet "websockets>=12.0" "psutil>=5.9.0" "requests"
if errorlevel 1 (
    echo  [!] Package install failed. Check your internet connection.
    pause
    exit /b 1
)
echo  [+] Packages OK

:: ── Step 3: Launch wizard ────────────────────────────────────────────────────
echo  [3/3] Launching CPharm Setup Wizard...
echo.
echo  =====================================================
echo    CPharm is starting - a window will open shortly
echo  =====================================================
echo.
cd /d "%~dp0"
if exist "wizard\setup_wizard.py" (
    start "" pythonw wizard\setup_wizard.py
) else (
    echo  [!] wizard\setup_wizard.py not found.
    echo      Make sure you are running this from the CPharm folder.
    pause
    exit /b 1
)
timeout /t 2 /nobreak >nul
exit
