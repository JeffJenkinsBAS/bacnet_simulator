# ACI BACnet Building Simulation Platform — Phase 4: Scenario Library &amp; Instructor Panel

Fault injection framework, timed scenarios, and an Instructor Panel UI, all
built on the Phase 3 single-supervisory-device architecture. At this
historical Phase 4 milestone the inventory was one BACnet device, 143
objects, and 16 equipment groups on UDP 47808. Later milestones are
appended below rather than rewriting that history.

## What's new

**Fault injection (`app/faults.py`)** — 11 generic mechanics rather than
one code path per named fault in the original spec's 24-item list. Every
named fault (frozen sensor, sensor drift, valve commanded but stuck, VFD
fault, device offline, etc.) maps to one of these mechanics plus a target
point and parameters — see the mapping table in `faults.py`'s module
docstring. Applied entirely through `GroupView` (the same chokepoint that
made the single-device merge transparent to equipment models), plus four
transport-level mechanics (`device_offline`, `slow_response`,
`write_rejected`, `intermittent_comm`) applied in `transport.py` since they
affect the whole device rather than one point.

**Scenarios (`app/scenario.py`, `config/scenarios/*.json`)** — initial
conditions plus a timed event list, executed against simulated time so a
scenario behaves identically at 1x or 20x speed. Six shipped scenarios
cover an interlock trip, a stuck actuator, a slow sensor drift, a
failed-to-prove boiler, a frozen sensor, and a whole-device comm loss —
chosen to be genuinely different failure *patterns*, not just different
equipment.

**A real design correction made while building this**: "trip an interlock"
(like Freezestat Trip) needs to be a **real BACnet write** to the actual
commandable object, not a fault-layer override — otherwise the equipment
would react correctly while the real object WebCTRL reads would still show
untripped, breaking the realism the whole platform exists for. `set_value`
now checks whether a target point is BACnet-writable: writable points get
a real `WriteProperty` at priority 3; non-writable sensor points get a
`stuck_value` output override instead, since they were never writable to
begin with. This is documented in `scenario.py`'s docstring, not just
fixed silently.

**Instructor Panel UI** — the dashboard is now tabbed: Dashboard,
Equipment &amp; Points, Instructor Panel, Logs. The Instructor Panel has
scenario cards with Run/Stop/Reset, a manual fault-injection form (group +
alias autocomplete + fault type + value/rate), an active-faults list with
per-fault Clear buttons, and Force/Release Value controls. The points table
now shows FORCED and fault-type badges per point. A prominent red
"STOP ALL SIMULATION" control at the top of the page stops the engine and
clears every fault, force, and running scenario in one confirmed action.

## Quickstart

Same as Phase 3:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m app.main
```

Dashboard at **http://127.0.0.1:8001**.

## Running the tests

```bash
PYTHONPATH=. pytest tests/ -v
```

24 tests. New in Phase 4 (`tests/test_faults_and_scenarios.py`): each fault
mechanic tested against a real running equipment model and real BACnet
objects (not mocks) — frozen value holding output constant, offset/drift
shifting a published sensor reading, stuck_value freezing what the
equipment model reads while the real object keeps accepting new writes,
forced_status overriding a computed boolean, reversed_actuator inverting a
commanded percentage — plus a scenario-JSON validator that checks every
shipped scenario only references real fault types and actions.

Also fixed in this pass: `test_bacnet_integration.py` had a latent
test-isolation bug (all tests shared one fixed UDP port pair, reused
sequentially) that surfaced as intermittent `AbortPDU: no-response`
failures once the suite grew larger. Fixed with per-test port allocation.

All of Phase 4 also re-verified live against a separately-running process,
not just the test suite: ran the VAV-1 reheat-valve-stuck scenario end to
end, then wrote a real BACnet command to the valve from a second process
and confirmed the object itself accepted it (showed 90%) while Discharge
Temp stayed flat at 55°F — exactly the mismatch the scenario is designed to
teach a technician to recognize.

## What's NOT in Phase 4 (by design)

- **Duplicate BACnet instance, incorrect BACnet network number, incorrect
  units, and "priority-array override" as a distinct fault** — flagged as
  not implemented in `faults.py`'s module docstring rather than silently
  omitted. Priority-array override is really just the Force Value feature
  at a chosen priority, not a separate mechanic; the other three would need
  either a decoy second device or runtime-mutable network/units properties,
  neither built yet.
- **Completion criteria and student objectives are informational text**,
  shown to the instructor, not auto-graded against simulated state.
- **Occupancy** is not modeled — a scenario's `initial_conditions.occupied`
  key (from the original spec's example) is accepted but logged as
  unrecognized, not silently ignored and not pretended to work.
- Windows packaging, user/instructor guides, point-mapping worksheet,
  troubleshooting guide — Phase 5.

## Project layout additions since Phase 3

```
app/
  faults.py                 FaultManager, FaultType, the mapping-table docstring
  scenario.py                 Scenario/ScenarioEvent schema, ScenarioEngine
config/scenarios/*.json         6 shipped scenarios
tests/test_faults_and_scenarios.py   Fault mechanic tests + scenario JSON validation
static/index.html                     Tabbed dashboard with the Instructor Panel
```

## API additions

```
GET  /api/fault-types                       list of the 11 fault mechanics
GET  /api/faults                              currently active faults
POST /api/faults/set                            {fault_type, group_id, alias, parameters}
POST /api/faults/clear?fault_id=...               clear one fault
POST /api/faults/clear-all                          clear every fault
POST /api/force                                       {group_id, alias, value}
POST /api/release                                       {group_id, alias}
GET  /api/scenarios                                       list shipped scenarios
GET  /api/scenarios/{id}                                    full scenario detail
POST /api/scenarios/{id}/start                                 start (stops any running one first)
POST /api/scenarios/stop                                          stop, faults from it are cleared
POST /api/scenarios/reset                                            stop + clear ALL faults/forces
GET  /api/scenarios/status/current                                     current run status
POST /api/simulation/stop-all                                            the big red button
```

---

## Subsequent milestone — 2026-07-23 command-center expansion

The project later expanded the 16-group / 143-point Phase 4 baseline into
an interactive, multi-zone building digital twin while retaining the
single-supervisory-device architecture and verified BACnet transport
behavior.

### Expanded equipment and point catalog

- **28 equipment groups / 219 BACnet points** under
  `ACI-SIM-SUPERVISOR`, device instance `242000`.
- **VAV-1 through VAV-17** replace the five-zone baseline. VAV-1 and VAV-2
  retain their external zone-temperature controller connections to the
  test-bench wall; VAV-3 through VAV-17 are virtual zones with simulated
  zone temperatures.
- The Site group gained a **Building Pressure AI** and the Exhaust Fan
  gained a **VFD AO**. Together they provide the process behavior for
  training a positive-building-pressure sequence: AHU supply raises
  pressure, enabled exhaust lowers it in proportion to VFD command, and
  the external BAS/EIKON logic owns the final setpoint and tuning.

The larger floorplate is a typical training arrangement selected to make
plant, AHU, exhaust, pressure, and multi-zone relationships visible. It is
not an as-built record, construction design, equipment-sizing model, or
approved project sequence.

### Diagnostics and command-center UI

`app/diagnostics.py` and the command-center API contract add a
wall-clock diagnostic layer without changing the deterministic 1 Hz
simulation model:

- command/status and VAV airflow mismatches must persist for **15 real
  seconds** before becoming failures;
- a VAV tracks normally inside an inclusive **±25%** band around airflow
  setpoint;
- the interactive UI maps systems, equipment, and all 17 VAV locations to
  live point sources and lets an instructor move from the building view
  to detailed equipment/point state;
- the responsive layout keeps essential navigation and safety actions
  available at the 1024×768 bench resolution.

The illustrative building view uses the generated
`static/assets/building-digital-twin.png` asset. Font Awesome Free 7.3.1
and Rajdhani are self-hosted for offline operation; their notices are in
`THIRD_PARTY_NOTICES.md`.

### Generated artifacts and documentation

- `scripts/generate_phase3_configs.py` now produces all 28 device configs,
  including VAV-1 through VAV-17 and the new pressure/VFD points.
- `config/building_layout.json` defines the command-center topology,
  hotspots, display locations, and diagnostic sources.
- `ACI_BACnet_Simulator_Point_Mapping.xlsx` was regenerated for all 219
  points.
- `README.md`, `HANDOFF.md`, `docs/COMMAND_CENTER.md`, and
  `THIRD_PARTY_NOTICES.md` document the current topology, UI conventions,
  training-only scope, and bundled-asset licenses.
- The expanded automated suite passes **87/87 tests**, including full
  catalog construction, layout/source validation, command-center API
  shape, the 15-second delay, and inclusive ±25% VAV tracking boundaries.

### Parent-equipment realism and animated space air

A follow-on realism pass connected the plant, AHU, terminal, and zone
models into one dependency chain while leaving WebCTRL in command:

- AHU mechanical cooling now requires proven chilled-water capacity, and
  VAV reheat requires proven hot-water distribution.
- Duct static requires AHU proof, VAV airflow follows a square-root pressure
  relationship, and actual flow is bounded by both physical capacity and the
  WebCTRL airflow setpoint.
- VAV discharge temperature responds to actual upstream SAT, airflow, and
  hot-water capacity. Virtual zone temperatures now respond to delivered
  sensible effect plus outdoor-envelope and internal loads.
- The command center derives cooling, heating, ventilation, or off directly
  from actual delivery. A generated airflow-ribbon image animates in the
  space and is colored blue, red, or white/gray.
- VAV airflow diagnostics are inhibited during an upstream AHU outage,
  avoiding a cascade of misleading terminal failures.
- CHWS and HWS reset units/ranges, scenario prerequisites, common chilled
  water flow capacity, and chiller isolation behavior were corrected.

The automated suite passes **87/87 tests**. The realism pass completed its
controlled Windows-service cutover on 2026-07-23. Live acceptance exercised
AHU-off VAV inhibition, the 15-real-second command/proof failure timer,
neutral ventilation, the full chilled-water/AHU/VAV cooling chain, and the
full boiler/distribution/VAV reheat chain. Browser acceptance verified the
blue, red, and white/gray space-air states against live backend data.

### Live cutover verification

The earlier 16/143 build was verified through a live NSSM service restart,
WebCTRL writes, and recovery of 26 active COV subscriptions. On
2026-07-23 the expanded 28/219 checkout completed its controlled service
cutover:

1. `ACIBACnetSimulator` restarted and `/api/status` reported 28 groups /
   219 points;
2. the live process accepted writes from `192.168.168.2`, a WebCTRL read
   from `192.168.168.200`, and COV traffic from `192.168.168.5`, with zero
   blocked requests;
3. Building Pressure AI responded to Exhaust Fan VFD AO;
4. a VAV airflow mismatch became a failure after 15 real seconds and
   recovered after release; and
5. the BACnet priority-release path was corrected and regression tested.

The later parent-equipment cutover reused the verified bind, device instance,
and peer allowlists. Active COV subscriptions rebuilt from 13 during the
acceptance sequence to 26 before final handoff, with zero blocked requests
and no application errors. VAV-3
measured 1,000 CFM with 57.0 F cooling discharge, 300 CFM with 95.0 F
heating discharge, and 800 CFM with 75.2 F neutral ventilation. All
temporary instructor overrides were relinquished and the simulator returned
to 1x with an empty injected-fault list.

The historical count of 26 active subscriptions applies to the 16/143
baseline. The current WebCTRL client appears to reuse its subscription
process identifier, so expanded-build evidence records confirmed COV
traffic without asserting that the old active-count total was restored.

---

## Subsequent milestone — 2026-07-23 AHU SAT reset and coil-energy diagnostic

The live catalog increased from 219 to **220 BACnet objects** by adding one
commandable AHU-1 `SA Temperature Setpoint` at `analog-value:9001`. The point
is intentionally shared by cooling and heating modes, with a 45-95 F range
and 55 F relinquish default. WebCTRL remains command authority for the
setpoint and both coil valves.

The AHU physical model gained minimum outdoor-air load, independent
cooling/heating actuator travel, plant-dependent coil response, and a
characterized heating-valve approximation. The calibrated live results were:

- 70 F OA, 15% minimum OA, 85 F SATSP, 50% heat: **85.06 F SAT**;
- 40 F OA, the same 50% heat: **80.14 F SAT**;
- 40 F OA, 72% heat: **85.07 F SAT**; and
- 80 F OA, 55 F SATSP, 44 F CHWS, 100% cooling: **56.0 F SAT**.

The command center now distinguishes a legitimate cross-ramp from persistent
simultaneous conditioning. A cross-ramp gets one 60-simulated-second
actuator-travel window. Steady cooling/heating commands above 10% enter the
15-real-second diagnostic timer and then outline AHU-1 red with both command
values and a WebCTRL priority-lock warning.

The controlled service restart returned at 28 groups / 220 points with
WebCTRL traffic and zero blocked requests. The final acceptance report under
`artifacts/live-ahu-sat-acceptance-20260723-150930` passed all checks and
left zero forces, 1x speed, no injected faults, and no active scenario. The
updated automated suite passes **92/92 tests**.

---

## Staged milestone — 2026-07-23 VAV diversity, zone physics, and humidity

The configured catalog increases from 220 to **233 BACnet objects** by adding
Zone Humidity AI:3 only to VAV-3 through VAV-15. Those new global identifiers
are AI:13003 through AI:25003. A regression fingerprint proves that all 220
pre-existing group/alias/object-type/global-instance identifiers are
unchanged.

Every VAV now has a representative space type, unique area, design cooling
maximum, occupied minimum, and heating maximum. The virtual-zone schedule
spans 600–2,400 square feet and 400–2,120 CFM, with varied initial
temperatures, thermal capacitance, envelope UA, solar exposure, internal
load, occupancy density, infiltration, mixing, and humidity storage.

The former temperature shortcut was replaced with an analytical zone heat
balance driven by actual CFM and DAT plus real space loads. Humidity is
integrated as humidity ratio; AHU wet-coil cooling may remove moisture,
while terminal reheat changes dry-bulb/RH without inventing moisture removal.
The command center now exposes zone sizing/humidity and uses ASCII-safe
Unicode escapes/entities so strict JavaScript decoding cannot produce
`Â°F`.

The complete automated suite passes **104/104 tests**. This milestone is
staged but not yet live: at the operator's request, no administrator/UAC
restart was attempted. The last verified Windows-service process remains the
28/220 build until an approved restart and WebCTRL read/write/COV acceptance
confirm the 28/233 catalog.

The pre-cutover audit then closed four additional correctness gaps without
changing any BACnet identifier: VAV-11's airflow metadata now covers its
2,120-CFM design value; analog command bounds are published and enforced;
Stop/Reset relinquishes tracked priority-3 overrides and reports them through
the point API; and virtual-zone physical temperature is isolated from sensor
fault substitution. VAV profile semantics also reject impossible physical
parameters at startup.

---

## Live milestone — 2026-07-23 28/233 cutover, vault design plan, and readability

The 28-group / 233-point configuration completed its controlled live cutover
on the Test Bench laptop. The simulator returned at 1x with 28 equipment
models, no faults, no priority overrides, and no blocked traffic. Live
WebCTRL traffic resumed, and a later inspection found 50 active confirmed
and unconfirmed COV subscriptions. The full realism acceptance report is in
`artifacts/live-realism-acceptance-20260723-170906`; the repeated AHU SAT and
coil-energy acceptance is in
`artifacts/live-ahu-sat-acceptance-20260723-173703`.

The Obsidian generator now produces a master building design plan, an
authoritative BACnet point-allocation standard, the complete per-zone VAV
schedule, a current-status note, and a focused vault home page. The reviewed
50-file copy set was merged into the live Test Bench vault. All 110 generated
files match staging, all 20 live-only items remain in place, and no
`.obsidian/**` file was copied or replaced.

The command center and legacy interfaces now use Segoe UI Variable Text /
Segoe UI throughout, with a 17 px body size and larger small labels. Browser
acceptance at 1024x768 confirmed no horizontal overflow and no malformed
degree-Fahrenheit text.

A final VAV airflow correction makes damper and parent state authoritative:
a 0% damper publishes 1.0 CFM of modeled leakage while AHU proof is present,
and an unproven/stopped AHU publishes exactly 0.00 CFM. The complete suite
passes 105 tests. A reviewed administrator restart loaded the correction,
and `scripts/live_vav_airflow_acceptance.ps1` passed all checks with evidence
in `artifacts/live-vav-airflow-acceptance-20260723-174258`.

---

## Live milestone — 2026-07-23 VAV design limits and damper feedback

The project and live Windows service expanded from the verified 233-point
catalog to **318 BACnet objects** without moving or changing any existing
identifier.
Every VAV receives the same consecutive, simulator-owned, read-only AV
contract: heating minimum CFM at AV:81, heating maximum CFM at AV:82, cooling
minimum CFM at AV:83, cooling maximum CFM at AV:84, and effective simulated
damper-position feedback at AV:85. The exact delta is 85 objects across 17
VAVs, and the compatibility fingerprint for all prior 233 objects is
unchanged.

The VAV model publishes the four configured design values and separates
AO:20 command from AV:85 feedback. Feedback follows the effective actuator
position after input faults, while diagnostics use the registered AV:85 value
so a fault on the feedback signal remains visible to WebCTRL and the command
center. BACnet transport coverage confirms writes to AV:81 through AV:85 are
rejected and do not alter their values.

The full point-mapping workbook and Obsidian building/point-allocation
documents were regenerated from the 28/318 configuration. The reviewed
33-file vault copy set was merged: all 110 generated files now match
staging, all 20 live-only items remain, and `.obsidian/**` was not replaced.
The complete suite passes **107/107 tests**. After the approved controlled
restart, `/api/status` reported 28 groups / 318 points and the dedicated
acceptance passed: all 85/85 new addresses and all 68/68 design-airflow
values validated, VAV-3 produced 75% feedback from a safe 75% damper
command, and cleanup restored zero forces at 1x speed. Live WebCTRL messages
and writes were active with zero blocked requests. COV recovery reached 50
active subscriptions: 29 confirmed and 21 unconfirmed across `.2`, `.5`,
`.6`, and `.7`. The final snapshot is
`artifacts/live-vav-point-exposure-acceptance-20260723-195858/03-webctrl-cov-recovery.json`.

Evidence is in
`artifacts/live-vav-point-exposure-acceptance-20260723-195858`; the reviewed
service action is recorded in
`artifacts/vav-point-exposure-restart-20260723.log`.

---

## Live milestone — 2026-07-24 AHU duct-static PID lab and cold restart

The configured catalog expands from the verified live 318-point baseline to
**321 objects** by adding only AHU-1 Duct Static Pressure Setpoint AV:9002,
Duct Static Pressure AV:9003, and Supply Fan Speed Feedback AV:9004. A new
fingerprint regression proves every prior identifier is preserved.

The AHU supply fan now uses an internally simulated VFD and a direct-acting
duct-static PID. The plant applies fan-law pressure response and the
design-CFM-weighted feedback of all 17 downstream VAV dampers. At fixed fan
speed, opening dampers lowers pressure and closing dampers raises it; the
controller increases speed to recover a pressure deficit. Fan, duct, and
sensor dynamics prevent instantaneous response. Command or proof loss forces
pressure and speed to exact zero and records a zero off-state trend.

The command center gained a separate training page with run command/proof,
actual/setpoint/error, VFD output, aggregate terminal demand, an animated
AHU/common-duct/two-thirds sensor graphic, editable P/I/D/interval controls,
and a setpoint/actual/fan-speed trend.

A guarded Restart action now cold-resets the simulator in process: scenario
and faults are cleared, tracked priority writes are drained, the BACnet
application and object database are rebuilt, equipment models/controller
memory are recreated, speed and elapsed time return to defaults, and the
BACnet link is rebound for WebCTRL discovery/COV renewal. Registry rebuilds
also clear old reliability bookkeeping so a future injected sensor fault
cannot be hidden by stale object references.

The complete automated suite passes **129/129 tests**, including new
pressure-plant, PID, point-contract, API restart, and dashboard regressions.
The controlled administrator restart completed successfully and the Windows
service now reports **28 groups / 321 points**. Live WebCTRL writes resumed
with zero blocked messages. The GUI Restart action then completed a second
cold BACnet rebind, restored default PID tuning and 1x speed, and WebCTRL
commands plus confirmed COV subscriptions recovered.

---

## Live release — 2026-07-24 AHU command center and safety physics

The next configured catalog advances from the verified 321-point baseline to
**329 BACnet objects** while preserving every prior object identifier. The
exact additions are AHU-1 mixed-air humidity `AI:9005`, supply-air humidity
`AI:9006`, cooling-coil entering-air temperature `AI:9007`, automatic
high-static trip `BI:9044`, supply-duct structural failure `BI:9045`,
automatic freezestat trip `BI:9046`, cooling-coil freeze condition `BI:9047`,
and cooling-coil burst/flood alarm `BI:9048`.

The existing duct-static actual remains `AV:9003`; its range expands from
0.00-5.00 to 0.00-10.00 in. H2O so a deliberately bypassed safety lesson can
publish the overpressure rather than clip it. Existing writable
`BV:9100`/`BV:9101` remain external/manual interlock inputs.

The duct-static page is redesigned as a complete AHU command center. Its
graphic follows the actual device order: OA economizer, prefilter, RA
sensors/smoke and return fan, mixing/MA sensors, preheat, downstream
serpentine freezestat, cooling coil, reheat coil, supply fan, SA
sensors/smoke, high-static switch, graphical duct break, and the two-thirds
duct sensor. Live damper, coil-valve, and fan state drives animation.

The safety model distinguishes healthy protection from deliberately defeated
protection. A healthy high-static switch latches at 4.0 in. H2O and stops
both fans before the representative 5.0-in. H2O training duct-class limit.
Only a restricted instructor safety-bypass/failure fault can permit the
structural-failure state and exploded-duct/red-flash display. A healthy
freezestat latches protective shutdown. When explicitly bypassed,
cooling-coil entering air below 32 degrees F accumulates 20 simulated minutes
without useful chilled-water flow or 60 simulated minutes with cooling-valve
command and proven chilled-water flow before freeze and burst/flood latch.
The longer flow-proven path is a training approximation, not a freeze-design
calculation.

Automatic safety and catastrophic states use the guarded Restart control as
their manual-reset boundary. Safety exposure uses simulated time; the
existing 15-second command/status diagnostic remains wall-clock based.

The same candidate adds a computed airside economizer state machine without
changing the BACnet catalog. `AO:9023` remains the raw WebCTRL request while
an effective damper position is limited by dual OA/RA enthalpy, OA dry-bulb
and dew-point high limits, sensor reliability fallbacks, fan/safety state,
and a 45/47-degree-F mixed-air low limit. The fallback order is dual
enthalpy, single enthalpy, differential dry bulb, fixed dry bulb, then
unavailable when OAT is unreliable. Three minutes at full effective stroke
with SAT still above setpoint permits integrated cooling. The AHU command
center exposes requested/effective position, psychrometric values, limiting
reason, proof, integrated state, and FDD flags.

The controlled service cutover is complete. The checkout passes **155/155
automated tests**; the 329-point workbook and reviewed Obsidian merge are
complete. The live service reports 28/329 at 1x, WebCTRL writes and confirmed
COV recovery were observed with zero blocked traffic, both bypassed
catastrophe exercises passed, and Restart clears latches/training state while
preserving the BACnet object graph and active COV subscriptions.
