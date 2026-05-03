@echo off
setlocal EnableDelayedExpansion
title CPharm - Dashboard
color 0A

:: Headless / unattended: optional env (child python reads these; pass-through if already set)
if not defined CPHARM_HOST  set "CPHARM_HOST=127.0.0.1"
if not defined CPHARM_PORT  set "CPHARM_PORT=8080"
:: CPHARM_WS_PORT: unset = dashboard.py derives from config offset (e.g. 8081 when HTTP is 8080)
:: CPHARM_UNATTENDED=1: skip final pause (Task Scheduler, services, CI)

:: Always run from this script's folder (dashboard imports rely on cwd / sibling files)
cd /d "%~dp0"

echo.
echo  ==========================================
echo   CPharm - Starting Web Dashboard
echo  ==========================================
echo.

where py >nul 2>&1 (
  py -3 --version >nul 2>&1
  if errorlevel 1 goto try_python
  set "_RUN=py -3"
  goto have_python
)
:try_python
python --version >nul 2>&1
if errorlevel 1 (
  echo  [!] Python not found. Installing via winget...
  winget install Python.Python.3.12 -e --silent
  echo  [!] Restart this script after Python installs.
  pause
  exit /b 1
)
set "_RUN=python"

:have_python
echo  [*] Host %CPHARM_HOST%  HTTP port %CPHARM_PORT%
echo  [*] Open on this PC:   http://localhost:%CPHARM_PORT%
echo.

for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4" ^| findstr /v "127.0.0.1"') do (
  set IP=%%a
  set IP=!IP: =!
  echo  [*] Open on your phone: http://!IP!:%CPHARM_PORT%
)

echo.
echo  Press Ctrl+C to stop the dashboard.
echo.

%_RUN% "%~dp0dashboard.py"
set "EX=%ERRORLEVEL%"
if %EX% neq 0 echo  [!] dashboard.py exited with code %EX%
if /i "%CPHARM_UNATTENDED%"=="1" exit /b %EX%
pause
exit /b %EX%
