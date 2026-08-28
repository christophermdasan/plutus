@echo off
REM Plutus - stop the app and its data services. Your data is kept.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap.ps1" -Stop
pause
