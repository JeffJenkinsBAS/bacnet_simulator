@echo off
setlocal
cd /d "%~dp0\.."

echo ============================================================
echo  Auto-start via Task Scheduler (fallback, no NSSM needed)
echo ============================================================
echo  This is NOT a true Windows Service -- it will not appear in
echo  services.msc. It DOES start automatically at boot, before
echo  login, and restarts are handled by Task Scheduler's own retry
echo  settings rather than NSSM's. Use install_service.bat instead
echo  if NSSM is available -- see PACKAGING.md for the tradeoffs.
echo ============================================================
echo.
echo  This must be run as Administrator. If it fails below, close
echo  this window and re-run by right-clicking this file and
echo  choosing "Run as administrator".
echo.

if not exist venv\Scripts\python.exe (
    echo ERROR: virtual environment not found.
    echo Run scripts\windows\install.bat ^(or install_offline.bat^) first.
    pause
    exit /b 1
)

set TASK_NAME=ACIBACnetSimulator
set PROJECT_DIR=%cd%
set PYTHON_EXE=%PROJECT_DIR%\venv\Scripts\python.exe

echo Creating scheduled task "%TASK_NAME%" ...
schtasks /Create /TN "%TASK_NAME%" /TR "\"%PYTHON_EXE%\" -m app.main" /SC ONSTART /RU SYSTEM /RL HIGHEST /F

if errorlevel 1 (
    echo.
    echo ERROR: failed to create the scheduled task. Most likely cause:
    echo this window is not running as Administrator. Right-click this
    echo file and choose "Run as administrator", then try again.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Done. The simulator will start automatically at every boot,
echo  running as SYSTEM ^(no login required^), working directory
echo  set to: %PROJECT_DIR%
echo.
echo  To remove it later:
echo      schtasks /Delete /TN "%TASK_NAME%" /F
echo.
echo  Same firewall caveat as the service method: no interactive
echo  popup will appear. Add the manual firewall rule from
echo  PACKAGING.md before relying on this for real bench traffic.
echo ============================================================
pause
