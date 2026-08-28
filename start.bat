@echo off
REM Plutus - double-click to install anything missing and start the app.
REM Bypass is scoped to this one process: it does not change machine policy,
REM which is otherwise the usual reason a downloaded script refuses to run.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap.ps1" %*
if errorlevel 1 (
  echo.
  echo Setup did not finish. The error above says why.
  pause
)
