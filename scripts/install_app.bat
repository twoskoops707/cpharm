@echo off
setlocal EnableDelayedExpansion
title CPharm - Install App
color 0A

set LDPLAYER="C:\LDPlayer\LDPlayer9\ldconsole.exe"
set APKDIR=%~dp0..\apks

echo.
echo  ==========================================
echo   CPharm - Install App on Master Phone
echo  ==========================================
echo.

if not exist %LDPLAYER% (
    echo  [!] LDPlayer not found. Install from ldplayer.net
    pause & exit /b 1
)

:: Find APK (BUG FIX: was missing 'do' keyword)
set APK=
for %%f in ("%APKDIR%\*.apk") do set APK=%%f

if not defined APK (
    echo  [!] No APK found in apks\ folder.
    echo  [!] Drop your .apk file there and try again.
    pause & exit /b 1
)

echo  [*] Found: %APK%
echo  [*] Installing on master phone (index 0)...
%LDPLAYER% installapp --index 0 --filename "%APK%"

echo.
echo  ==========================================
echo   Done! Now run scripts\clone_phones.bat
echo   to copy this setup to all phones.
echo  ==========================================
echo.
pause
