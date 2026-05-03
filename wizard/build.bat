@echo off
echo ============================================================
echo  CPharm Wizard Builder
echo  This turns setup_wizard.py into a standalone CPharmSetup.exe
echo  You only need to run this once on a Windows PC.
echo ============================================================
echo.

echo Step 1 of 3: Checking Python is installed...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found.
    echo.
    echo Please install Python first:
    echo   1. Go to https://www.python.org/downloads/
    echo   2. Download the latest Python 3 installer
    echo   3. Run it. IMPORTANT: tick "Add Python to PATH" before clicking Install.
    echo   4. Come back and run this file again.
    pause
    exit /b 1
)
echo    Found Python. Good.
echo.

echo Step 2 of 3: Installing build tools...
python -m pip install --upgrade pyinstaller pillow
if errorlevel 1 (
    echo ERROR: pip install failed. Check your internet connection.
    pause
    exit /b 1
)
echo    Build tools installed. Good.
echo.

echo Step 3 of 3: Building CPharmSetup.exe ...
cd /d "%~dp0"
rem Produces a .exe matching this machine's Python (x64 on typical PCs; native ARM64 on Windows on ARM if Python is arm64).
pyinstaller --onefile --windowed --name CPharmSetup --hidden-import wizard_theme --add-data "assets;assets" setup_wizard.py
if errorlevel 1 (
    echo ERROR: Build failed. Read the error above.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  SUCCESS!
echo  Your .exe is at:  dist\CPharmSetup.exe
echo  Copy it to any Windows PC and double-click to run.
echo ============================================================
echo.
pause
