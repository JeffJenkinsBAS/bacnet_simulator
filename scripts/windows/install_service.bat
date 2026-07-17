@echo off
setlocal

echo ============================================================
echo  Install ACI BACnet Simulator as a Windows Service (NSSM)
echo ============================================================
echo.

cd /d "%~dp0..\.."

if not exist venv\Scripts\python.exe (
    echo ERROR: virtual environment not found.
    echo Run scripts\windows\install.bat ^(or install_offline.bat^) first.
    pause
    exit /b 1
)

set NSSM=tools\nssm\nssm.exe
if not exist "%NSSM%" (
    echo ERROR: nssm.exe not found at %NSSM%
    echo.
    echo This project does not ship the NSSM binary -- download it once
    echo from https://nssm.cc/download, extract the win64\nssm.exe from
    echo the zip, and place it at:
    echo     %cd%\tools\nssm\nssm.exe
    echo Then re-run this script. See PACKAGING.md for the full walkthrough.
    pause
    exit /b 1
)

set SERVICE_NAME=ACIBACnetSimulator
set PROJECT_DIR=%cd%
set PYTHON_EXE=%PROJECT_DIR%\venv\Scripts\python.exe
set LOG_DIR=%PROJECT_DIR%\logs

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo Checking whether the service already exists ...
sc query %SERVICE_NAME% >nul 2>nul
if not errorlevel 1 (
    echo A service named "%SERVICE_NAME%" already exists.
    echo Run uninstall_service.bat first if you want to reinstall it.
    pause
    exit /b 1
)

echo Installing service "%SERVICE_NAME%" ...
"%NSSM%" install %SERVICE_NAME% "%PYTHON_EXE%" "-m app.main"
"%NSSM%" set %SERVICE_NAME% AppDirectory "%PROJECT_DIR%"
"%NSSM%" set %SERVICE_NAME% DisplayName "ACI BACnet Building Simulation Platform"
"%NSSM%" set %SERVICE_NAME% Description "Simulates BACnet building equipment for the WebCTRL training bench. Dashboard at http://127.0.0.1:8000 -- see PACKAGING.md."
"%NSSM%" set %SERVICE_NAME% Start SERVICE_AUTO_START
"%NSSM%" set %SERVICE_NAME% AppStdout "%LOG_DIR%\service_stdout.log"
"%NSSM%" set %SERVICE_NAME% AppStderr "%LOG_DIR%\service_stderr.log"
"%NSSM%" set %SERVICE_NAME% AppRotateFiles 1
"%NSSM%" set %SERVICE_NAME% AppRotateBytes 5242880
"%NSSM%" set %SERVICE_NAME% AppExit Default Restart
"%NSSM%" set %SERVICE_NAME% AppRestartDelay 5000

if errorlevel 1 (
    echo.
    echo ERROR: something failed during service configuration. See above.
    echo This step usually needs to be run as Administrator.
    pause
    exit /b 1
)

echo.
echo Starting the service ...
"%NSSM%" start %SERVICE_NAME%

echo.
echo ============================================================
echo  Done. The simulator will now start automatically on every
echo  Windows boot, even before anyone logs in.
echo.
echo  Dashboard:     http://127.0.0.1:8000
echo  Service name:  %SERVICE_NAME%  (visible in services.msc)
echo  Logs:          logs\service_stdout.log, logs\service_stderr.log,
echo                 plus the normal logs\aci_sim.log / bacnet_traffic.log
echo.
echo  IMPORTANT: a service has no interactive desktop session, so the
echo  Windows Firewall "Allow access" popup will NOT appear. Add the
echo  firewall rule manually -- see PACKAGING.md, "Windows Firewall"
echo  section -- BEFORE relying on this for real bench traffic.
echo ============================================================
pause
