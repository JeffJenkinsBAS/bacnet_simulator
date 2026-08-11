# P0 Training Layer

## Delivered scope

The training layer turns the coupled HVAC simulator into a repeatable lab
runtime. It adds:

- six named, versioned seasonal/operational baselines;
- deterministic graph rebuild, bounded settling, and in-memory checkpoint restore;
- explicit inspection, retention, or release of WebCTRL/external priorities;
- scenario preflight with blocking and warning conditions;
- internally sampled session evidence using simulated and wall time;
- time-window assertions and weighted automated scores for every shipped scenario;
- durable JSON evidence under `artifacts/training/<run-id>.json`;
- short-lived student and instructor role tokens around every mutation endpoint;
- a Training Session workspace in the command center.

## Instructor PIN

Set `ACI_SIM_INSTRUCTOR_PIN` in the Windows service environment to manage the
PIN explicitly. If it is absent, the simulator generates a stable six-digit
local PIN at:

`logs/training-instructor-pin.txt`

The generated file is runtime state and is excluded from source control by the
existing `logs/` ignore rule. Student login does not require a PIN. Student
tokens can read every telemetry/training endpoint and add evidence markers,
but receive HTTP 403 from simulator mutation endpoints.

## Baselines

Definitions live in `config/training/baselines.json`:

| ID | Purpose |
|---|---|
| `neutral_commissioning` | BACnet, priorities, COV, command/proof, recovery |
| `summer_hot_humid` | Mechanical cooling and warm-humid economizer lockout |
| `shoulder_economizer` | Favorable outside-air enthalpy |
| `winter_heating` | Boilers, reheat, freeze protection, infiltration |
| `after_hours_low_load` | Overrides and simultaneous heating/cooling |
| `destructive_cold_weather` | Instructor-only frozen-coil demonstrations |

Build and restore performs this transaction:

1. inspect external BACnet priority slots;
2. require an explicit `retain` or `release` decision when any exist;
3. stop the physics loop;
4. clear simulator-owned scenarios, faults, and Priority 3 writes;
5. preserve priorities 1 and 2 in every mode;
6. optionally relinquish external priorities 4 through 16;
7. rebuild the equipment graph without rebuilding the BACnet device;
8. apply baseline weather/commands;
9. settle in bounded one-simulated-second steps;
10. capture equipment internals and non-commandable point values;
11. restart the loop if it was previously running.

The first restore creates an in-memory checkpoint. Subsequent checkpoint
restores rewind thermal/moisture inventories, actuator/proof timers, PID state,
zone state, safety latches, and published simulator outputs. A failed restore
rolls back the prior graph, clock, outputs, and baseline declaration.

An explicit retain decision fingerprints the accepted external priority state.
Unchanged WebCTRL commands are then a preflight warning, not a permanent
blocker. Any later change to an external priority slot invalidates that
reconciliation and blocks the next graded start until the instructor chooses
retain or release again.

## Preflight

`GET /api/training/preflight/{scenario_id}` blocks a graded start for:

- active scenario effects;
- active faults/forces;
- absent or consumed baseline;
- unreconciled external priority slots.

It warns for an engine stop or speed that differs from the scenario's
recommendation, and returns prerequisites, observations, baseline/version,
priority detail, and estimated accelerated duration.

The scenario start API requires a matching active training session when the
P0 layer is enabled. This prevents bypassing preflight from the older
Operations scenario cards.

## Evidence and scoring

During an active session the engine records selected observations after every
bounded physics step, not on the browser's slower polling interval. Each sample
has wall time, total simulated time, scenario-relative elapsed time, and point
values. The action log captures session lifecycle, authenticated API mutations,
student/instructor markers, scenario start, and newly observed WebCTRL BACnet
commands.

`config/training/outcomes.json` defines assertions with:

- point-to-constant or point-to-point comparisons;
- offsets/tolerances;
- start/end windows;
- required persistence duration;
- scoring weight.

Finishing an attempt converts unresolved assertions to failed, calculates the
weighted score, and writes a complete JSON bundle. The bundle is reproducible
without consulting current live state.

## API workflow

1. `POST /api/training/auth/login`
2. `GET /api/training/priorities`
3. `POST /api/training/baselines/{id}/restore`
4. `GET /api/training/preflight/{scenario_id}`
5. `POST /api/training/sessions`
6. `POST /api/scenarios/{scenario_id}/start`
7. `POST /api/training/sessions/{run_id}/markers` as needed
8. `POST /api/training/sessions/{run_id}/finish`
9. `GET /api/training/sessions/{run_id}/evidence`

Send the returned token as `Authorization: Bearer <token>`. Tokens expire after
eight hours and are intentionally memory-only, so a service restart requires a
new login.

## Security boundary

Role authentication protects the REST mutation surface. It does not replace
the BACnet peer allowlist or WebCTRL's own user/priority controls. WebCTRL
commands still arrive over BACnet and are recorded/reconciled by priority.
Keep the dashboard bound to the local bench network and do not publish the PIN
or evidence bundles externally.

## Verification

- Four dedicated P0 tests cover explicit priority reconciliation,
  deterministic checkpoint rewind, one-second evidence/time-window scoring,
  durable evidence, and student/instructor enforcement.
- 187 non-network tests pass.
- 11 BACnet integration tests pass.
- Total: 198 passing tests.
- Production application-factory acceptance passes with 28 equipment groups,
  355 BACnet objects, 10 scenarios, six baselines, instructor login, neutral
  restore/settle, and blocker-free preflight.
- Live service acceptance retained 76 WebCTRL priority slots, proved changed
  priority fingerprints block until re-reconciled, denied a student mutation
  with HTTP 403, accepted a student evidence marker, and persisted two
  one-second samples. BACnet ended with zero blocked messages and zero COV
  notification failures.

The follow-on P1 pack expands this foundation from 10 to 16 graded scenarios;
see `TRAINING_P1_SCENARIO_PACK.md`.
