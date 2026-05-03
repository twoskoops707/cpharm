@echo off
setlocal EnableDelayedExpansion
title CPharm - Find My IP
color 0A

echo.
echo  ==========================================
echo   CPharm - Your PC's Local IP Address
echo  ==========================================
echo.

for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4" ^| findstr /v "127.0.0.1"') do (
    set IP=%%a
    set IP=!IP: =!
    echo  Your PC IP:  !IP!
    echo.
    echo  Dashboard URL:  http://!IP!:8080
    echo.
)

echo  Open that URL on your phone's browser.
echo  (Make sure your phone is on the same WiFi)
echo.
pause
