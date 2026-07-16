# Packaging & Installation — Running on the Bench Laptop

## Recommended method: Python + batch launcher (not a PyInstaller .exe)

A single-file `.exe` via PyInstaller was considered and set aside for this
project specifically. `bacpypes3` is built entirely on asyncio and does some
dynamic module loading internally; bundling that kind of async network stack
with PyInstaller has a real history of subtle failures (missing hidden
imports, slow/broken startup) that are hard to diagnose on-site on a bench
laptop with no direct access to a dev environment. It also has to be *built
on a Windows machine* — there's no way to produce a working Windows `.exe`
from a Linux build environment, only to hand over a build script for someone
to run once on an actual Windows box.

Plain Python + a batch-file launcher is more work up front (installing
Python once) but is transparent, easy to fix on-site, and matches the
project's own stated priority: predictable operation over decorative
packaging. If a true single-file `.exe` is wanted later, `scripts/windows/`
is the place to add a PyInstaller `.spec` — flagged here as a possible
Phase 5 follow-up, not built in this pass since it needs to be built and
tested on the real target machine, not this Linux sandbox.

## Prerequisites (one-time, per laptop)

1. **Python 3.11 or later.** Download from
   [python.org/downloads/windows](https://www.python.org/downloads/windows/).
   On the installer's first screen, check **"Add python.exe to PATH"**
   before clicking Install — this is the single most common setup failure.
   Verify afterward by opening Command Prompt and running `python --version`.
2. **This project folder**, copied onto the laptop (USB drive, network
   share, however it gets there).

That's the whole prerequisite list. No admin rights are required beyond
whatever the Python installer itself needs.

## Installing

### If the laptop has internet access

```
scripts\windows\install.bat
```

Creates a virtual environment in `.\venv` and installs everything from
`requirements.txt` off PyPI. Takes a few minutes the first time.

### If the laptop does NOT have internet access

Two-step process, done once (or whenever a dependency version changes):

1. On a **different** Windows machine that does have internet access and
   the **same Python version** you'll run on the bench laptop, run:
   ```
   scripts\windows\download_offline_packages.bat
   ```
   This downloads every dependency as `.whl` files into `vendor_packages\`.

2. Copy the **whole project folder**, including the new `vendor_packages\`
   directory, onto the bench laptop (USB drive), then run:
   ```
   scripts\windows\install_offline.bat
   ```
   This installs from `vendor_packages\` with no internet access needed.

If offline install fails with a version-mismatch-looking error, the most
likely cause is that `vendor_packages\` was built on a different Python
version or a different OS than the bench laptop — re-run step 1 on a
matching machine.

## Running

```
scripts\windows\run.bat
```

This opens the dashboard automatically at **http://127.0.0.1:8000** and
starts the BACnet device in the same console window. **The console window
IS the running application** — minimize it, don't close it. Closing it or
pressing Ctrl+C inside it stops the simulator and every BACnet object it
publishes, which is also the fastest "something's wrong, kill it" recovery
step if needed.

## Before connecting to the real bench network

Two settings in `config/network.json` need to be correct — the app will
run without changing these, but won't actually be reachable from WebCTRL or
the real controllers until they're set for the bench's actual network:

1. **`bind_address`** must be the bench laptop's real NIC IP address on the
   `192.168.68.0/24` subnet (e.g. `192.168.68.50`), **never `0.0.0.0`** —
   binding to `0.0.0.0` was tested during development and does not reliably
   deliver BACnet replies with this stack (see the Phase 2 finding in the
   project's development history). Find the laptop's IP with `ipconfig` in
   Command Prompt.
2. **`udp_port`** should be `47808` (the standard BACnet/IP port, and the
   default in this config) — only change this if something else on the
   laptop is already using that port, which should be resolved rather than
   worked around, since Automated Logic controllers broadcast to 47808 by
   default.

## Windows Firewall

The first time the simulator runs, Windows Defender Firewall will likely
prompt: *"Windows Defender Firewall has blocked some features of
python.exe."* Check **Private networks** (the bench network should be set
to Private, not Public, in Windows network settings) and click **Allow
access**. If that prompt never appears (e.g. running unattended) and BACnet
traffic isn't reaching the simulator, add a manual inbound rule instead:

1. Open **Windows Defender Firewall with Advanced Security** (`wf.msc`).
2. **Inbound Rules → New Rule → Port → UDP → Specific local ports:** `47808`
   (or whatever `udp_port` is set to).
3. Allow the connection, apply to **Private** networks only, name it
   something identifiable like "ACI BACnet Simulator."

## Viewing the dashboard from another device on the bench network (optional)

The web dashboard (`http://127.0.0.1:8000`) is separate from the BACnet
bind address and has none of the same restrictions — it's plain HTTP, not
BACnet broadcast traffic. If you want to view it from another device on the
bench network (a second laptop, a tablet), change the dashboard's bind host
in `app/main.py`'s `main()` function from `127.0.0.1` to `0.0.0.0`, then
browse to `http://<bench-laptop-ip>:8000` from the other device. Leave
`config/network.json`'s `bind_address` alone — that one setting is genuinely
BACnet-specific and the 0.0.0.0 restriction there stays in force regardless
of this dashboard setting.

## Updating

Copy the new project files over the old ones (or `git pull` if using
version control), then re-run `install.bat` (or `install_offline.bat`) to
pick up any new dependencies — it's safe to re-run any time, it reuses the
existing `venv` folder rather than starting over.

## Running as a Windows Service (auto-start on boot)

For a bench that should have the simulator running automatically after
every reboot, without anyone needing to log in and double-click `run.bat`.

### Recommended: NSSM

**Why NSSM instead of a "real" Python Windows service:** writing a native
Windows service in Python (via `pywin32`) means bridging asyncio's event
loop into the service's start/stop lifecycle, which is exactly the kind of
fragile async-plus-Windows-internals interaction this project has
deliberately avoided elsewhere (see the PyInstaller discussion above).
NSSM sidesteps that entirely — it just launches the exact same
`python.exe -m app.main` command that already works from `run.bat`,
monitors the process, restarts it if it crashes, and redirects its output
to log files. Zero changes to the application code.

**Getting NSSM** (not shipped in this project — see the reasoning in
`tools\nssm\PUT_NSSM_EXE_HERE.txt`):

1. Download from [nssm.cc/download](https://nssm.cc/download).
2. Extract `win64\nssm.exe` (or `win32\nssm.exe` on a 32-bit Windows
   install) into `tools\nssm\nssm.exe` in this project folder.

**Installing the service:**

```
scripts\windows\install_service.bat
```

Run as Administrator (right-click → Run as administrator) — service
installation requires elevated privileges. This registers a service named
`ACIBACnetSimulator`, sets it to start automatically at boot, redirects its
output to `logs\service_stdout.log` / `logs\service_stderr.log` (in
addition to the app's own `logs\aci_sim.log` and `logs\bacnet_traffic.log`,
which work identically whether run as a service or interactively), and
configures it to restart automatically if the process ever dies.

**Removing it:**

```
scripts\windows\uninstall_service.bat
```

### Fallback: Task Scheduler (no third-party download)

If installing NSSM isn't an option on a given laptop (locked-down IT
policy, no way to get the file over), `install_scheduled_task.bat` uses
only Windows' built-in Task Scheduler to start the simulator at boot,
running as SYSTEM. It is **not** a true Windows Service — it won't show up
in `services.msc`, and restart-on-crash behavior depends on Task
Scheduler's own settings rather than NSSM's — but it accomplishes the same
practical goal (running automatically, before anyone logs in) with nothing
to download. Must also be run as Administrator.

### Important: firewall rule becomes mandatory, not optional

The interactive "Windows Defender Firewall has blocked some features"
popup described earlier **will not appear** for a service or a scheduled
task running as SYSTEM — there's no desktop session to show it to. Add the
manual inbound firewall rule (see the Windows Firewall section above)
**before** relying on either auto-start method for real bench traffic, or
BACnet requests will silently go nowhere with no obvious symptom.

### Two different "stop" buttons — don't confuse them

Once running as a service, there are two separate things that can be
"stopped," at two different layers:

- **`services.msc` (or `nssm stop ACIBACnetSimulator`)** stops the whole
  application process — the BACnet device disappears from the network
  entirely until the service is started again.
- **The dashboard's red "STOP ALL SIMULATION" button** stops the
  simulation engine and clears every fault/force/scenario, but the process
  keeps running and the BACnet device stays online (all points just stop
  updating and hold their last values).

For "something's wrong, reset everything" during a class, the dashboard
button is almost always the right one — it's faster and doesn't require
Administrator access. Stopping the actual service is more for
maintenance/updates.

### Viewing the dashboard when running as a service

No difference from running interactively — `http://127.0.0.1:8000` works
identically whether the process is running as a service (under the Local
System account, a different Windows session) or launched from a logged-in
user's console. Loopback connections work across sessions on the same
machine; this isn't something to configure.

## Quick recovery checklist

| Symptom | Likely cause | Fix |
|---|---|---|
| `install.bat` says Python not found | Python not installed, or "Add to PATH" wasn't checked | Reinstall Python, check the PATH box |
| Dashboard doesn't open / connection refused | Simulator not actually running, or crashed on startup | Check the console window for an error near the top (or `logs\service_stderr.log` if running as a service) |
| WebCTRL can't discover the device | `bind_address` still `127.0.0.1` or `0.0.0.0`, or firewall blocking UDP 47808 | Fix `config/network.json`, add the firewall rule above |
| Everything was working, now nothing responds | The console window got closed (interactive mode), or the service/task stopped | Re-run `run.bat`, or check `services.msc` / Task Scheduler |
| Need to wipe all faults/forces immediately | Any active fault, force, or scenario got left in a confusing state | Use the dashboard's red **STOP ALL SIMULATION** button — works the same whether running interactively or as a service |
| Works when run manually, not after reboot as a service | Firewall rule was never added (no popup appears for a service) | Add the manual inbound rule — see "Windows Firewall" above |
