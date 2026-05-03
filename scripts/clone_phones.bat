@echo off
setlocal EnableDelayedExpansion
title CPharm - Clone Phones
color 0A

set LDPLAYER="C:\LDPlayer\LDPlayer9\ldconsole.exe"

echo.
echo  ==========================================
echo   CPharm - Clone Master Phone
echo  ==========================================
echo.
echo  How many phones do you want total?
echo  (Each phone uses ~1.5GB RAM)
echo.
set /p COUNT="  Enter number of phones: "

echo.
echo  [*] Creating %COUNT% phones from master...
echo.

for /l %%i in (1,1,%COUNT%) do (
    echo  [+] Creating phone %%i...
    %LDPLAYER% copy --name "CPharm-%%i" --from 0
    timeout /t 8 /nobreak >nul
    echo      Done.
)

echo.
echo  ==========================================
echo   %COUNT% phones created!
echo   Run scripts\launch_all.bat to start them.
echo  ==========================================
echo.
pause
