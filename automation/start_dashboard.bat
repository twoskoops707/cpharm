@echo off
setlocal EnableDelayedExpansion
title CPharm - Dashboard
color 0A

echo.
echo  ==========================================
echo   CPharm - Starting Web Dashboard
echo  ==========================================
echo.

:: Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo  [!] Python not found. Installing via winget...
    winget install Python.Python.3.12 -e --silent
    echo  [!] Restart this script after Python installs.
    pause
    exit /b 1
)

echo  [*] Dashboard starting on port 8080...
echo  [*] Open on this PC:   http://localhost:8080
echo.

:: Get local IP and show it
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4" ^| findstr /v "127.0.0.1"') do (
    set IP=%%a
    set IP=!IP: =!
    echo  [*] Open on your phone: http://!IP!:8080
)

echo.
echo  Press Ctrl+C to stop the dashboard.
echo.

python "%~dp0dashboard.py"
pause
