@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build-report.ps1"
if errorlevel 1 exit /b 1
endlocal
