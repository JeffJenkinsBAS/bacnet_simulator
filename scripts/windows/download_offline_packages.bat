@echo off
setlocal

echo ============================================================
echo  Download offline install packages
echo ============================================================
echo  Run this on a Windows machine WITH internet access, matching
echo  the Python version you'll run on the bench laptop. It downloads
echo  every dependency into vendor_packages\ as .whl files. Copy the
echo  whole project folder (including vendor_packages\) to the bench
echo  laptop by USB drive, then run install_offline.bat there.
echo ============================================================
echo.

cd /d "%~dp0\.."

where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python was not found. Install Python 3.11+ first.
    pause
    exit /b 1
)

python --version
echo.

if not exist vendor_packages mkdir vendor_packages

echo Downloading packages into vendor_packages\ ...
python -m pip download -r requirements.txt -d vendor_packages
if errorlevel 1 (
    echo.
    echo ERROR: download failed. See the message above.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Done. vendor_packages\ now contains everything needed for
echo  install_offline.bat to work without internet access.
echo ============================================================
pause
