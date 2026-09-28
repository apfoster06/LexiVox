@echo off
rem Starts LexiVox. Double-click this file, or use the Start menu shortcut
rem (scripts\create-shortcut.ps1 makes one that points here).
setlocal
cd /d "%~dp0"

set "PYW=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%PYW%" (
    echo LexiVox isn't installed in this folder yet. Run this first:
    echo.
    echo   powershell -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1"
    echo.
    pause
    exit /b 1
)

rem pythonw has no console window; "start" returns immediately so this window closes.
start "" "%PYW%" "%~dp0launcher.py"
