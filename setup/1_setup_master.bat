@echo off
title CPharm - Master Phone Setup
color 0A
echo.
echo  ==========================================
echo   CPharm - Setting Up Master Phone
echo  ==========================================
echo.

:: Check if LDPlayer is installed
set LDPLAYER="C:\LDPlayer\LDPlayer9\ldconsole.exe"
if not exist %LDPLAYER% (
    echo  [!] LDPlayer not found at C:\LDPlayer\LDPlayer9\
    echo  [!] Please install LDPlayer 9 from ldplayer.net first.
    pause
    exit /b 1
)

echo  [1] Launching master phone (index 0)...
%LDPLAYER% launch --index 0
timeout /t 15 /nobreak >nul

echo  [2] Enabling ADB on master phone...
%LDPLAYER% adb --index 0 --command "shell settings put global adb_enabled 1"
timeout /t 3 /nobreak >nul

echo  [3] Setting recommended performance settings...
%LDPLAYER% modify --index 0 --resolution 540,960,240
%LDPLAYER% modify --index 0 --cpu 2
%LDPLAYER% modify --index 0 --memory 1024
timeout /t 3 /nobreak >nul

echo  [4] Connecting ADB...
adb connect 127.0.0.1:5554
timeout /t 5 /nobreak >nul

echo.
echo  ==========================================
echo   Master phone is ready!
echo   Next step: Run scripts\install_app.bat
echo  ==========================================
echo.
pause
