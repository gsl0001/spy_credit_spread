@echo off
setlocal EnableExtensions

set "ROOT=%~dp0"
set "PRESET=%~1"
if "%PRESET%"=="" set "PRESET=canary-moomoo"

cd /d "%ROOT%"

powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\paper-trading-day.ps1" -Preset "%PRESET%"
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
    echo Paper trading day script exited with code %RC%.
) else (
    echo Paper trading day script completed.
)
pause
exit /b %RC%
