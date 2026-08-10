@echo off
setlocal

echo ============================================================
echo  ACI BACnet Building Simulation Platform - Setup (Online)
echo ============================================================
echo.

cd /d "%~dp0..\.."

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
    echo Checking the existing virtual environment ...
    venv\Scripts\python.exe -c "import sys; assert sys.version_info[:2] == (3, 11), sys.version"
    if errorlevel 1 (
        echo.
        echo ERROR: .\venv is broken or is not based on Python 3.11.
        echo Do not reuse or copy virtual environments between applications.
        echo Move the existing venv aside, install standalone Python 3.11,
        echo then run this installer again to create a clean environment.
        pause
        exit /b 1
    )
    echo Virtual environment is healthy, reusing .\venv
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

python -c "import app.api, app.config_models, app.engine, bacpypes3, fastapi, pydantic, uvicorn"
if errorlevel 1 (
    echo ERROR: dependency smoke test failed; the simulator was not installed cleanly.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Setup complete.
echo  Run scripts\windows\run.bat to start the simulator.
echo ============================================================
pause
