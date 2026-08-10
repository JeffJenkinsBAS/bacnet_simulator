@echo off
setlocal
cd /d "%~dp0\..\.."

fltmc >nul 2>&1
if errorlevel 1 (
    echo ERROR: Run this script as Administrator.
    echo Right-click restart_service.bat and choose "Run as administrator".
    exit /b 1
)

set "NSSM=%CD%\tools\nssm\nssm.exe"
if not exist "%NSSM%" (
    echo ERROR: NSSM was not found at "%NSSM%".
    exit /b 1
)

echo Restarting ACIBACnetSimulator...
"%NSSM%" restart ACIBACnetSimulator
if errorlevel 1 (
    echo ERROR: NSSM could not restart ACIBACnetSimulator.
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$deadline=(Get-Date).AddSeconds(45); $ready=$false; do { Start-Sleep -Seconds 1; try { $s=Invoke-RestMethod -Uri 'http://127.0.0.1:8001/api/status' -TimeoutSec 2; if ($s.simulation.running -and $s.fleet.total_point_count -eq 329 -and $s.uptime_seconds -lt 120) { $ready=$true } } catch {} } while (-not $ready -and (Get-Date) -lt $deadline); if (-not $ready) { Write-Error 'Service did not return a fresh, healthy 329-point status within 45 seconds'; exit 1 }; Write-Host ('Healthy: {0}, instance {1}, {2} groups / {3} points, uptime {4:N1}s' -f $s.device.name,$s.device.instance,$s.fleet.group_count,$s.fleet.total_point_count,$s.uptime_seconds)"
if errorlevel 1 exit /b 1

echo Service restart verified.
exit /b 0
