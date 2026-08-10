@echo off
REM ============================================================================
REM ACI BACnet Simulator - inbound Windows Firewall rule for UDP 47808
REM
REM MUST be run as Administrator (right-click -> "Run as administrator").
REM A service has no desktop session, so the interactive "Allow access"
REM firewall popup never appears -- this rule has to be added manually.
REM
REM Verified bench topology:
REM   - Simulator (this machine): 192.168.168.201, listening on UDP 47808.
REM   - Verified WebCTRL/controller peers: 192.168.168.1-.7 and .200.
REM
REM Scope notes:
REM   - remoteip matches config\network.json's verified live allowlist. Nothing
REM     else on the bench or office/corporate network can reach the simulator
REM     socket even at the firewall layer; the app allowlist is the second gate.
REM   - profile=any because the bench subnet has no default gateway, which
REM     Windows classifies as an "Unidentified" (Public) network -- a rule
REM     limited to Private/Domain profiles would silently not apply there.
REM ============================================================================

netsh advfirewall firewall add rule name="ACI BACnet Simulator (UDP 47808 in)" dir=in action=allow protocol=UDP localport=47808 remoteip=192.168.168.1-192.168.168.7,192.168.168.200 profile=any

echo.
netsh advfirewall firewall show rule name="ACI BACnet Simulator (UDP 47808 in)"
pause
