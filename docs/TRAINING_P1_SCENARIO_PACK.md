# P1 Training Scenario Pack

## Delivered scope

The first P1 pack expands the catalog from 10 to 16 graded scenarios without
adding or renumbering BACnet objects. It uses the existing coupled physics and
P0 baseline/preflight/evidence layer.

| Scenario | Primary lesson | Duration | Speed |
|---|---|---:|---:|
| `economizer_dual_enthalpy_transition` | Favorable OA to warm-humid lockout | 480 s | 10x |
| `economizer_damper_stuck` | Command versus effective actuator/process | 360 s | 10x |
| `chw_excessive_bypass_low_flow` | Common flow versus evaporator-barrel flow | 540 s | 10x |
| `building_negative_pressure_recovery` | Supply/exhaust imbalance and recovery | 480 s | 10x |
| `bacnet_priority3_conflict` | Accepted write versus effective priority | 360 s | 10x |
| `chw_pump_proof_loss_comfort_complaint` | Child comfort symptom from parent loss | 540 s | 10x |

The outcome catalog now contains 39 weighted time-window assertions across all
16 scenarios.

## Calibrated physical evidence

All scenarios were executed through the production equipment graph in bounded
one-simulated-second steps. The regression verifies a 100 percent machine score
for every new lab.

- Favorable 50 F / 30 percent RH air pulled mixed air to about 50.7 F against
  roughly 73.1 F return air.
- The warm-humid transition produced an explicit lockout while the 100 percent
  request remained visible; mixed air stayed near return air.
- The stuck economizer held effective damper position at 20 percent until the
  physical fault cleared, after which mixed air approached outside air.
- An 80 percent chiller bypass retained 300 GPM common flow while dropping
  evaporator flow below permit and removing chiller proof. Proof returned only
  after bypass closure and the normal startup delay.
- Full EF-1 exhaust moved building pressure from about +0.014 to -0.112 in.
  H2O; reducing exhaust recovered it to about +0.014 in. H2O.
- Priority 3 held the static setpoint at 0.25 in. H2O despite lower-priority
  authority. Relinquish restored the external command or 1.0 in. H2O default.
- Forced-false CHW-pump proof left its command and the 80 percent AHU valve
  request on while common flow and chiller proof fell to zero and SAT rose
  above 74 F.

## Instructor use

Run named-baseline restore and reconcile WebCTRL priorities before preflight.
Do not reveal internal fault names during blinded diagnosis. Students should
build their conclusion from command, proof, flow, and thermal consequence.

The building-pressure lab is not a smoke-control or life-safety sequence. Its
representative pressure values must not be applied as universal acceptance
limits. The CHW bypass represents an evaporator-barrel bypass and should be
shown that way on the training schematic.

## Deferred model-backed pack

The next pack should add explicit physics/state before authoring its labs:

1. chiller and boiler minimum run, minimum off, and anti-recycle timers;
2. lead/lag staging and rotation;
3. boiler low-flow/high-limit lockout and reset;
4. shared condenser-water header/cell staging;
5. occupancy, internal load, schedules, and door/window disturbances;
6. energy telemetry such as chiller kW/COP/PLR and boiler efficiency.

Those scenarios should not be simulated through labels alone; their outcome
criteria need corresponding physical state and observable evidence.

## Verification

- Six scenario files validate against the deployed 355-point catalog.
- Every P1 outcome passes against the real equipment graph.
- Focused scenario/training tests pass.
- Full suite target: 199 passing tests.
- Production application-factory acceptance loaded 28 equipment groups, 355
  BACnet objects, six baselines, and all 16 scenarios. Neutral restore settled
  and all six P1 preflights returned no blockers.
