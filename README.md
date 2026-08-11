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

## P0 repeatable training workflow

The command center now includes a **Training** workspace with authenticated
student/instructor roles, six versioned baselines, deterministic checkpoint
restore, BACnet priority reconciliation, scenario preflight, internal evidence
recording, and time-window scoring for all ten shipped scenarios. Completed
attempts are written to `artifacts/training/` as JSON evidence bundles.

Set `ACI_SIM_INSTRUCTOR_PIN` for the Windows service, or read the locally
generated PIN from `logs/training-instructor-pin.txt`. See
[`docs/TRAINING_P0_LAYER.md`](docs/TRAINING_P0_LAYER.md) for the workflow,
permissions, endpoints, and recovery semantics.

**Status:** Phases 1–6a complete plus audit-and-hardening passes
(2026-07-17 through 2026-08-11). The configured 355-point release passes
**198 automated tests**. The historical 329-point release was verified live
on the Windows bench service before this additive HW-loop telemetry release.
The historical 321-point baseline passed 129 tests. Core BACnet behavior has
been verified against real
cross-process BACnet/IP traffic on Windows, not assumed from unit tests alone. See
[`SIMULATION_AUDIT.md`](SIMULATION_AUDIT.md) for the audit and
[`HANDOFF.md`](HANDOFF.md) §0 for the session log.

**Deployment state:** The current checkout contains **28 groups / 355 BACnet
points**. This release preserves every identifier in the verified 329-point
catalog, including eight read-only AHU-1 sensor/safety objects: mixed-air
humidity `AI:9005`, supply-air humidity `AI:9006`, cooling-coil entering-air
temperature `AI:9007`, automatic high-static trip `BI:9044`, supply-duct
structural failure `BI:9045`, automatic freezestat trip `BI:9046`,
cooling-coil freeze condition `BI:9047`, and cooling-coil burst/flood alarm
`BI:9048`. The existing duct-static actual remains `AV:9003`, but its
published range is now 0.00-10.00 in. H2O so the training-only overpressure
failure can be represented. It also adds 26 read-only HW-loop objects: eight
common boiler-manager temperature/flow/pressure/load points and six unit
temperature/flow/firing/pump-proof points on each boiler. The 329-point
Windows-service cutover completed
on 2026-07-24 with advancing 1x simulation ticks, live WebCTRL writes,
confirmed COV recovery, zero blocked requests, and zero residual
faults/forces/scenario after the acceptance exercises.
Final evidence is recorded in
`artifacts/live-ahu-command-center-acceptance-20260724.md`.

The accepted catalog also includes 85 read-only VAV AVs at
local instances `81`–`85` on VAV-1 through VAV-17, with all 233 prior point
identifiers preserved. The dedicated acceptance validated all **85/85**
addresses and all **68/68** design-airflow values, demonstrated a 75%
VAV-3 damper command and 75% feedback, and cleaned up to zero forces at 1x
speed. Live WebCTRL messages and writes are active with zero blocked
requests. The post-restart client state reached 50 active COV subscriptions:
29 confirmed and 21 unconfirmed across peers `.2`, `.5`, `.6`, and `.7`.
Evidence:
`artifacts/live-vav-point-exposure-acceptance-20260723-195858` and
`artifacts/vav-point-exposure-restart-20260723.log`; the final COV snapshot is
`artifacts/live-vav-point-exposure-acceptance-20260723-195858/03-webctrl-cov-recovery.json`.

---

## What this actually does

- Configures **355 BACnet objects** in the checkout under **one supervisory device**
  (`ACI-SIM-SUPERVISOR`, instance `242000`) on the bench simulator host
  `192.168.168.201`, listening on **UDP `47808`**. A transport-level
  `peer_allowlist` drops every BACnet request from any non-allowlisted
  source without a reply — on the verified live bench this is set to
  `192.168.168.1` through `.7` plus the WebCTRL host `192.168.168.200`,
  with a live blocked-request counter on the dashboard.
- Supports **all three WebCTRL refresh strategies on every deployed point** —
  polling (refresh timer < 31 s), UnconfirmedCOV (>= 31 s), and
  ConfirmedCOV (>= 1 min ending :01) — with notification delivery for both
  COV modes verified on earlier accepted builds and a dashboard panel
  showing active subscriptions per mode. The 2026-07-24 cold-restart
  acceptance observed live WebCTRL writes plus renewed confirmed COV
  subscriptions with zero blocked BACnet requests. The preceding 318-point
  cutover reached 50 subscriptions: 29 ConfirmedCOV and 21 UnconfirmedCOV
  across `.2`, `.5`, `.6`, and `.7`.
- Simulates **28 equipment groups**: an AHU, three chillers with condenser
  water and cooling towers, three boilers, an exhaust fan, **17 VAV
  zones**, and the Site, Chiller Manager, and Boiler Manager plant-level
  points. VAV-1 and VAV-2 retain their external zone-temperature
  controller connections; VAV-3 through VAV-17 are fully virtual zones.
  The airside model includes a Building Pressure AI and an exhaust-fan
  VFD AO for positive-pressure training. Mechanical behavior remains
  connected across the model: AHU cooling depends on proven chilled-water
  capacity; VAV reheat depends on proven hot-water distribution; airflow
  responds to AHU proof, damper position, airflow target, and available
  static pressure; a 0% damper yields only 1 CFM leakage with AHU proof,
  while AHU-off airflow is exactly 0.00 CFM; reheat discharge temperature is physically bounded by
  actual hot-water availability; chillers and boilers require proven
  water flow and have real purge/ignition/proof delays; cooling towers
  track wet-bulb and climb toward a high-head trip if the fan stops; hard
  interlocks like Freezestat Trip force real equipment shutdown including
  the OA damper; and failed sensors raise the real BACnet Reliability flag
  WebCTRL displays. AHU-1 also validates the raw WebCTRL economizer request
  against differential OA/RA enthalpy, OA dry bulb/dew point, sensor
  reliability, and a mixed-air low limit. The dashboard shows requested and
  effective damper positions plus its fallback method and FDD result without
  adding or renumbering BACnet points. Fifteen differently sized virtual zones use a physical
  heat balance driven by actual CFM and discharge temperature, effective
  thermal mass, envelope/infiltration, internal load, solar exposure, and
  adjacent-space mixing. VAV-3 through VAV-15 also publish slow, moisture-
  conserving Zone Humidity AIs. Design maximums span 400–2,120 CFM and
  zone areas span 600–2,400 square feet. The live VAV exposure package
  also publishes read-only heating minimum, heating maximum, cooling
  minimum, cooling maximum, and damper-position feedback AVs at local
  instances `81`–`85` on every VAV.
- Adds a fully simulated **AHU-1 command center and duct-static/VFD loop**. WebCTRL writes one
  0.25–2.00 in. H2O setpoint AV; the simulator publishes actual pressure and
  fan-speed feedback over a 0.00-10.00 in. H2O actual-pressure range. The
  process model combines fan laws with the
  design-CFM-weighted feedback of all 17 VAV dampers. A separate command-
  center page provides adjustable P/I/D/interval settings, a detailed AHU
  airflow-path graphic, animated dampers/coils/fans, live MA/RA/SA sensors,
  a floating duct sensor value, and a live setpoint/actual/speed trend. See
  [`docs/DUCT_STATIC_PID_LAB.md`](docs/DUCT_STATIC_PID_LAB.md).
- Adds two instructor-visible AHU safety state machines. A correctly
  commissioned high-static switch trips and latches at 4.0 in. H2O, stopping
  the fans before the representative 5.0-in. H2O training duct-class limit.
  Only an explicit safety-bypass/failure fault can allow pressure above that
  limit, latch structural failure, and trigger the exploded-duct display.
  With the freezestat explicitly bypassed, cooling-coil entering air below
  32 degrees F accumulates a 20-simulated-minute exposure timer, extended to
  60 simulated minutes when cooling-valve command and chilled-water flow are
  proven. Catastrophic states latch until Restart rebuilds equipment state.
  These are training behaviors, not project safety settings or a claim that
  every real duct fails at one pressure.
- Gives WebCTRL one writable **AHU-1 SA Temperature Setpoint AV** (`AV:9001`,
  45-95 degrees F) for both cooling and heating modes. The simulator leaves
  valve control with WebCTRL and models the physical coil response, minimum
  outdoor-air load, valve travel, chilled/hot-water availability, and fan
  heat. A persistent material cooling/heating-valve overlap becomes a
  command-center energy-waste failure after 15 real seconds; a normal
  cross-ramp is allowed for one actuator-travel window.
- Shows **source-backed animated air delivery** in each active VAV space:
  blue for useful cooling, red for useful reheat, and white/gray for
  ventilation-only airflow. The color comes from actual airflow, discharge
  temperature, zone temperature, and proven parent equipment.
- Runs **15-second airside diagnostics** across the VAV fleet. Where a
  meaningful airflow target exists, actual airflow within **±25%** of the
  expected value is treated as tracking; excursions outside that band are
  surfaced as training diagnostics for investigation.
- **Injects faults on demand**: generic mechanics (frozen/drifting/
  offset sensors, stuck/reversed actuators, forced status, and
  transport-level faults like device-offline or intermittent comm), plus an
  explicit safety-bypass/failure mechanic restricted to the simulated AHU
  high-static and freezestat devices, that
  give real meaning to every named fault a technician needs to practice
  diagnosing.
- **Runs timed training scenarios** — ten shipped, including interlock and
  compound-safety failures, stuck actuators, sensor drift/freeze,
  failed-to-prove equipment, whole-device communications loss, and complete
  CHW/HW parent-to-child load-response labs. Events execute against bounded
  simulated time so sequencing remains ordered from 1x through 60x.
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
pip install -r requirements-dev.txt  # only needed to run the test suite
python -m app.main
```

Dashboard at **http://127.0.0.1:8001**. On Windows, use
`scripts\windows\install.bat` then `scripts\windows\run.bat` instead — see
[`PACKAGING.md`](PACKAGING.md) for the full install/firewall/service
walkthrough, including running this as an auto-starting Windows Service.

**Before connecting to the bench network:** edit `config/network.json` to
the verified bench topology — `bind_address` is the simulator host's NIC
IP `192.168.168.201` (never `127.0.0.1` or `0.0.0.0`), `udp_port` is
`47808`, and both `peer_allowlist` and `write_source_allowlist` match the
verified BACnet peers: `192.168.168.1` through `.7` plus the WebCTRL host
`192.168.168.200` (its BACnet connection uses UDP `47809`). Do not replace
this list from an older document without re-verifying live bench traffic.

## The dashboard

An interactive command center built around the expanded building digital
twin, with a floating rail and command bar, compact controls, and the ACI
logo as the brand mark. It remains one self-contained HTML application
backed by a plain REST API: no build step and no CDN. Font Awesome is
self-hosted, and the interface uses the laptop-readable Segoe UI Variable
Text / Segoe UI system stack so the interface remains offline-safe.
The responsive layout keeps essential status, navigation, and control
actions usable at the test-bench display target of **1024×768**, while
expanding naturally on the 1920×1080 remote-support display. See
[`docs/COMMAND_CENTER.md`](docs/COMMAND_CENTER.md) for the digital-twin
layout and diagnostic conventions. Six views:

| View | What's there |
|---|---|
| **Overview** | Interactive building digital twin, engine power toggle, ×1–×60 time-rate control, network/device status with peer-allowlist + blocked-request readouts, site weather and building pressure, active scenario, live COV subscriptions per refresh mode |
| **AHU Command Center** | Detailed AHU-1 airflow path; live MA/RA/SA sensors; economizer, coil, and fan animation; high-static/freezestat safety states; WebCTRL pressure setpoint; actual pressure/VFD feedback; adjustable PID gains/interval; and live training trend |
| **Equipment** | All 28 groups and 329 configured searchable BACnet objects, including the five read-only design/feedback AVs on every VAV, three AHU duct-static AVs, and eight new AHU sensor/safety points, plus VAV tracking diagnostics, value-change flashes, and fault/forced/interlock badges |
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
  network.json, supervisory_device.json                              Bind address, port 47808, peer allowlist, device identity
  devices/*.json, scenarios/*.json, llm/*.json                          Equipment groups, training scenarios, LLM settings
static/command-center.html             Command-center shell served at `/`
static/styles.css, static/app.js       Offline-safe command-center styling and behavior
static/assets/, static/vendor/         Digital-twin artwork plus self-hosted fonts/icons; see THIRD_PARTY_NOTICES.md
scripts/
  generate_phase3_configs.py, generate_point_mapping_workbook.py         Regenerate configs/docs from code, not by hand
  windows/                                                                 install/run/service scripts — see PACKAGING.md
tests/                                 Unit, real BACnet/IP integration (incl. both COV modes + peer allowlist), regression, diagnostics, restart, AHU safety/duct-static/PID, parent dependencies, and LLM orchestration
```

## Testing

```bash
PYTHONPATH=. pytest tests/ -v
```

The historical live 321-point baseline passed 129 tests. The live 329-point
release passes 165 tests.
The suite includes real integration tests against actual BACnet/IP
traffic (not mocked): confirmed/unconfirmed COV notification delivery,
peer-allowlist enforcement, flow-proving interlocks, and audit-fix behaviors,
plus the real Ollama client's HTTP contract (mocked transport, real
request/response parsing). Live-traffic testing has caught
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
| [`docs/AUDIT_2026-07-23.md`](docs/AUDIT_2026-07-23.md) | Live-bench risks, configuration hardening, GUI review, agent feasibility, and remaining work |
| [`docs/COMMAND_CENTER.md`](docs/COMMAND_CENTER.md) | Expanded 28-group digital twin, VAV topology, pressure behavior, diagnostics, and responsive UI guidance |
| [`docs/TRAINING_P0_LAYER.md`](docs/TRAINING_P0_LAYER.md) | Baselines, deterministic restore, priority reconciliation, preflight, evidence, scoring, and role permissions |
| [`docs/HVAC_REALISM_MODEL.md`](docs/HVAC_REALISM_MODEL.md) | Parent-equipment dependencies, air-delivery colors, thermal ranges, sequence guidance, and realism backlog |
| [`docs/DUCT_STATIC_PID_LAB.md`](docs/DUCT_STATIC_PID_LAB.md) | AHU command-center device order, BACnet point contract, pressure/freezestat physics, safety-bypass lessons, PID tuning, restart lifecycle, and cutover plan |
| [`docs/REALISM_CUTOVER_CHECKLIST.md`](docs/REALISM_CUTOVER_CHECKLIST.md) | Controlled Windows-service restart, local API, physical-chain, WebCTRL, UI, and rollback checks |
| [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) | Notices and license terms for the self-hosted dashboard fonts and icons |
| `ACI_BACnet_Simulator_Point_Mapping.xlsx` | Every BACnet object, address, and direction (not in this repo by default — generate with `scripts/generate_point_mapping_workbook.py`) |

## Known limitations

Honestly scoped, not hidden — full list in `HANDOFF.md` §6, briefly:

- The supervisory device instance (`242000`) is the verified bench value.
- No occupancy scheduling or internally generated people/lighting/plug loads.
- The command-center building is a **typical training layout**, chosen to
  make upstream/downstream HVAC relationships easy to demonstrate. It is
  not an as-built record, construction design, equipment-sizing model,
  ventilation calculation, or approved sequence of operations.
- The startup duplicate-instance check is skipped on loopback binds (a
  Windows loopback-broadcast quirk kills the UDP transport); its behavior
  on the real bench NIC's broadcast domain is still unverified.
- Ollama `0.32.1` and small local models are installed, but the laptop's
  current 8 GB memory configuration is not suitable for a reliable
  always-on local agent alongside WebCTRL and the simulator. A
  local-tool/cloud-inference arrangement is recommended.
- Dynamic equipment management (adding/removing simulated equipment at
  runtime via the LLM) is deliberately not built yet — see
  `PHASE6_REVIEW.md` for why that's a separate, higher-risk phase.

## Author

Built for Jeff Jenkins, Automated Controls Inc.
