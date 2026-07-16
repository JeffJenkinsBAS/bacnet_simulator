@echo off
setlocal

echo ============================================================
echo  ACI BACnet Building Simulation Platform - Setup (Online)
echo ============================================================
echo.

cd /d "%~dp0\.."

where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python was not found on this system.
    echo.
    echo Install Python 3.11 or later from:
    echo     https://www.python.org/downloads/windows/
    echo.
    echo IMPORTANT: on the installer's first screen, check the box
    echo "Add python.exe to PATH" before clicking Install. If you
    echo miss this, Windows will not be able to find "python" from
    echo this script or from a Command Prompt.
    echo.
    pause
    exit /b 1
)

echo Found Python:
python --version
echo.

if not exist venv (
    echo Creating a virtual environment in .\venv ...
    python -m venv venv
    if errorlevel 1 (
        echo ERROR: failed to create the virtual environment. See the message above.
        pause
        exit /b 1
    )
) else (
    echo Virtual environment already exists, reusing .\venv
)

call venv\Scripts\activate.bat

echo.
echo Installing dependencies from requirements.txt ...
echo (this needs internet access and may take a few minutes the first time)
echo.
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: dependency installation failed. See the message above.
    echo If this machine has no internet access, use install_offline.bat
    echo instead -- see PACKAGING.md for how to prepare the offline packages.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Setup complete.
echo  Run scripts\windows\run.bat to start the simulator.
echo ============================================================
pause
