@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel% equ 0 (
    python restart_server.py
) else (
    where py >nul 2>nul
    if %errorlevel% equ 0 (
        py restart_server.py
    ) else (
        echo [ERROR] Python not found. Install Python 3.10+ first.
        echo         https://www.python.org/downloads/
    )
)
pause
