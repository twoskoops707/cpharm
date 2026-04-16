@echo off
title CPharm - Install App
color 0A

set LDPLAYER="C:\LDPlayer\LDPlayer9\ldconsole.exe"
set APKDIR=%~dp0..\apks

echo.
echo  ==========================================
echo   CPharm - Install App on All Phones
echo  ==========================================
echo.

:: Find APK
for %%f in (%APKDIR%\*.apk) set APK=%%f
if not defined APK (
    echo  [!] No APK found in apks\ folder.
    echo  [!] Drop your .apk file in the apks\ folder and try again.
    pause
    exit /b 1
)

echo  [*] Found APK: %APK%
echo.

:: Get list of all instances
for /f "tokens=1 delims=," %%i in ('%LDPLAYER% list2') do (
    set INDEX=%%i
    echo  [+] Installing on phone !INDEX!...
    %LDPLAYER% installapp --index !INDEX! --filename "%APK%"
    timeout /t 5 /nobreak >nul
)

echo.
echo  ==========================================
echo   App installed on all phones!
echo   Next: Run scripts\launch_all.bat
echo  ==========================================
echo.
pause
