@echo off
setlocal

echo ============================================================
echo  ACI BACnet Building Simulation Platform - Setup (Offline)
echo ============================================================
echo.

cd /d "%~dp0..\.."

where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python was not found on this system.
    echo Install Python 3.11 or later from a USB installer or internal
    echo software repository, checking "Add python.exe to PATH".
    pause
    exit /b 1
)

if not exist vendor_packages (
    echo ERROR: vendor_packages\ not found.
    echo Run download_offline_packages.bat on an internet-connected
    echo machine first, then copy the whole project folder ^(including
    echo vendor_packages\^) to this laptop.
    pause
    exit /b 1
)

python --version
echo.

if not exist venv (
    echo Creating a virtual environment in .\venv ...
    python -m venv venv
)

call venv\Scripts\activate.bat

echo Installing dependencies from vendor_packages\ ^(no internet needed^) ...
python -m pip install --no-index --find-links=vendor_packages -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: offline installation failed. See the message above.
    echo Most likely cause: vendor_packages\ was built for a different
    echo Python version or OS than this machine. Re-run
    echo download_offline_packages.bat on a machine matching this one.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Setup complete.
echo  Run scripts\windows\run.bat to start the simulator.
echo ============================================================
pause
