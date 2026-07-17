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

**Status:** Phases 1–6a complete plus a full audit-and-hardening pass
(2026-07-17). **55 automated tests passing.** Core BACnet behavior —
including confirmed and unconfirmed COV notification delivery — verified
live against real cross-process BACnet/IP traffic on Windows, not assumed
from unit tests alone. See [`SIMULATION_AUDIT.md`](SIMULATION_AUDIT.md)
for the audit and [`HANDOFF.md`](HANDOFF.md) §0 for the session log.

---

## What this actually does

- Exposes **143 BACnet objects** under **one supervisory device**
  (`ACI-SIM-SUPERVISOR`, instance `242000`) on **UDP `47809`** —
  deliberately NOT the standard 47808, so bench traffic can never reach
  the office building-control WebCTRL that lives there. A transport-level
  `peer_allowlist` additionally drops every BACnet request from any
  non-allowlisted source without a reply (single-point connection to the
  bench WebCTRL only), with a live blocked-request counter on the
  dashboard.
- Supports **all three WebCTRL refresh strategies on every point** —
  polling (refresh timer < 31 s), UnconfirmedCOV (>= 31 s), and
  ConfirmedCOV (>= 1 min ending :01) — with notification delivery for
  both COV modes verified live and a dashboard panel showing active
  subscriptions per mode, so instructors can demonstrate the difference.
- Simulates **16 equipment groups**: an AHU, three chillers with condenser
  water and cooling towers, three boilers, an exhaust fan, five VAV
  zones, and the Chiller/Boiler Manager plant-level points — with real
  mechanical behavior (airflow responds to damper position and available
  static pressure; reheat discharge temp is physically bounded by the
  hot-water loop; chillers/boilers require proven water flow and have
  real purge/ignition/proof delays; cooling towers track wet-bulb and
  climb toward a high-head trip if the fan stops; hard interlocks like
  Freezestat Trip force real equipment shutdown including the OA damper;
  failed sensors raise the real BACnet Reliability flag WebCTRL
  displays).
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
`0.0.0.0`), `udp_port` stays `47809` (the bench WebCTRL's BACnet
connection must match; the office system owns 47808), and
`peer_allowlist` should hold the laptop's own IP so only the co-resident
bench WebCTRL can talk to the simulator.

## The dashboard

A liquid-glass control station (Apple design language: floating glass
rail and command bar, capsule controls, specular highlights, the ACI
logo as the brand mark) — still one self-contained HTML file backed by a
plain REST API, no build step, no CDN, fully offline-safe. Five views:

| View | What's there |
|---|---|
| **Overview** | Engine power toggle, ×1–×60 time-rate control, network/device status with peer-allowlist + blocked-request readouts, site weather, active scenario, live COV subscriptions per refresh mode |
| **Equipment** | Every BACnet object with live searchable values, value-change flashes, fault/forced/interlock badges |
| **Operations** | Training scenarios with live progress, manual fault injection, force/release, guarded by confirm dialogs and the STOP ALL control |
| **AI Console** | Ollama connection status, plain-language scenario/fault requests, action preview and approval, audit trail |
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
  config_models.py, registry.py, transport.py, engine.py    Core BACnet simulation layers (peer allowlist lives in transport)
  faults.py, scenario.py                                       Fault injection + scenario engine
  equipment/                                                     ahu.py, chiller.py, boiler.py, exhaust_fan.py, site.py, vav_single_duct.py, managers.py
  llm/, services/                                                  Ollama client, action validation, orchestration (Phase 6a)
  api.py, main.py                                                    FastAPI endpoints (incl. /api/cov/subscriptions), entry point
config/
  network.json, supervisory_device.json                              Bind address, port 47809, peer allowlist, device identity
  devices/*.json, scenarios/*.json, llm/*.json                          Equipment groups, training scenarios, LLM settings
static/index.html, static/logo.png     The dashboard (single file, no build step) + company logo
scripts/
  generate_phase3_configs.py, generate_point_mapping_workbook.py         Regenerate configs/docs from code, not by hand
  windows/                                                                 install/run/service scripts — see PACKAGING.md
tests/                                  55 tests: unit, real BACnet/IP integration (incl. both COV modes + peer allowlist), regression, LLM orchestration
```

## Testing

```bash
PYTHONPATH=. pytest tests/ -v
```

55 tests, many of them real integration tests against actual BACnet/IP
traffic (not mocked) — including confirmed/unconfirmed COV notification
delivery, peer-allowlist enforcement, flow-proving interlocks, and the
audit-fix behaviors — plus the real Ollama client's HTTP contract (mocked
transport, real request/response parsing). Live-traffic testing has caught
real bugs at every stage: a fan/pump proof-delay bug during development
([`DEVELOPMENT_HISTORY.md`](DEVELOPMENT_HISTORY.md)), and later a
Windows-specific loopback-broadcast bug that left the BACnet socket bound
but deaf ([`SIMULATION_AUDIT.md`](SIMULATION_AUDIT.md) status notes).

## Documentation

| Document | For |
|---|---|
| **This file** | Start here |
| [`HANDOFF.md`](HANDOFF.md) | Current authoritative project status, architecture detail, open items (§0 = latest session) |
| [`SIMULATION_AUDIT.md`](SIMULATION_AUDIT.md) | Full equipment/BACnet audit against WebCTRL domain references, with fix status |
| [`PACKAGING.md`](PACKAGING.md) | Install, Windows Firewall, running as a service, troubleshooting |
| [`NEXT_STEPS_INTEGRATION_TESTING.md`](NEXT_STEPS_INTEGRATION_TESTING.md) | Step-by-step bench laptop deployment |
| [`PHASE6_REVIEW.md`](PHASE6_REVIEW.md) | The LLM/dashboard expansion plan and risk analysis |
| [`DEVELOPMENT_HISTORY.md`](DEVELOPMENT_HISTORY.md) | Phase-by-phase build narrative, including real bugs found and fixed |
| `ACI_BACnet_Simulator_Point_Mapping.xlsx` | Every BACnet object, address, and direction (not in this repo by default — generate with `scripts/generate_point_mapping_workbook.py`) |

## Known limitations

Honestly scoped, not hidden — full list in `HANDOFF.md` §6, briefly:

- The supervisory device instance (`242000`) is a proposed default, not
  yet formally confirmed as final.
- No occupancy modeling, no auto-graded scenario completion criteria.
- The startup duplicate-instance check is skipped on loopback binds (a
  Windows loopback-broadcast quirk kills the UDP transport); its behavior
  on the real bench NIC's broadcast domain is still unverified.
- Ollama isn't installed on the bench laptop yet — the AI Console
  correctly reports NOT REACHABLE until it is.
- Dynamic equipment management (adding/removing simulated equipment at
  runtime via the LLM) is deliberately not built yet — see
  `PHASE6_REVIEW.md` for why that's a separate, higher-risk phase.

## Author

Built for Jeff Jenkins, Automated Controls Inc.
