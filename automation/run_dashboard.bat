@echo off
REM Double-click or run from cmd — starts the web dashboard (dashboard.py)
REM Optional env: CPHARM_HOST, CPHARM_PORT, CPHARM_WS_PORT (see dashboard.py docstring)
if not defined CPHARM_HOST  set "CPHARM_HOST=127.0.0.1"
if not defined CPHARM_PORT  set "CPHARM_PORT=8080"

cd /d "%~dp0"
where py >nul 2>&1 && (
  py -3 dashboard.py
  exit /b %ERRORLEVEL%
)
python dashboard.py
exit /b %ERRORLEVEL%
