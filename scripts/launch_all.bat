@echo off
setlocal EnableDelayedExpansion
title CPharm - Launch All Phones
color 0A

set LDPLAYER="C:\LDPlayer\LDPlayer9\ldconsole.exe"

echo.
echo  ==========================================
echo   CPharm - Launching All Phones
echo  ==========================================
echo.

if not exist %LDPLAYER% (
    echo  [!] LDPlayer not found at C:\LDPlayer\LDPlayer9\
    echo  [!] Please install LDPlayer 9 from ldplayer.net first.
    pause
    exit /b 1
)

set COUNT=0
for /f "tokens=1,2 delims=," %%i in ('%LDPLAYER% list2') do (
    set INDEX=%%i
    set NAME=%%j
    if /i "!NAME:~0,6!"=="CPharm" (
        set /a COUNT+=1
        echo  [+] Starting !NAME! (index !INDEX!)...
        %LDPLAYER% launch --index !INDEX!
        timeout /t 3 /nobreak >nul
    )
)

if %COUNT%==0 (
    echo  [!] No CPharm phones found.
    echo  [!] Run scripts\clone_phones.bat first to create phones.
    pause
    exit /b 1
)

echo.
echo  ==========================================
echo   %COUNT% phones launched!
echo.
echo   Dashboard: run automation\start_dashboard.bat
echo   then open http://localhost:8080
echo  ==========================================
echo.
pause
