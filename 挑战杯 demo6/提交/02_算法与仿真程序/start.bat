@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "VENV_PY=%~dp0..\.venv\Scripts\python.exe"

if exist "%VENV_PY%" (
    "%VENV_PY%" "%~dp0start.py" %*
    goto :end
)

where python >nul 2>nul
if %errorlevel% equ 0 (
    python "%~dp0start.py" %*
    goto :end
)

where py >nul 2>nul
if %errorlevel% equ 0 (
    py "%~dp0start.py" %*
    goto :end
)

echo [ERROR] Python not found. Install Python 3.10+ first.
echo         https://www.python.org/downloads/

:end
pause
