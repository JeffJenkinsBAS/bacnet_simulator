# Next Steps: Bench Laptop Deployment → Integration Testing

Use **`aci-bacnet-sim-phase5-service.zip`** — it's the cumulative, current
build (everything from the equipment models through the service scripts).
The earlier phase zips are superseded; ignore them to avoid confusion
about which one is "real."

## 1. Get the project onto the laptop

Unzip to a permanent location, e.g. `C:\ACI-BACnet-Simulator\`. Anywhere
works, but pick somewhere you won't accidentally clean up later — the
service scripts reference paths relative to wherever this folder ends up.

## 2. Install Python (one-time, if not already present)

Python 3.11+ from [python.org/downloads/windows](https://www.python.org/downloads/windows/).
**Check "Add python.exe to PATH"** on the installer's first screen — the
single most common setup failure. Verify with `python --version` in
Command Prompt afterward.

## 3. Install dependencies

```
scripts\windows\install.bat
```
(or `install_offline.bat` if this laptop has no internet — see
`PACKAGING.md` for the two-step offline process).

## 4. Set the real network config — do this before anything else works

Edit `config\network.json`:
- **`bind_address`** → the laptop's actual NIC IP on the bench's
  `192.168.68.0/24` subnet (find it with `ipconfig`). **Never `127.0.0.1`
  or `0.0.0.0`** — both were tested during development and don't reliably
  deliver BACnet traffic.
- **`udp_port`** → `47809` (bench standard, deliberately NOT 47808: the
  office building-control WebCTRL at 192.168.45.34 lives on 47808 and
  must never see bench traffic; Jeff sets the bench WebCTRL's BACnet
  connection to 47809 to match).
- **`peer_allowlist`** → `["<this laptop's static IP>"]` so the simulator
  only answers the co-resident bench WebCTRL and silently drops every
  other host (single-point connection). The dashboard's Network panel
  shows a live count of blocked requests.

## 5. Do one manual test run first, before wrapping it as a service

```
scripts\windows\run.bat
```

Confirm the dashboard opens at `http://127.0.0.1:8000` and
`logs\aci_sim.log` shows something like:

```
Loaded and validated 16 equipment groups (143 total objects)
Supervisory BACnet device 'ACI-SIM-SUPERVISOR' (instance 242000) online, bound to <your IP>:47809
Duplicate device-instance check passed for instance 242000
```

That last line matters — it's the app's own automatic check that nothing
else on the bench network is already claiming instance 242000. If it warns
instead of passing, stop here and resolve that before going further; two
devices sharing an instance will cause confusing, intermittent WebCTRL
behavior that's much harder to diagnose after the fact than to catch now.

Debugging is much easier at this interactive stage than after it's a
background service — worth not skipping.

Close the console window (or Ctrl+C) once confirmed.

## 6. Add the Windows Firewall rule now — don't wait

A service has no desktop session, so the "Allow access" popup you might
expect **will not appear**. Add it manually before moving on:

1. `wf.msc` → **Inbound Rules → New Rule → Port → UDP → Specific local
   ports: `47809`**
2. Allow the connection, apply to **Private** networks, name it
   something identifiable.

## 7. Get NSSM and install the service

1. Download from [nssm.cc/download](https://nssm.cc/download), extract
   `win64\nssm.exe` into `tools\nssm\nssm.exe`.
2. Run **as Administrator**:
   ```
   scripts\windows\install_service.bat
   ```

Confirm in `services.msc` that **ACIBACnetSimulator** shows as Running,
and that the dashboard still loads at `http://127.0.0.1:8000`.

## 8. Reboot the laptop once, to prove auto-start actually works

This is the real test of "starts on Windows startup" — not just that the
install script worked, but that it survives a cold boot with nobody logged
in yet. After reboot, check the dashboard loads without you having done
anything.

## 9. Begin WebCTRL integration testing

Now the actual integration work, using the **point-mapping workbook**
(`ACI_BACnet_Simulator_Point_Mapping.xlsx`) delivered earlier as your
reference for every address:

1. In WebCTRL, follow the BACnet Discovery steps from the ALC Integration
   Guide (disconnect/reconfigure the BACnet/IP connection with the
   server's IP/subnet, restart it, then run Discovery against the
   simulator's IP).
2. You should see one device — **instance 242000** — with all 143 objects
   underneath it. If Discovery doesn't find it, that's a firewall or
   `bind_address` problem before it's anything else; re-check steps 4 and 6.
3. Start with something small and observable: bind a Network Input
   microblock to `bacnet://242000/ai:9004` (AHU-1 Supply Air Temp) and
   confirm it reads back correctly — the point-mapping workbook's
   **BACnet Address String** column has the exact string for every point.
4. For your first real end-to-end check, I'd suggest replicating the same
   test we ran during development: command AHU-1's cooling valve and
   supply fan on through WebCTRL, then watch the dashboard's **Equipment &
   Points** tab live — you should see Supply Air Temp drop and, if VAV-1's
   damper is also commanded open, its Discharge Temp track that change in
   real time. That single test touches reads, writes, priority arrays, and
   cross-equipment behavior all at once.
5. Once basic reads/writes are confirmed, the **Instructor Panel** tab
   (scenarios, fault injection, force/release) is ready to use for actual
   training exercises whenever you want to move past pure integration
   testing into class use.

## If something doesn't work

`PACKAGING.md`'s recovery checklist covers the common failure modes
(Python not found, firewall, dashboard not loading, service vs. in-app
stop). For anything integration-specific — an object that won't bind, a
value that looks wrong, a point missing from Discovery — the exact
address, direction, and expected behavior for every point is in the
point-mapping workbook; start there before assuming it's a simulator bug.
