# Simulation Design and Training Audit — 2026-08-11

## Scope and result

This audit covered every configured equipment model, the one-second causal
parent/child graph, the scenario engine, all shipped lessons, command-center
diagnostics, scenario APIs/UI, reset semantics, and the training coverage
needed by the coupled CHW, HW, AHU, VAV, zone, exhaust, and site physics.
Equipment-model findings were checked against the parent/child contract in
`HVAC_REALISM_MODEL.md`, corrected in the implementation, and verified both
with focused regressions and a complete cooling-to-heating acceptance run.

The shipped library now contains ten catalog-validated scenarios. Two new
commissioning labs exercise complete parent-to-child energy paths, and the
existing equipment-fault lessons now establish the plant and airflow
prerequisites needed for their claimed outcomes. Scenario time remains
simulated time, while failure confirmation remains wall time.

## Physics corrections made

### Simulation clock and causal ordering

- Acceleration now changes simulated elapsed time without changing the physics
  integration interval. Every update is divided into steps no larger than one
  simulated second, including at 60x.
- Site, source equipment, plant headers, AHU, exhaust, and VAV/zone children are
  evaluated in causal order on every bounded step. Start/proof timers, actuator
  travel, scenario events, and thermal inventories no longer skip intermediate
  states under acceleration.
- BACnet analog-write validation now explicitly rejects NaN and positive or
  negative infinity. Ordinary min/max comparisons do not reject NaN; a live
  startup write exposed that gap by temporarily making point and command-center
  JSON non-serializable.

### Chilled water, chillers, and condenser water

- Chiller proof uses a 45-second start sequence and compressor capacity ramps
  over 30 seconds instead of appearing instantly.
- Failed chiller, CHW-pump, condenser-pump, or tower-fan proof removes the
  corresponding physical capacity; a false status can no longer hide a
  running heat source/sink behind it.
- CHW common and branch flow ramp over three seconds and conserve flow across
  operating branches. The bypass valve diverts flow around the evaporator,
  reducing barrel flow and producing a low-flow trip at 25% of design flow.
- Cooling-tower leaving-water temperature uses outdoor wet bulb, fan/VFD
  effect, and condenser-water-pump proof. A commanded tower without water flow
  cannot cool the condenser loop.
- Condenser rejection includes evaporator load plus approximately 22% compressor
  work. High-head behavior is based on condenser return temperature, latches,
  and requires both a cooled condition and manager reset.
- Pump work, ambient gain, coil heat pickup, refrigeration removal, finite loop
  inventory, and CHWS/CHWR sensor response participate in one first-law balance.

### Hot water, boilers, and primary-secondary distribution

- Boiler start now includes a 30-second purge and 5-second ignition period.
  Proof loss, failed pump proof, or a stuck-false local start immediately
  inhibits firing and physical heat output.
- Each boiler primary circulator supplies a fixed 60 GPM primary circuit,
  decoupled from the variable secondary distribution flow. HW flow and DP ramp
  over three seconds and coast using actual residual flow.
- The hydraulic separator uses actual active boiler leaving-water temperatures.
  Excess primary flow recirculates into primary return; excess secondary flow
  blends secondary return into distribution supply. Common HWS cannot be
  synthesized above the hottest active boiler leaving temperature.
- Secondary HWR and primary-to-secondary exchange are constrained by actual
  coil demand, distribution loss, flow, and water inventory so the common
  `500 x GPM x Delta-T` balance closes rather than creating a hidden heat sink.
- AHU and VAV two-way valves use equal-percentage flow behavior. Coil air-side
  output and water-side flow/return temperature conserve energy with variable
  water Delta-T instead of assuming 20 F at every valve position.

### AHU, VAVs, zones, and building pressure

- Mixed air now conserves moist-air enthalpy and humidity ratio when outside
  and return air combine.
- Supply and return fans have independent acceleration and three-second proof
  timers. Supply-fan heat follows speed squared.
- AHU heating and preheat valves have independent actuator travel and
  equal-percentage hydraulic response. Cooling and heating effectiveness were
  recalibrated after the fan/valve corrections to preserve accepted SAT targets.
- VAV damper feedback is an eight-second physical actuator position. Parent
  duct resistance uses that effective position rather than the instantaneous
  command.
- EF-1 has six-second VFD response, fifteen-second damper response, exact proof
  timing, and physical exhaust CFM. Outside-air intake versus exhaust flow
  drives envelope pressure.
- Negative pressure adds sensible and moisture infiltration load to zones;
  positive pressure suppresses it. Zone load changes propagate through return
  air, mixed air, AHU coil load, and the source plant.

## Integrated physics acceptance

The complete 28-equipment/355-point graph was run for 900 simulated seconds in
cooling and then 1,200 simulated seconds in heating. The cooling phase produced
CHWS below CHWR with 300 GPM and a matching AHU coil load. The heating phase
proved the boiler primary circuit, low-flow secondary circuit, separator
recirculation, VAV reheat, and HWS above HWR. Every published point remained
finite and within its configured range. The complete automated suite passes
194 tests; the two warnings are known BACpypes asynchronous object-construction
warnings in API tests, not failed assertions.

## Remaining equipment-model backlog

### P0 - close before claiming a calibrated whole-building twin

1. **External thermostat bridge for VAV-1 and VAV-2.** Their internal shadow
   zones affect AHU return air, but the physical wall-controller ZS values do
   not yet seed/correct those shadow states. Add source/reliability telemetry
   and a defined fallback when the external value is unavailable.
2. **Named baselines and deterministic state restore.** A restart rebuilds
   physics but retains WebCTRL priority commands. Repeatable graded labs need
   explicit summer/winter/shoulder baselines and checkpoint restoration of
   water/air/zone inventories, actuator states, timers, and latches.
3. **Field calibration and acceptance tolerances.** Replace assumed loop
   volumes, UA/effectiveness, pump heat, fan curves, equipment capacities,
   leakage, and time constants with equipment schedules and trended bench data.

### P1 - next realism and observability additions

1. Wire validated `model_parameters` from configuration into chiller, boiler,
   manager, AHU, exhaust, and zone constructors, with units and acceptable
   ranges, rather than keeping several constants code-only.
2. Add chiller/boiler minimum run, minimum off, anti-recycle, lead/lag rotation,
   staging, low-load cycling, boiler turndown, and lockout sequences while
   preserving WebCTRL supervisory authority.
3. Replace the aggregate CHW pump model with pump/fitting/valve curves,
   variable-speed DP control, individual branch pressure drop, measured pump
   heat, and low-Delta-T staging behavior.
4. Model shared condenser-water headers and tower cells rather than independent
   per-chiller loops. Add cell staging, basin inventory, blowdown/makeup, and
   freeze protection.
5. Add chiller kW, COP, PLR/IPLV behavior and boiler efficiency, stack/flue
   temperature, condensing return-temperature behavior, and fuel input.
6. Add explicit supply, return, outside, relief, and exhaust airflow nodes.
   Return-fan operation currently has real proof timing but does not yet alter
   return mass flow, relief flow, or building pressure.
7. Publish AHU airflow, fan Hz/kW, effective damper positions, exhaust CFM,
   hydronic separator flow/temperature, equipment lockout, and energy-balance
   residual points to BACnet so students can prove causes from trends.
8. Extend coils with UA/face-velocity/bypass-factor curves, fouling, condensate,
   pan/drain behavior, and freeze/ice accumulation.
9. Add occupancy, people, lighting, plug, door, solar orientation/glazing,
   shading, wind, and calendar schedules. Calibrate the envelope leakage model
   with floor-by-floor stack and wind effects.

### P2 - advanced plant and distribution detail

1. Add expansion tanks, makeup water, air separation, water loss, glycol
   concentration, freeze point, and water-quality/chemistry effects.
2. Add a branch-level duct network with fittings, filters, leakage, measured
   flow error, fan coastdown, backdraft dampers, and pressure coupling.
3. Add component degradation and maintenance state: tube/coil fouling, scaled
   towers, plugged strainers, bearing degradation, valve leakage, and sensor
   calibration drift tied to energy and pressure consequences.

## Corrections made

### Scenario execution and safety

- Scenario events are schema-validated for known actions, nonnegative and
  ordered times, required targets/values, real fault types, required fault
  parameters, and transport-versus-point scope.
- Every shipped scenario is validated at startup against the configured point
  catalog, including writability and analog command limits. The AHU's three
  deliberate non-BACnet physical fault hooks are explicitly allowlisted rather
  than allowing arbitrary nonexistent aliases.
- Duplicate scenario IDs now stop startup instead of silently replacing the
  first lesson.
- The engine's bounded one-simulated-second integration makes scenario events
  fire at their actual t+5/t+60 boundaries at 60x; they no longer collapse
  into one accelerated wall tick.
- Starting a lesson captures the prior weather targets. STOP/RESET restores
  those targets, relinquishes tracked Priority 3 writes, and clears the
  lesson's faults.
- A completed timeline is now reported as `effects_active`. The UI keeps STOP
  enabled and warns that the final conditions remain applied. Starting another
  scenario also warns and performs the cleanup.
- Scenario metadata now includes difficulty, recommended speed, simulated
  duration, estimated accelerated duration, observation points,
  prerequisites, and tags. The scenario library displays the core timing and
  difficulty information.

### Command/proof timing

The former universal “15 real seconds from command to failure” coupled the
display alarm to deliberately accelerated model proof times. That would have
made physically realistic chiller or boiler start sequences look failed.

Binary command/proof diagnostics now use two clocks:

1. A location-specific **simulated-time start allowance** represents expected
   equipment sequencing and therefore scales correctly from 1x to 60x.
2. After that allowance expires, a **15-wall-second confirmation** prevents a
   transient UI/network observation from becoming an immediate failure.

Configured allowances are 120 simulated seconds for chillers, 90 for boilers,
15 for cooling towers and AHU-1, and 10 for pumps and EF-1. These are training
allowances, not assertions that every real machine must prove in that time.
They decouple future equipment timing calibration from the diagnostic.

### Existing scenario repairs

| Scenario | Audit finding | Correction |
|---|---|---|
| AHU freezestat trip | Cooling was requested without a CHW source; fan/cooling forces were released before the trip, so the claimed running baseline could disappear | Starts the complete chiller path, AHU, and a downstream VAV; allows 120 simulated seconds of baseline; trip occurs while the chain remains active |
| Freezestat bypass/coil burst | Uses a deliberate derived physical OA-damper fault hook that is not a BACnet point | Hook is now explicitly catalog-validated; lesson metadata identifies 60x use and observable safety points |
| High-static bypass/duct failure | Timeline completes immediately but destructive forces remain active | UI now labels active effects and keeps STOP available; metadata identifies the PID/safety evidence and 10x recommendation |
| Boiler-2 failed proof | Forcing only the proof output false allowed the hidden boiler model to fire and heat the loop, teaching a physically false failure | Failure now blocks the simulated start/ignition input while the BACnet command remains accepted; firing and heat output remain zero while pump flow/heat remain real |
| Chiller-1 sensor drift | No airside load existed, leaving too little independent process evidence to distinguish sensor error from capacity loss | Starts AHU cooling and VAV delivery; students compare unit CHWS with common headers, flow, HWR, SAT, and DAT |
| VAV-1 stuck reheat valve | No boiler or HW pumps were started; DAT could not physically respond even before the actuator fault | Starts boiler circulation, distribution, firing, AHU airflow, and VAV airflow; establishes 75% heat, then relinquishes the displayed command while the physical actuator stays captured |
| VAV-3 frozen zone sensor | AHU fan alone did not establish useful mechanical cooling | Starts the complete CHW/AHU/VAV chain so airflow and DAT continue changing while zone-temperature telemetry freezes |
| Simulator communication loss | Timing and cleanup were valid | Added speed, difficulty, prerequisites, and scope metadata |

### New training labs

- `hot_water_reheat_load_response`: stages two VAV reheat loads and removes
  them, exposing pump proof, DP/flow, HWS/HWR, loop BTUH, boiler firing lag,
  DAT, pump heat, and loop coast.
- `chilled_water_load_response`: starts the complete evaporator/condenser
  path, stages AHU cooling-valve load, and removes it, exposing CHWS/CHWR,
  flow, proof, SAT, DAT, zone response, pump heat, ambient gain, and finite
  loop inventory.

## Reset determinism

The controls intentionally have three different meanings:

| Control | What it clears | What it does **not** restore |
|---|---|---|
| STOP scenario | Current lesson faults, its Priority 3 writes, and its weather targets | Thermal inventory, zone/air/water temperatures, actuator travel already in progress, equipment latches |
| RESET scenario engine | STOP plus all manual faults/forces | Same physical history and latches |
| RESTART simulation | Rebuilds equipment state and clears simulator runtime/fault state while retaining the BACnet session | WebCTRL-owned priority commands are deliberately retained and may immediately drive the fresh model |

Therefore STOP is suitable for handoff and observation of natural recovery,
but RESTART is required before a graded repeat that needs the same physical
initial state. A truly identical lab also requires the same retained WebCTRL
priority-array commands. The UI now states this boundary instead of calling a
fault cleanup a thermodynamic reset.

## Architecture and telemetry assessment

The system API already exposes the plant and child operating snapshots needed
to explain coupled physics: common CHWS/CHWR/flow/load, HWS/HWR/flow/DP/load,
boiler/pump heat, AHU coil loads and flows, SAT/RA/MA conditions, and VAV
airflow/DAT/reheat load. Scenario `observation_points` currently names BACnet
points; richer derived values remain available through the command-center
`systems` and equipment snapshots.

The remaining observability gap is semantic rather than numeric: the API does
not yet publish one causal event/training trace connecting “child valve moved”
to “plant load changed” to “source staged” to “space temperature responded.”
Students and instructors must correlate separate trends manually. That is a
useful advanced exercise, but it limits introductory and auto-graded labs.

## Prioritized training and design backlog

### P0 — needed before repeatable graded labs

1. **Baseline profiles and preflight.** Define named summer/winter/shoulder
   baselines with required WebCTRL relinquishes, plant command states, weather,
   zone loads, and a settle-to-tolerance gate. A lesson should refuse to start
   or clearly warn when preconditions are not met.
2. **Deterministic checkpoint/restore.** Snapshot and restore all model state,
   loop energy inventories, actuator states, timers/latches, faults, weather,
   scenario clock, and instructor-owned priorities. Do not silently overwrite
   WebCTRL-owned priorities; show and require an explicit decision.
3. **Machine-evaluated outcomes.** Add time-window assertions such as
   `CHWR-CHWS > 5 F for 120 s`, `boiler output remains 0`, and `SAT responds
   within a band`. Preserve instructor notes, but distinguish evidence from
   opinion and record pass/fail/timeout.
4. **Scenario precondition/interlock report.** Before RUN, show missing parent
   dependencies, conflicting active faults/overrides, expected wall duration at
   current speed, and whether 15-wall-second diagnostics can be observed during
   the selected acceleration.

### P1 — highest-value use cases

The first P1 scenario pack now covers excessive CHW bypass, CHW pump-proof
loss, favorable-to-warm-humid economizer transition, a stuck economizer
damper, negative-building-pressure recovery, and BACnet Priority-3 conflict.
The remaining items below require additional scenario variants or explicit
model hooks.

1. Chiller failed-to-prove caused separately by CHW pump, isolation valve,
   condenser-water pump, tower fan/high head, and plant shutdown interlocks.
2. CHW low-delta-T syndrome, excessive bypass, sensor bias, loss of flow, and
   staging inefficiency under changing AHU load.
3. Boiler short cycling, lead/lag rotation, low-flow limit, high-limit lockout,
   reset schedule, failed pump proof, and stuck-open reheat causing hot return
   or after-hours energy waste.
4. Economizer lessons for favorable enthalpy, the OA >60 F and RH >35% lockout,
   mixed-air low limit, integrated cooling permission, bad OA/RA humidity, and
   simultaneous economizer/mechanical-cooling diagnosis.
5. AHU/VAV diversity: multiple zone load steps, critical-zone static reset,
   duct leakage, filter loading, fan-law energy, ventilation minimum, humidity,
   and simultaneous heating/cooling.
6. Zone envelope/occupancy labs: solar and orientation load, people/equipment
   schedules, door/window infiltration, warm-up, cooldown, setback, and
   recovery. Occupancy is not currently modeled.
7. Condenser/tower labs: wet-bulb approach, tower fan/VFD staging, no-flow fan,
   loss of fan under load, basin thermal mass, and condenser high-pressure trip.
8. Building pressure labs coupling AHU outdoor/relief air, return fan, exhaust
   fan, infiltration, door state, and smoke-control modes.

### P2 — teaching platform capabilities

1. Role-based instructor/student views with fault reveal, hints, attempt timer,
   evidence submission, and debrief replay.
2. A causal timeline/trend bundle captured per run, including commands,
   effective actuator positions, proofs, parent availability, energy rates,
   temperatures, alarms, and student actions.
3. Difficulty variants and seeded uncertainty: the same symptom with sensor,
   actuator, hydraulic, sequencing, or communications causes.
4. Scenario composition with reusable setup/cleanup blocks instead of copying
   six chiller prerequisite writes into every cooling lesson.
5. Curriculum mapping for controls fundamentals, hydronics, psychrometrics,
   commissioning, FDD, BACnet priorities/COV, and incident triage.
6. Calibration packs tied to actual equipment schedules/trends, with provenance,
   uncertainty bands, and versioned acceptance tolerances.

## Verification

- All ten scenario JSON files parse and validate against the 28-group,
  355-point configured catalog.
- Dedicated tests cover catalog references, invalid timelines/actions, weather
  target restoration, completed-but-active status, and the dual-clock
  command/proof diagnostic.
- Focused airside verification passes 66 tests; focused hydronic/coupling
  verification passes 52 tests; focused scenario/diagnostic verification
  passes 33 tests.
- The complete suite passes 194 tests. Two BACpypes asynchronous post-init
  warnings remain in API tests; there are no failed assertions.
- In the integrated cooling phase, the graph produced 45.64 F CHWS, 46.53 F
  CHWR, 300 GPM, 120,989 BTUH of AHU load, 60.73 F SAT, and 73.17 F RAT.
- In the integrated heating phase, the graph produced 184.72 F HWS, 178.66 F
  HWR, 3.47 GPM, 4,147 BTUH of coil load, 6,379 BTUH of modeled distribution
  loss, and 10,526 BTUH of separator exchange. Rounded common-header
  `500 x GPM x Delta-T` was 10,514 BTUH; the small displayed difference is
  caused by calculating from rounded temperatures/flow.
- The same run produced 88.11 F VAV discharge air, a 72.11 F zone, 1,400 CFM
  exhaust flow, and -0.0001 in. w.c. building pressure. All 355 published point
  values were finite and inside configured ranges.
