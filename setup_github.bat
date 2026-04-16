@echo off
title CPharm - GitHub Setup
color 0A

echo.
echo  ==========================================
echo   CPharm - Push to GitHub
echo  ==========================================
echo.

:: Install gh CLI if not present
where gh >nul 2>&1
if errorlevel 1 (
    echo  [*] Installing GitHub CLI...
    winget install GitHub.cli -e
    echo  [*] Restart this script after install completes.
    pause
    exit /b
)

echo  [1] Logging into GitHub (browser will open)...
gh auth login

echo.
echo  [2] Creating GitHub repo...
gh repo create cpharm --public --description "Virtual Android phone farm - LDPlayer 9 on Windows" --source=. --remote=origin --push

echo.
echo  ==========================================
echo   Done! Repo is live on GitHub.
echo   Future pushes: git push
echo  ==========================================
echo.
pause
