@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>&1
if not errorlevel 1 (
    py -3 control_panel.py
) else (
    python control_panel.py
)

if errorlevel 1 (
    echo.
    echo Control panel failed to start.
    pause
)
