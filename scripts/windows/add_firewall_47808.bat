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
REM   - WebCTRL (the only allowed peer): 192.168.168.200.
REM
REM Scope notes:
REM   - remoteip is restricted to the WebCTRL host 192.168.168.200 so nothing
REM     else on the bench subnet or the office/corporate network can reach the
REM     simulator socket even at the firewall layer (the app's peer_allowlist
REM     is the second gate, also set to 192.168.168.200).
REM   - profile=any because the bench subnet has no default gateway, which
REM     Windows classifies as an "Unidentified" (Public) network -- a rule
REM     limited to Private/Domain profiles would silently not apply there.
REM ============================================================================

netsh advfirewall firewall add rule name="ACI BACnet Simulator (UDP 47808 in)" dir=in action=allow protocol=UDP localport=47808 remoteip=192.168.168.200 profile=any

echo.
netsh advfirewall firewall show rule name="ACI BACnet Simulator (UDP 47808 in)"
pause
