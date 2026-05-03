@echo off
setlocal EnableDelayedExpansion
title CPharm - GitHub Setup
color 0A

echo.
echo  ==========================================
echo   CPharm - Push to GitHub
echo  ==========================================
echo.

:: Find gh.exe (check PATH first, then common winget/scoop locations)
set GH=
for %%G in (gh.exe) do set GH=%%~$PATH:G
if not defined GH (
    for %%D in (
        "%ProgramFiles%\GitHub CLI\gh.exe"
        "%LOCALAPPDATA%\Programs\GitHub CLI\gh.exe"
        "%LOCALAPPDATA%\Microsoft\WinGet\Packages\GitHub.cli_Microsoft.Winget.Source_8wekyb3d8bbwe\gh.exe"
        "%USERPROFILE%\scoop\shims\gh.exe"
        "%USERPROFILE%\.local\bin\gh.exe"
    ) do (
        if exist %%D set GH=%%D
    )
)

:: Wildcard search in WinGet packages folder as fallback
if not defined GH (
    for /r "%LOCALAPPDATA%\Microsoft\WinGet\Packages" %%F in (gh.exe) do set GH=%%F
)

if not defined GH (
    echo  [*] GitHub CLI not found. Installing...
    winget install GitHub.cli -e --silent --accept-package-agreements --accept-source-agreements
    :: Re-search after install
    for /r "%LOCALAPPDATA%\Microsoft\WinGet\Packages" %%F in (gh.exe) do set GH=%%F
)

if not defined GH (
    echo  [!] Could not find gh.exe after install.
    echo  [!] Please install manually from: https://cli.github.com
    pause
    exit /b 1
)

echo  [*] Found gh at: %GH%
echo.

:: Check if already authenticated
"%GH%" auth status >nul 2>&1
if errorlevel 1 (
    echo  [1] Opening GitHub login in browser...
    "%GH%" auth login --web --git-protocol https
)

echo.
echo  [2] Creating GitHub repo and pushing...
cd /d "%~dp0"

:: Check if remote already exists
git remote get-url origin >nul 2>&1
if not errorlevel 1 (
    echo  [*] Remote already set. Pushing...
    "%GH%" auth setup-git
    git push -u origin master
) else (
    "%GH%" repo create cpharm --public ^
        --description "Virtual Android phone farm — LDPlayer 9 on Windows" ^
        --source=. --remote=origin --push
)

echo.
echo  ==========================================
echo   Done! CPharm is live on GitHub.
echo   Run: gh repo view --web
echo  ==========================================
echo.
pause
