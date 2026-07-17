@echo off
setlocal

cd /d "%~dp0..\.."

if not exist venv (
    echo Virtual environment not found.
    echo Run scripts\windows\install.bat first ^(or install_offline.bat if this
    echo machine has no internet access^).
    pause
    exit /b 1
)

call venv\Scripts\activate.bat

echo ============================================================
echo  ACI BACnet Building Simulation Platform
echo ============================================================
echo  Dashboard:  http://127.0.0.1:8000
echo  BACnet:     see config\network.json for bind address / port
echo.
echo  This window IS the running application. Closing it, or
echo  pressing Ctrl+C in it, stops the simulator and every BACnet
echo  object it publishes. Minimize it, don't close it.
echo ============================================================
echo.

start "" http://127.0.0.1:8000

python -m app.main

echo.
echo Simulator stopped.
pause
