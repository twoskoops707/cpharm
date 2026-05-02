@echo off
REM Double-click or run from cmd — starts the web dashboard (dashboard.py)
cd /d "%~dp0"
where py >nul 2>&1 && (
  py -3 dashboard.py
  exit /b %ERRORLEVEL%
)
python dashboard.py
exit /b %ERRORLEVEL%
