@echo off
setlocal EnableDelayedExpansion
title CPharm - Dashboard
color 0A

:: Same cwd semantics as run_dashboard.bat — dashboard.py and HTML live next to this file
cd /d "%~dp0"

echo.
echo  ==========================================
echo   CPharm - Starting Web Dashboard
echo  ==========================================
echo.

where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
  set "_RUN=py -3"
) else (
  where python >nul 2>&1
  if %ERRORLEVEL% NEQ 0 (
    echo  [!] Python not found. Installing via winget...
    winget install Python.Python.3.12 -e --silent
    echo  [!] Restart this script after Python installs.
    pause
    exit /b 1
  )
  set "_RUN=python"
)

echo  [*] Dashboard starting on port 8080...
echo  [*] Open on this PC:   http://localhost:8080
echo.

for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4" ^| findstr /v "127.0.0.1"') do (
  set IP=%%a
  set IP=!IP: =!
  echo  [*] Open on your phone: http://!IP!:8080
)

echo.
echo  Press Ctrl+C to stop the dashboard.
echo.

%_RUN% "%~dp0dashboard.py"
set "EX=!ERRORLEVEL!"
if !EX! neq 0 echo  [!] dashboard.py exited with code !EX!
pause
exit /b !EX!
