# ACI BACnet Building Simulation Platform

A locally-running BACnet/IP building simulator for training HVAC controls
technicians on a WebCTRL test bench — not a static point generator, but
equipment that actually behaves: realistic proof delays, interlocks,
priority-array command resolution, injectable faults, and instructor-built
training scenarios, all exposed as real BACnet objects a real WebCTRL
system reads from and writes to.

Built for Automated Controls Inc.'s training bench, integrating with
existing WebCTRL/EIKON programs (AHU, Chiller Manager, Boiler Manager,
VAV-1, VAV-2, Simulation Manager) exactly as real field hardware would.

**Status:** Phases 1–6a complete. 40 automated tests passing. Core BACnet
behavior verified against real BACnet/IP traffic during development, not
assumed from unit tests alone.

---

## What this actually does

- Exposes **143 BACnet objects** under **one supervisory device**
  (`ACI-SIM-SUPERVISOR`, instance `242000`) on the standard BACnet/IP port
  `47808` — discoverable and bindable from WebCTRL exactly like a real
  building's field devices.
- Simulates **16 equipment groups**: an AHU, three chillers with condenser
  water and cooling towers, three boilers, an exhaust fan, and five VAV
  zones — with real mechanical behavior (airflow responds to damper
  position and available static pressure; discharge temp responds to
  reheat valve position, supply air temp, and airflow; chillers/boilers
  have real purge/ignition/proof delays; hard interlocks like Freezestat
  Trip force real equipment shutdown, not just an alarm flag).
- **Injects faults on demand**: 11 generic mechanics (frozen/drifting/
  offset sensors, stuck/reversed actuators, forced status, and
  transport-level faults like device-offline or intermittent comm) that
  give real meaning to every named fault a technician needs to practice
  diagnosing.
- **Runs timed training scenarios** — six shipped, each a genuinely
  different failure pattern (an interlock trip, a stuck actuator, a slow
  sensor drift, a failed-to-prove boiler, a frozen sensor, a whole-device
  comm loss) — executed against simulated time so a scenario behaves
  identically whether run at 1x or 20x speed.
- **Lets an instructor ask for scenarios and faults in plain language**
  through a local Ollama LLM, with every proposed action validated and
  previewed before anything actually changes — the LLM never bypasses
  validation to touch simulator state directly, and never issues raw
  BACnet commands.
- Ships as a **Windows batch-script install** (not a fragile PyInstaller
  bundle — see [`PACKAGING.md`](PACKAGING.md) for why) with an optional
  auto-start Windows Service via NSSM.

## Quickstart

```bash
python3 -m venv venv
source venv/bin/activate        # venv\Scripts\activate on Windows
pip install -r requirements.txt
python -m app.main
```

Dashboard at **http://127.0.0.1:8000**. On Windows, use
`scripts\windows\install.bat` then `scripts\windows\run.bat` instead — see
[`PACKAGING.md`](PACKAGING.md) for the full install/firewall/service
walkthrough, including running this as an auto-starting Windows Service.

**Before connecting to a real bench network:** edit `config/network.json`
— `bind_address` must be the machine's real NIC IP (never `127.0.0.1` or
`0.0.0.0`, both tested and confirmed unreliable for BACnet replies with
this stack).

## The dashboard

Five tabs, all backed by a plain REST API (no build step, no framework):

| Tab | What's there |
|---|---|
| **Dashboard** | Simulation state, network/device status, active scenario |
| **Equipment & Points** | Every BACnet object, live values, fault/interlock badges |
| **Instructor Panel** | Manual fault injection, force/release values, scenario controls, the big red Stop-All-Simulation button |
| **LLM Console** | Ollama connection status, plain-language scenario/fault requests, action preview and approval, audit trail |
| **Logs** | Application log and BACnet traffic log, live-tailed |

## Architecture, in brief

```
BACnet Transport  →  Simulation Engine  →  Equipment Models  →  Point Registry  →  REST API + Dashboard
 (one bacpypes3       (1Hz tick loop,        (AHU, chillers,      (GroupView: the
  Application,          faults, scenarios)     boilers, VAVs...)    one seam every
  one UDP socket)                                                    layer above
                                                                      goes through)
```

`GroupView` is the load-bearing abstraction here — every equipment model
reads and writes through it, never touching BACnet objects or fault state
directly. That's what let the object model consolidate from 16 separate
BACnet devices down to one (a real correction made mid-project) and later
let fault injection and LLM orchestration get bolted on without changing a
single equipment model file.

Full detail in [`HANDOFF.md`](HANDOFF.md) §3–4.

## Project layout

```
app/
  config_models.py, registry.py, transport.py, engine.py    Core BACnet simulation layers
  faults.py, scenario.py                                       Fault injection + scenario engine
  equipment/                                                     ahu.py, chiller.py, boiler.py, exhaust_fan.py, site.py, vav_single_duct.py
  llm/, services/                                                  Ollama client, action validation, orchestration (Phase 6a)
  api.py, main.py                                                    FastAPI endpoints, entry point
config/
  network.json, supervisory_device.json                              Bind address/port, the one device's identity
  devices/*.json, scenarios/*.json, llm/*.json                          Equipment groups, training scenarios, LLM settings
static/index.html                  The dashboard (single file, no build step)
scripts/
  generate_phase3_configs.py, generate_point_mapping_workbook.py         Regenerate configs/docs from code, not by hand
  windows/                                                                 install/run/service scripts — see PACKAGING.md
tests/                                  40 tests: unit, real BACnet/IP integration, regression, LLM orchestration
```

## Testing

```bash
PYTHONPATH=. pytest tests/ -v
```

40 tests, several of them real integration tests against actual BACnet/IP
traffic (not mocked) and against the real Ollama client's HTTP contract
(mocked transport, real request/response parsing). A fan/pump proof-delay
bug was caught this way during development — see
[`DEVELOPMENT_HISTORY.md`](DEVELOPMENT_HISTORY.md) for the full story.

## Documentation

| Document | For |
|---|---|
| **This file** | Start here |
| [`HANDOFF.md`](HANDOFF.md) | Current authoritative project status, architecture detail, open items |
| [`PACKAGING.md`](PACKAGING.md) | Install, Windows Firewall, running as a service, troubleshooting |
| [`NEXT_STEPS_INTEGRATION_TESTING.md`](NEXT_STEPS_INTEGRATION_TESTING.md) | Step-by-step bench laptop deployment |
| [`PHASE6_REVIEW.md`](PHASE6_REVIEW.md) | The LLM/dashboard expansion plan and risk analysis |
| [`DEVELOPMENT_HISTORY.md`](DEVELOPMENT_HISTORY.md) | Phase-by-phase build narrative, including real bugs found and fixed |
| `ACI_BACnet_Simulator_Point_Mapping.xlsx` | Every BACnet object, address, and direction (not in this repo by default — generate with `scripts/generate_point_mapping_workbook.py`) |

## Known limitations

Honestly scoped, not hidden — full list in `HANDOFF.md` §6, briefly:

- The supervisory device instance (`242000`) is a proposed default, not
  yet formally confirmed as final.
- BACnet COV notification delivery on value change isn't confirmed
  working (subscription setup/ack is); low WebCTRL refresh timers sidestep
  this in practice.
- No occupancy modeling, no auto-graded scenario completion criteria.
- Dynamic equipment management (adding/removing simulated equipment at
  runtime via the LLM) is deliberately not built yet — see
  `PHASE6_REVIEW.md` for why that's a separate, higher-risk phase.

## Author

Built for Jeff Jenkins, Automated Controls Inc.
