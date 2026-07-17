@echo off
setlocal
cd /d "%~dp0..\.."

set NSSM=tools\nssm\nssm.exe
set SERVICE_NAME=ACIBACnetSimulator

if not exist "%NSSM%" (
    echo nssm.exe not found at %NSSM% -- removing the service via sc.exe instead.
    net stop %SERVICE_NAME% >nul 2>nul
    sc delete %SERVICE_NAME%
    pause
    exit /b 0
)

echo Stopping and removing service "%SERVICE_NAME%" ...
"%NSSM%" stop %SERVICE_NAME%
"%NSSM%" remove %SERVICE_NAME% confirm

echo.
echo Done. The service has been removed. The project files themselves
echo are untouched -- you can still run the simulator manually with
echo scripts\windows\run.bat.
pause
