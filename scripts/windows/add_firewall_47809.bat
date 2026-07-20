@echo off
REM ============================================================================
REM ACI BACnet Simulator - inbound Windows Firewall rule for UDP 47809
REM
REM MUST be run as Administrator (right-click -> "Run as administrator").
REM A service has no desktop session, so the interactive "Allow access"
REM firewall popup never appears -- this rule has to be added manually.
REM
REM Scope notes:
REM   - remoteip is restricted to the bench subnet 192.168.168.0/24 so the
REM     office/corporate network can never reach the simulator socket even
REM     at the firewall layer (the app's peer_allowlist is the second gate).
REM   - profile=any because the bench subnet has no default gateway, which
REM     Windows classifies as an "Unidentified" (Public) network -- a rule
REM     limited to Private/Domain profiles would silently not apply there.
REM ============================================================================

netsh advfirewall firewall add rule name="ACI BACnet Simulator (UDP 47809 in)" dir=in action=allow protocol=UDP localport=47809 remoteip=192.168.168.0/24 profile=any

echo.
netsh advfirewall firewall show rule name="ACI BACnet Simulator (UDP 47809 in)"
pause
