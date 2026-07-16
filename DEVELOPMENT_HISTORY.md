# ACI BACnet Building Simulation Platform — Phase 4: Scenario Library &amp; Instructor Panel

Fault injection framework, timed scenarios, and an Instructor Panel UI, all
built on the Phase 3 single-supervisory-device architecture (unchanged: one
BACnet device, 143 objects, 16 equipment groups on UDP 47808).

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

Dashboard at **http://127.0.0.1:8000**.

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
