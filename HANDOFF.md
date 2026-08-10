# ACI BACnet Building Simulation Platform — Handoff Document (v5)

Prepared for Jeff Jenkins, Automated Controls Inc. Written to be
self-contained for a fresh agent session with no prior context — if
you're picking this up cold, this document plus the codebase itself
should be enough to continue safely.

## 0. Current AHU command-center and safety release — 28/329

The working checkout and `ACIBACnetSimulator` Windows service are verified at
**28 equipment groups / 329 BACnet objects**. The 2026-07-24 administrator
restart completed, WebCTRL writes resumed from allowlisted peers, confirmed
COV subscriptions recovered during cutover, both catastrophic training
scenarios passed, and the guarded Restart returned the simulator to 1x with
zero faults, forces, scenarios, or blocked BACnet requests.
Acceptance evidence:
`artifacts/live-ahu-command-center-acceptance-20260724.md`.

Every identifier in the 321-point catalog is preserved. The exact eight
additions are read-only AHU-1 objects:

| Local/global object | Alias | Direction | Units |
|---|---|---|---|
| `AI:5` / `AI:9005` | `ahu_ma_humidity` | Simulator to WebCTRL | percent RH |
| `AI:6` / `AI:9006` | `ahu_sa_humidity` | Simulator to WebCTRL | percent RH |
| `AI:7` / `AI:9007` | `cooling_coil_entering_air_temp` | Simulator to WebCTRL | degrees F |
| `BI:44` / `BI:9044` | `automatic_high_static_trip` | Simulator to WebCTRL | no units |
| `BI:45` / `BI:9045` | `duct_structural_failure` | Simulator to WebCTRL | no units |
| `BI:46` / `BI:9046` | `automatic_freezestat_trip` | Simulator to WebCTRL | no units |
| `BI:47` / `BI:9047` | `cooling_coil_freeze_condition` | Simulator to WebCTRL | no units |
| `BI:48` / `BI:9048` | `cooling_coil_rupture_flood` | Simulator to WebCTRL | no units |

`AV:9003 duct_static_pressure` keeps its identifier and direction, but its
published range changes from 0.00-5.00 to **0.00-10.00 in. H2O** so the
deliberately bypassed overpressure lesson can be represented. Existing
`BV:9100 high_static_pressure_trip` and `BV:9101 freezestat_trip` remain
writable external/manual hard-interlock inputs; the simulator must not
overwrite them.

### Safety state machines

- **Healthy high-static safety:** at 4.0 in. H2O the automatic switch latches,
  publishes BI:9044, and stops both fans before the structural limit.
- **Bypassed high-static safety:** only the restricted instructor
  safety-bypass/failure mechanic permits continued operation. Above the
  representative 5.0-in. H2O training duct-class limit, BI:9045 latches,
  the duct uses the damaged/exploded animation, and the AHU outline flashes
  red. Five inches is a configurable training limit, not a universal real
  duct rupture rating.
- **Healthy freezestat:** low cooling-coil entering air latches BI:9046 and
  forces the protective fan/OA/cooling/heating response.
- **Bypassed freezestat:** below 32 degrees F accumulates 20 simulated minutes
  without useful chilled-water flow or 60 simulated minutes with cooling
  valve open and chilled-water flow proven. The catastrophe lesson also
  fails the physical OA damper open so the working mixed-air low limit cannot
  remove the hazard. BI:9047 and BI:9048 then latch, with ice and
  burst/flood graphics.
- Safety exposure timers use simulated seconds. The 15-second command/status
  diagnostic remains a wall-clock observation timer.
- Clearing a bypass fault does not erase a latched consequence. The guarded
  Restart control is the manual-reset boundary for automatic trips,
  structural failure, freeze, burst/flood, PID memory, scenarios, faults,
  priority-3 forces, speed, and elapsed simulation time.

### Economizer availability state machine

`AO:9023` remains the raw WebCTRL request; no existing point is renumbered and
no economizer BACnet object is added. The simulator publishes computed
requested-versus-effective position and suitability diagnostics through the
AHU command-center API.

- Preferred method: dual enthalpy, enabling at OA minus RA enthalpy
  `<= -1 Btu/lb` and disabling at `>= +1 Btu/lb`.
- Additional high limits: 75 degrees F OA dry bulb and 55/57 degrees F OA
  dew-point enable/disable.
- Reliability fallback: dual enthalpy -> single enthalpy (28 +/- 1 Btu/lb)
  -> differential dry bulb (65/67 degrees F) -> fixed dry bulb. Unreliable
  OAT disables free cooling.
- Unsuitable weather holds the effective stroke at 0%, which is the
  operating 15% minimum-ventilation position. Fan-off, hard-safety, and
  mixed-air-below-45-degrees-F states close outdoor air fully; the mixed-air
  limit releases at 47 degrees F.
- After 180 simulated seconds at >=95% effective stroke with SAT still above
  setpoint, integrated mechanical cooling is allowed.
- The UI exposes method, suitability, OA/RA enthalpy, delta, OA dew point,
  limiting reason, full-open proof, integrated state, and FDD flags.

### Detailed AHU graphic order

Outside air enters from the left through the economizer and prefilter.
Return-air temperature/humidity and smoke detection are before the return
fan in the top return duct. The streams meet at the mixing plenum with MA
temperature/humidity, followed by preheat, a downstream serpentine
freezestat element, cooling coil, reheat coil, supply fan, SA
temperature/humidity and smoke detection, the high-static switch, a jagged
duct break, and the two-thirds duct-static sensor. Dampers, valves, and fans
animate from their live commands/proofs.

### WebCTRL and release work

Existing mappings remain valid. Discover/map only the eight new read-only
objects and refresh cached `AV:9003` metadata if WebCTRL still shows a
5.00-in. H2O maximum. The live 329-point checkout passes **155/155**
automated tests. The source workbook and generated Obsidian notes are
authoritative for the point contract; WebCTRL still needs only the eight new
read-only objects discovered/mapped.

Authoritative design and cutover details:
`docs/DUCT_STATIC_PID_LAB.md` and `docs/REALISM_CUTOVER_CHECKLIST.md`.

---

The sections below preserve earlier 321-, 318-, 233-, 220-, and 219-point
milestones as historical evidence. Their point and test counts are not the
current 329-point release boundary.

## 0. Live AHU duct-static PID lab and cold restart — 2026-07-24

The working checkout and `ACIBACnetSimulator` Windows service now both run
**28 groups / 321 BACnet objects** and the suite passes **129/129 automated
tests**. The operator approved the administrator action and the controlled
service cutover completed successfully.

The only catalog additions are AHU-1:

| Local/global object | Alias | Direction | Units |
|---|---|---|---|
| `AV:2` / `AV:9002` | `duct_static_pressure_setpoint` | WebCTRL to simulator | in. H2O |
| `AV:3` / `AV:9003` | `duct_static_pressure` | Simulator to WebCTRL | in. H2O |
| `AV:4` / `AV:9004` | `sa_fan_speed_feedback` | Simulator to WebCTRL | percent |

The fingerprint regression proves all 318 prior identifiers are unchanged.
The duct-static setpoint is commandable from 0.25–2.00 in. H2O with a
1.00-in. H2O relinquish default. Actual pressure and VFD feedback are
read-only.

The AHU model now:

- locates the training sensor two-thirds down the straight common supply
  trunk immediately before the first VAV takeoff;
- requires supply-fan command and proof before enabling the pressure loop;
- uses the design-CFM-weighted feedback of all 17 VAV dampers;
- applies fan-law pressure response, dynamic fan/duct/sensor time constants,
  deterministic sensor ripple, and exact zero values while off;
- implements adjustable direct-acting P/I/D control with derivative on
  measurement, filtering, anti-windup, output slew, and a 25–100% proven-fan
  range; and
- publishes a 900-sample server history for the training trend.

At fixed fan speed, opening VAV dampers lowers pressure and closing dampers
raises it. The PID increases fan speed as opening terminals pull pressure
below setpoint. This correct physical sequence is stated directly in the UI
and test coverage.

The command center has a separate **Duct Static PID** page with command/proof,
actual/setpoint/error, VFD output, design-CFM-weighted VAV demand, editable
P/I/D/interval controls, an animated AHU/common-duct/sensor graphic, a
floating pressure bubble, and an actual-versus-setpoint trend. The command
bar also has a guarded **Restart** button.

Restart is an in-process state reset: it stops the engine, resets scenarios,
drains instructor priority writes, clears faults and every command priority,
clears reliability state, recreates every equipment/PID/safety model,
restores 1x/zero elapsed time, announces I-Am, and starts the engine. The
BACnet object graph stays online so existing WebCTRL COV subscriptions remain
attached instead of disappearing until their old lifetime expires.

Live acceptance completed:

1. Fresh project and full Obsidian-vault backups were created before
   cutover/merge.
2. The reviewed administrator restart returned a fresh 28-group / 321-point
   `/api/status`.
3. WebCTRL writes resumed from the allowlisted bench peers with zero blocked
   BACnet requests.
4. AHU-1 proved, its PID became active, and pressure tracked the 1.000
   in. H2O setpoint with live VFD response.
5. The dashboard Restart button completed a second cold rebind, restored
   default tuning and 1x speed, cleared faults/forces/scenarios, and WebCTRL
   commands plus confirmed COV subscriptions recovered.
6. The point workbook and generated Obsidian design pack were rebuilt for
   321 points and visually/structurally verified.

The remaining WebCTRL-side task is to discover/map AV:9002, AV:9003, and
AV:9004, then perform the instructor-selected damper-demand tuning exercise.
Authoritative design and acceptance details:
`docs/DUCT_STATIC_PID_LAB.md`.

---

## 0. Current checkout and verified bench state — 2026-07-23 (read this first)

The working checkout on the Test Bench laptop is authoritative. The
`ACIBACnetSimulator` NSSM service is installed, the verified BACnet
topology binds the simulator to `192.168.168.201:47808`, and the local
dashboard is at `http://127.0.0.1:8001`. Live traffic from the last
completed service cutover and the current configuration verify these
approved BACnet sources:

`192.168.168.1` through `192.168.168.7`, plus `192.168.168.200`.

Both `peer_allowlist` and `write_source_allowlist` intentionally contain
that exact list. Older text below that says only `.200` was an earlier
single-peer assumption and must not be used to narrow the working build.
The public repository is `github.com/JeffJenkinsBAS/bacnet_simulator`.
The expanded automated suite currently passes **129/129 tests**.

The live service is the **28-group / 318-point command-center build**.
The approved controlled cutover on 2026-07-23 used the verified
`192.168.168.201:47808` bind and returned with 28 equipment models, a
running 1 Hz engine, zero faults, zero priority overrides, and zero blocked
requests. The dedicated acceptance validated 85/85 new point addresses and
68/68 design-airflow values, exercised VAV-3 at a 75% damper command with
75% feedback, and restored zero forces at 1x speed. Live WebCTRL messages
and writes are active. COV recovery reached 50 active subscriptions: 29
confirmed and 21 unconfirmed across `.2`, `.5`, `.6`, and `.7`. The live
diagnostic catalog contains 34 mapped locations.

Live acceptance evidence:

- `artifacts/live-realism-acceptance-20260723-170906`
- `artifacts/live-ahu-sat-acceptance-20260723-173703`
- `artifacts/live-vav-point-exposure-acceptance-20260723-195858`

Reviewed restart log:
`artifacts/vav-point-exposure-restart-20260723.log`.

A final VAV correction is also live: 0% damper position publishes only
1.0 CFM of modeled leakage when AHU proof is present, and an
unproven/stopped AHU forces exactly 0.00 CFM. The focused acceptance passed
after the reviewed administrator restart:
`artifacts/live-vav-airflow-acceptance-20260723-174258`.

The earlier **16-group / 143-point baseline** remains useful historical
evidence: that build restored 26 active COV subscriptions after restart.
The expanded process also reached **26 active subscriptions** during its
initial recovery and later reached the final 50-subscription snapshot above.
Treat each count as point-in-time client state rather than a fixed catalog
requirement; confirmed/unconfirmed traffic and change delivery remain the
authoritative acceptance signals.

Ollama `0.32.1` is installed and running on `127.0.0.1:11434`; the
simulator is configured for `hermes3:3b`. Hermes Agent is also installed,
but its active inference provider is cloud-hosted Anthropic. See the
Obsidian Test Bench vault for the dated hardware and agent-feasibility
assessment.

---

## 0.0 Live VAV design-limit and damper-feedback exposure

The working checkout and Windows service are verified live at **28 equipment
groups / 318 BACnet objects**. The controlled restart and dedicated
point-exposure acceptance completed successfully.

The accepted delta is exactly five read-only analog values on every VAV:

| Local object | Alias | Units | Meaning |
|---|---|---|---|
| `AV:81` | `heating_min_airflow` | CFM | Occupied heating minimum |
| `AV:82` | `heating_max_airflow` | CFM | Heating-mode maximum |
| `AV:83` | `cooling_min_airflow` | CFM | Occupied cooling minimum |
| `AV:84` | `cooling_max_airflow` | CFM | Design cooling maximum |
| `AV:85` | `damper_position_feedback` | percent | Effective simulated damper position after actuator faults |

All 85 objects are `sim_to_webctrl`, non-writable, and non-commandable.
AO:20 remains the independent WebCTRL damper-position command. A regression
fingerprint proves all 233 previously deployed object identifiers are unchanged;
only the new objects require WebCTRL discovery and mapping. The next
available VAV analog-value number is `AV:86`.

The live acceptance script validated all 85/85 addresses, all 68/68
design-airflow values, a safe VAV-3 75% command/feedback transition, and
zero-force/1x cleanup. Evidence:
`artifacts/live-vav-point-exposure-acceptance-20260723-195858`.

---

## 0.1 Live VAV diversity, zone heat balance, and humidity

The Windows service currently exposes **28 equipment groups / 318 BACnet
objects**. `/api/status`, live WebCTRL messages/writes, priority rollback,
and 50 active COV subscriptions were verified after the cutover: 29 confirmed
and 21 unconfirmed across `.2`, `.5`, `.6`, and `.7`.

- Every VAV has a representative space type, unique zone area, cooling
  maximum, occupied minimum, and heating maximum. Virtual-zone areas span
  600–2,400 square feet and design maximums span 400–2,120 CFM.
- VAV-3 through VAV-17 begin at varied 69.9–74.3 F zone temperatures and use
  different thermal mass, envelope UA, solar exposure, internal load,
  occupancy density, infiltration, and adjacent-zone mixing.
- The old first-order drift-to-target shortcut was replaced by an analytical
  heat balance using `1.08 x actual CFM x (DAT - zone temperature)`. With no
  AHU proof, supply CFM is zero and only real space loads move temperature.
- A 0% VAV damper overrides occupied-minimum flow: it leaves only 1.0 CFM
  modeled leakage with AHU proof, and AHU-off flow is exactly 0.00 CFM.
- VAV-3 through VAV-15 add exactly one Zone Humidity AI each: local `AI:3`,
  published as `AI:13003` through `AI:25003`. No existing BACnet point number
  changed; a regression fingerprint covers all 220 pre-existing identifiers.
- Humidity is integrated as humidity ratio with buffered zone moisture
  capacitance. VAV reheat changes dry bulb/RH but does not remove moisture;
  AHU cooling can remove moisture at the wet coil.
- The command center exposes zone area, design/minimum/heating CFM, zone
  humidity, and corrected ASCII-safe degree-Fahrenheit text.
- The live command-center inspector also separates the WebCTRL AO:20 damper
  command from the effective AV:85 damper feedback and displays the four
  mode-specific CFM limits.
- Commandable analog objects now publish and enforce their configured
  BACnet minimum/maximum values. Instructor/API and BACnet network writes
  outside those limits are rejected rather than displayed while the
  physical model silently clamps.
- Stop/reset tracks every instructor priority-3 command, relinquishes the
  actual priority slot, and exposes those overrides through `/api/points`
  so acceptance cannot falsely report zero forces.
- Virtual zones retain a hidden physical temperature state. Sensor
  offset/freeze/drift faults change the published reading without feeding
  the bad value back into the heat balance.
- The complete automated suite passes **129/129 tests**. The live 28/318
  point-exposure, closed-damper/AHU-off, WebCTRL write/message,
  priority-release, AHU SAT, UI, and both COV-mode acceptance checks are
  complete.

---

## 0.2 Historical command-center expansion — 28/220 on 2026-07-23

At this earlier milestone, the checkout extended the validated baseline into
a larger, interactive training digital twin:

- **28 equipment groups / 220 BACnet points** under the same single
  supervisory device, instance `242000`.
- **VAV-1 through VAV-17.** VAV-1 and VAV-2 keep their external
  zone-temperature controller connections to the physical test-bench
  wall; VAV-3 through VAV-17 are virtual zones with simulated zone
  temperatures.
- A Site **Building Pressure AI** and Exhaust Fan **VFD AO** provide the
  process points for positive-building-pressure training. AHU supply
  raises pressure and enabled exhaust lowers it according to the VFD
  command; the BAS/EIKON program owns the final setpoint and sequence.
- A diagnostics service evaluates command/status relationships and VAV
  airflow tracking. A condition must persist for **15 real seconds**
  before it becomes a failure; VAV airflow is considered tracking inside
  an inclusive **±25%** band around its airflow setpoint.
- The dashboard now includes an interactive command-center view using the
  generated `static/assets/building-digital-twin.png` asset, live system
  hotspots/status, all 17 VAVs, responsive behavior for the 1024×768
  bench display, and self-hosted Font Awesome/Rajdhani assets.
- `ACI_BACnet_Simulator_Point_Mapping.xlsx` was regenerated for the
  220-point catalog. `README.md`, `docs/COMMAND_CENTER.md`,
  `THIRD_PARTY_NOTICES.md`, and the generation/operational documentation
  were updated with the expanded topology and asset licensing.

This is a **typical training layout**, not an as-built or construction
design. It is intended to make plant, AHU, exhaust, pressure, and
multi-zone relationships visible for training.

**Historical verification boundary:** the configuration, registry, layout contract,
diagnostics, priority release behavior, and live API are covered by the
green **92-test suite**. The expanded 28/220 process was running as the
Windows service with live WebCTRL reads, writes, and COV traffic. Mapping
the additional VAV-6 through VAV-17 objects into WebCTRL graphics/programs
remains future BAS-side work.

---

## 0.3 Historical HVAC realism acceptance — 28/220 on 2026-07-23

The second realism pass is **loaded into the Windows service and live-bench
verified**. A recoverable pre-cutover project/vault backup was taken, the
reviewed restart script was run once with administrator approval, and the
service returned cleanly on the unchanged verified BACnet topology.

- Chilled-water and hot-water plant managers expose proven capacity and
  common-header conditions to the AHU and VAV models.
- AHU cooling requires usable chilled water; VAV reheat requires proven
  hot-water distribution; VAV airflow requires AHU proof and duct static.
- VAV discharge temperature and virtual-zone temperature now respond to
  actual airflow, plant availability, discharge temperature, outside load,
  and internal load.
- The command center renders subtle looping image plumes in each VAV space:
  blue cooling, red heating, white/gray ventilation, or off.
- VAV airflow diagnostics report `inhibited` during an upstream AHU outage
  instead of declaring all seventeen terminals failed.
- CHWS and HWS reset metadata/ranges were corrected to degrees Fahrenheit;
  the point-mapping workbook was regenerated from the current configs.
- The full behavior and reference basis are in
  `docs/HVAC_REALISM_MODEL.md`.

Live acceptance verified 28 groups / 220 points, 34 digital-twin locations,
17 air-delivery snapshots, AHU-off VAV inhibition, the 15-real-second
command/proof failure timer, neutral ventilation, mechanical cooling, and
hot-water reheat. The measured VAV-3 checkpoints were 1,000 CFM / 57.0 F
for cooling, 300 CFM / 95.0 F for heating, and 800 CFM / 75.2 F for neutral
ventilation. All temporary instructor overrides were released, simulation
speed returned to 1x, and the fault list returned to empty.

WebCTRL traffic resumed immediately after restart. The live process recorded
writes from `192.168.168.2`, COV subscriptions rebuilding from 13 during the
acceptance sequence to 26 before final handoff across confirmed and
unconfirmed modes, zero blocked requests, and no application errors. Browser
acceptance verified the blue, red, and
white/gray space-air states, live inspector drill-down, 34 markers, all 17
VAV space layers, and no horizontal overflow at 1280x720 or 1024x768.
Evidence is under `artifacts/live-realism-acceptance-20260723-134142` and
`artifacts/design-qa`.

---

## 0.4 Historical AHU setpoint acceptance — 28/220 on 2026-07-23

The live Windows service now exposes exactly one writable AHU-1 supply-air
temperature setpoint at `analog-value:9001`. It is a 45-95 F WebCTRL-to-
simulator command with a 55 F relinquish default and is used for both cooling
and heating modes. WebCTRL remains responsible for the cooling- and
heating-valve commands; the simulator models the physical result.

- Proven chilled water, cooling-valve travel, minimum outdoor air, mixed-air
  temperature, coil approach, and fan heat determine cooling SAT.
- Proven hot water, a characterized-valve response, minimum outdoor air, and
  a 20.5 F design coil rise determine heating SAT.
- At 70 F OA and 15% minimum OA, a 50% heating-valve command settled at
  85.06 F against an 85 F setpoint.
- At 40 F OA, the same 50% command settled at 80.14 F; increasing the valve
  to 72% restored SAT to 85.07 F.
- At 80 F OA with 44 F chilled water, a 100% cooling-valve command settled
  at 56.0 F against the same point reset to 55 F.
- A cross-ramp gets one 60-second simulated actuator-travel window. If both
  cooling and heating commands remain above 10%, AHU-1 enters the 15-real-
  second command-center timer and then outlines red with both valve commands
  and a WebCTRL priority-lock warning.

The final acceptance run passed every check, released all instructor
overrides, restored 1x speed, and left no injected faults or active scenario.
Evidence is in
`artifacts/live-ahu-sat-acceptance-20260723-150930`. The service reports
28 groups / 220 points, WebCTRL traffic resumed after restart, 50 active COV
subscriptions were present at final verification, and the blocked-request
counter remained zero. The point-mapping workbook and Obsidian generator now
use the 220-point catalog. The automated suite passes 92/92 tests.

---

## 0.5 Historical update — 2026-07-17 Session

A full working session on Jeff's machine (`JEFF-JENKINS`, which hosts
WebCTRL 8.0–10.0 installs and is the likely bench laptop) moved the
  project substantially. The repo lives at
  `github.com/JeffJenkinsBAS/bacnet_simulator`; the phase zips in §8 are
  historical. The test suite was **55/55 at that time**.
Highlights, roughly in order:

- **Windows batch scripts were broken and are now fixed.** All seven
  `scripts\windows\*.bat` resolved paths one directory too shallow
  (`%~dp0\..` from `scripts\windows\` lands in `scripts\`), so
  `install.bat`/`run.bat`/service install could never have worked. Fixed,
  plus a new `run_headless.bat` because Task Scheduler provides no working
  directory. The fixed installer was executed end-to-end on this machine.
- **A full audit of every equipment model and the BACnet layer** was
  performed against the `webctrl-skill` domain references —
  `SIMULATION_AUDIT.md` is the report. **Every HIGH/MEDIUM finding is
  fixed**: manager groups are now serviced by aggregator models
  (`equipment/managers.py` — chillerN_ok/boilerN_ok mirrors, live CHW
  common header, plant remote_shutdown, Boiler Manager enables), VAV
  reheat discharge is clamped at hot-water temp, cooling-tower physics
  now track wet-bulb + approach and climb toward a high-head trip when
  the fan stops, chillers/boilers have flow-proving interlocks, the
  freezestat closes the OA damper, and `reliability_fail` faults set the
  real BACnet Reliability property.
- **CRITICAL Windows bug found and fixed: the deaf BACnet device.** The
  startup duplicate-instance check broadcast a Who-Is to 127.0.0.255; on
  Windows that send kills the asyncio UDP transport — socket stays bound,
  but the app never receives another BACnet packet. Every instance ever
  launched on this machine had been deaf (`messages_in` permanently 0).
  The check now skips on loopback binds. Two more live-caught bugs: the
  engine-start API endpoint corrupted state when called (sync-def
  threadpool, no event loop — mutating endpoints are now async), and
  priority-array forces on binary points always raised TypeError (now
  written as typed `BinaryPV`).
- **COV is RESOLVED** (was §5's open item): confirmed AND unconfirmed
  notification delivery verified live, cross-process, on the real port.
  All three WebCTRL refresh strategies work on every point — polling
  (refresh < 31 s), UnconfirmedCOV (>= 31 s), ConfirmedCOV (>= 1 min
  ending :01). `/api/cov/subscriptions` + a dashboard panel show live
  subscriptions per mode for training. Transport faults and the traffic
  counter now also intercept ReadPropertyMultiple / WritePropertyMultiple
  / SubscribeCOV — what WebCTRL actually sends.
- **Network isolation and verified bench topology (historical `.200` view;
  superseded by §0).** The bench is an isolated `192.168.168.0/24` segment:
  - **Simulator** (this app): `192.168.168.201`, listening on **UDP
    47808**.
  - **WebCTRL**: `192.168.168.200`, BACnet connection on **UDP 47809**,
    targeting the simulator at `192.168.168.201:47808`.
  - **Device instance**: `242000`.

  A `peer_allowlist` in `config/network.json` silently drops (no reply)
  every BACnet request from a source IP not in it, counting it in a
  `messages_blocked` counter on the dashboard; `write_source_allowlist`
  does the same for writes. The current verified list is in §0 and
  `config/network.json`.

  > **Supersedes the 2026-07-17 "bench standard is UDP 47809 / never move
  > back to 47808" note below.** That earlier decision assumed a
  > co-resident WebCTRL sharing the simulator's NIC and an office WebCTRL
  > squatting on 47808. The verified bench instead separates the two hosts
  > (`.200` WebCTRL / `.201` simulator) on an isolated subnet: the
  > simulator now binds **47808** and WebCTRL uses **47809**. Any remaining
  > references to a simulator on 47809, a `192.168.168.100` bench IP, or a
  > `192.168.68.0/24` subnet are stale.
- **The dashboard was rebuilt twice** (war-room pass, then Apple
  liquid-glass design per Jeff's preference): glass rail/command bar,
  capsule controls, engine power toggle, ×1–×60 time-rate control, live
  point search with value-change flashes, toasts, confirm modals, COV
  panel, peer-allowlist/blocked-requests readouts, and the ACI round logo
  (static/logo.png) as the top-left brand mark. Still one self-contained
  HTML file, offline-safe.
- **IT/network coordination**: the simulator binds the bench NIC directly
  at the verified static IP `192.168.168.201/24` (machine `JEFF-JENKINS`;
  wired Killer E3100G MAC `D8-BB-C1-F7-89-38`, USB Realtek
  `E0-EF-25-01-BD-C1`). The bench WebCTRL host is `192.168.168.200` on the
  same isolated `/24`. (An earlier manual `192.168.168.100` on the wired
  port is superseded by `192.168.168.201`.)

---

## 1. What This Is

A locally-running BACnet/IP simulation application that stands in for real
HVAC field equipment on the WebCTRL training test bench. It exposes 318
BACnet objects under one simulated supervisory device, publishing
realistic sensor/status values and accepting commands from Jeff's existing
WebCTRL/EIKON programs (AHU, Chiller Manager, Boiler Manager, VAV-1,
VAV-2, Simulation Manager) exactly as real field hardware would. The
larger virtual fleet extends through VAV-17 for repeatable multi-zone
training. Built for technician training, controls programming practice,
and commissioning/troubleshooting exercises.

---

## 2. Status Right Now

**Phases 1–6a complete, plus the 2026-07-17 hardening pass and 2026-07-23
command-center/realism expansion (§0–0.2)**:
architecture, equipment models (AHU, 3 chillers, 3 boilers, exhaust fan,
17 VAV zones, site conditions, and 2 plant-manager aggregators), fault
injection (11 mechanics), scenario engine (6 shipped scenarios), the
interactive command center, LLM orchestration (6a), and Windows
packaging/service scripts (see §0). **129 automated tests passing.** Core
BACnet behavior, including both COV modes, was verified live on earlier
accepted baselines. The current 28/318 service has verified WebCTRL
messages/writes, zero blocked requests, and 50 active COV subscriptions:
29 confirmed and 21 unconfirmed across `.2`, `.5`, `.6`, and `.7`.

**Physical deployment and expanded command-center cutover are operational
as of 2026-07-23:**
- ✅ Project on the laptop; the public GitHub repository is
  `JeffJenkinsBAS/bacnet_simulator`.
- ✅ Found and disabled a conflicting service (a previously-purchased SCADA
  Systems BACnet Simulator was squatting on UDP 47808).
- ✅ venv installed via the FIXED `install.bat`; suite green on this machine.
- ✅ **Verified live topology (see §0)**: simulator `192.168.168.201`
  on **UDP 47808**, device instance `242000`, and both allowlists contain
  `.1` through `.7` plus `.200`.
- ✅ NSSM service installed and running the expanded 28-group / 318-point
  checkout; WebCTRL messages/writes and both COV modes are present with zero
  blocked requests.
- ✅ Building Pressure AI / Exhaust Fan VFD AO behavior, the 15-second
  VAV failure transition, and BACnet priority release were exercised
  during the live cutover.
- ✅ Parent-equipment realism, VAV upstream inhibition, neutral/cooling/heating
  delivery, and the corresponding live space-air visualization were exercised
  during the controlled service cutover.
- ⬜ Reboot/startup and restore checks should still be repeated after
  planned upgrades, using the operational runbook rather than changing
  the live network configuration.

**Phase 6 direction has been reviewed and phased** (see §7) — a large
LLM/Ollama orchestration + dashboard upgrade spec was submitted and
critiqued before any implementation started, per that spec's own
instruction to plan before making major changes. Full review in
`PHASE6_REVIEW.md`. **Phase 6a is now built and verified live** — see §7.5.

---

## 3. Architecture Summary

- **One BACnet device** (`ACI-SIM-SUPERVISOR`) hosts everything — not one
  device per equipment group. This was a direct field correction from Jeff
  after an earlier draft used 16 separate devices/ports.
- **Verified device instance: 242000.** Confirmed for the bench, matching
  the `2420xx` block Jeff designated.
- **28 equipment groups, 318 BACnet objects.** Each group has an
  `instance_offset` (position × 1000 — e.g. AHU-1 is offset 9000); a
  point's real global object instance is `offset + local_instance`. Full
  table in `ACI_BACnet_Simulator_Point_Mapping.xlsx`.
- **Five-layer design:** BACnet Transport (`transport.py`, one bacpypes3
  Application) → Simulation Engine (`engine.py`, 1Hz tick loop) →
  Equipment Models (`equipment/*.py`) → Point Registry
  (`registry.py`: `PointRegistry` + `GroupView`) → FastAPI/REST (`api.py`)
  + dashboard (`static/command-center.html`, `styles.css`, and `app.js`).
- **`GroupView` is the critical extension seam** — every equipment model
  reads/writes through it, and it's what let the single-device merge and
  fault-injection system get added without touching any equipment model
  file. **Any Phase 6 work should extend through this same seam wherever
  possible**, not add parallel access paths to the registry.
- **Fault injection**: 11 generic mechanics (frozen/offset/drift/
  reliability-fail for sensors; stuck/reversed for actuators; forced-status
  for booleans; device-offline/slow-response/write-rejected/intermittent-
  comm at the transport level).
- **Scenario engine**: 6 shipped scenarios, timed against simulated time.
  `set_value` distinguishes writable (real BACnet write, priority 3) vs.
  non-writable (fault-layer override) targets — a design correction made
  mid-build; see `scenario.py`'s docstring for why.
- **Command-center UI**: interactive building digital twin, equipment and
  point drill-down, 15-second diagnostics, VAV tracking, fault injection,
  force/release, scenario controls, and a confirmed "Stop All
  Simulation." The building graphic is a representative training layout,
  not a construction drawing.
- **No authentication anywhere in the API.** Accepted so far because the
  blast radius was bounded to simulated point values, reversible via Stop
  All Simulation. **This assumption needs to be revisited before Phase 6
  ships anything that can structurally add/remove equipment** — see §7.

---

## 4. Project Structure Reference

```
app/
  config_models.py      Pydantic schema: EquipmentGroupConfig, SupervisoryDeviceConfig, NetworkConfig
  registry.py             PointRegistry (all 318 objects) + GroupView (per-group scoping, fault-aware)
  transport.py             The one bacpypes3 Application; network safety + transport-level faults
  engine.py                 1Hz tick loop; ticks FaultManager, ScenarioEngine, then every equipment model
  faults.py                  FaultManager, FaultType enum, the named-fault-to-mechanic mapping table
  scenario.py                 Scenario/ScenarioEvent schema, ScenarioEngine (+ register_scenario for Phase 6a)
  equipment/                    ahu.py, chiller.py, boiler.py, exhaust_fan.py, site.py, vav_single_duct.py
  llm/                             action_schema.py, action_validator.py, ollama_client.py, prompt_templates.py (Phase 6a)
  services/                          orchestration_service.py, audit_service.py (Phase 6a)
  api.py                          FastAPI REST endpoints (+ /api/llm/* in Phase 6a)
  main.py                          Entry point — loads config, wires everything, starts uvicorn
config/
  network.json                     verified: bind 192.168.168.201:47808; both allowlists = .1-.7 and .200
  supervisory_device.json            The one device's instance/name/description
  devices/*.json                       28 equipment group configs (generated — see script below)
  building_layout.json                   Command-center systems, hotspots, locations, and diagnostic sources
  scenarios/*.json                       6 shipped training scenarios
  llm/                                     models.json, system_prompts.json, policies.json (Phase 6a)
scripts/
  generate_phase3_configs.py               Source of truth for config/devices/*.json — re-run after object-model changes
  generate_point_mapping_workbook.py         Generates the point-mapping Excel workbook from live config
  windows/install.bat, install_offline.bat, download_offline_packages.bat, run.bat   Setup/run
  windows/install_service.bat, uninstall_service.bat, install_scheduled_task.bat      Auto-start on boot
static/command-center.html   Interactive command center + Equipment / Operations / AI Console / Logs views
static/styles.css, app.js    Offline-safe presentation and operator behavior
static/assets/               Generated building digital-twin artwork
static/vendor/               Self-hosted Font Awesome and Rajdhani assets
tests/                       129-test suite: unit, BACnet/IP integration, diagnostics/layout, restart/PID, parent dependencies, regression, faults/scenarios, LLM
PACKAGING.md                      Full install/service/firewall/troubleshooting guide
NEXT_STEPS_INTEGRATION_TESTING.md   Bench laptop deployment step sequence
PHASE6_REVIEW.md                      Architecture review + phased plan for the LLM/dashboard expansion
README.md                            Phase-by-phase technical narrative
docs/COMMAND_CENTER.md                 Expanded topology, diagnostics, UI behavior, and scope boundary
THIRD_PARTY_NOTICES.md                 Font Awesome/Rajdhani attribution and license terms
```

---

## 5. What's Been Verified Live (not just unit tests)

- Real BACnet reads/writes/priority-array resolution, over loopback and
  across separate OS processes.
- **Historical 16/143 baseline:** object-list discovery enumerated all 145
  BACnet objects in that build correctly (device/network objects plus the
  143-point catalog), matching WebCTRL's Discovery mechanism.
- **Historical expanded 28/220 service:** the live API and BACnet registry reported all
  220 points; WebCTRL reads, writes, and COV traffic are confirmed. A full
  WebCTRL graphics/program mapping pass for VAV-6 through VAV-17 remains
  BAS-side follow-up rather than a simulator cutover blocker.
- **Current 28/318 service:** the live API reports all 318 points. The
  dedicated acceptance validated 85/85 new addresses, 68/68 design values,
  and VAV-3's 75% command/feedback path before zero-force/1x cleanup.
  WebCTRL messages/writes are active with zero blocked requests; COV recovery
  reached 50 active subscriptions (29 confirmed and 21 unconfirmed) across
  `.2`, `.5`, `.6`, and `.7`.
- Cross-equipment coupling: AHU-1 cooling valve/fan commands visibly
  changing VAV-1's discharge temp in real time via the in-process
  `AhuModel` reference.
- Hard interlocks: tripping Freezestat Trip forces the fan off and heating
  valve open, confirmed via real BACnet writes and reads.
- **A real bug found this way and fixed**: fan/pump proof-delay states were
  silently never proving on, because a boolean was fed back into a
  first-order lag calculation every tick, discarding fractional progress.
  Fixed; regression tests added specifically for this bug class
  (`tests/test_equipment_proof_delays.py`).
- The single-device merge: unique object *instances* and unique object
  *names* (BACnet requires both, independently — discovered when all
  three boilers originally shared the literal name "Boiler OK" and broke
  startup).
- Fault mechanics tested against real equipment models and real BACnet
  objects, not mocks.
- A full scenario run end-to-end (`vav1_reheat_valve_stuck`): the real
  BACnet object accepted a new command (showed 90%) while the equipment's
  actual behavior (discharge temp) stayed flat — the exact mismatch the
  scenario exists to teach.
- **COV fully verified (2026-07-17, supersedes the earlier open item):**
  subscription, acknowledgment, AND change-driven notification delivery —
  both **confirmed** and **unconfirmed** modes — proven live across OS
  processes against the production port, plus covered by automated tests
  (`tests/test_audit_fixes_and_cov.py`). All three WebCTRL refresh
  strategies are usable on every point.
- **Single-point connection enforcement**: a non-allowlisted source gets
  pure silence (no reply, counted in `messages_blocked`) while an
  allowlisted one reads normally — verified live and by test.
- The audit-fix behaviors (flow-proving interlocks, tower high-head
  climb, VAV reheat clamp, manager mirrors, reliability flagging) each
  carry a dedicated test; the manager mirrors and COV panel were also
  exercised against the live running instance.

---

## 6. Known Open Items Carried Forward

| Item | Status |
|---|---|
| Supervisory device instance 242000 | **Verified** for the bench |
| COV notification delivery | Verified after the current 318-point restart: 50 active subscriptions, including 29 confirmed and 21 unconfirmed across `.2`, `.5`, `.6`, and `.7`. |
| Duplicate BACnet instance / incorrect network number / incorrect units faults | Not implemented — flagged in `faults.py`'s docstring |
| Occupancy modeling | Not implemented |
| Completion criteria / student objectives (scenarios) | Informational text only, not auto-graded |
| "Status Indicator" relay (Simulation Manager) | Assumed out of scope, same category as confirmed-out-of-scope "Safety Trip" — **not explicitly confirmed** the same way |
| NSSM binary | Not shipped, must be downloaded manually (`tools/nssm/PUT_NSSM_EXE_HERE.txt`) |
| ~~Windows batch/service scripts~~ | **FIXED 2026-07-17** — path bug corrected in all seven; `install.bat` executed end-to-end on this machine; service/scheduled-task install still unexercised |
| Full bench deployment | **Operational 2026-07-23** — NSSM service and live WebCTRL BACnet traffic verified; repeat reboot/restore checks after planned upgrades |
| Expanded command-center cutover | **Operational 2026-07-23** — service reports 28 groups / 318 points; 85/85 new addresses, 68/68 design values, VAV-3 75% command/feedback, zero-force/1x cleanup, WebCTRL writes/messages, zero blocked requests, and 50 active COV subscriptions (29 confirmed / 21 unconfirmed) across `.2`, `.5`, `.6`, and `.7` are verified. |
| Parent-equipment realism cutover | **Operational 2026-07-23** — live cooling/heating/ventilation chains, VAV upstream inhibition, 15-second proof failure, UI air states, and full override rollback passed |
| Ollama install on this laptop | **Installed and running** — `0.32.1` at `127.0.0.1:11434`, with `hermes3:3b` selected for the simulator |
| Duplicate-instance startup check on a real NIC | Skipped on loopback (Windows deaf-device bug, §0); behavior on the bench NIC's real broadcast domain still unverified — if BACnet goes silent after startup on the bench, disable `startup_duplicate_instance_check` first |
| Minor audit leftovers (audit §2.4/§3.6) | Chiller `ct_vfd_output`/`byp_vlv_output`/`manager_reset` remain simplified; freezestat does not self-trip on low MA temp; documented realism backlog |

---

## 7. Phase 6 Direction — Reviewed and Phased

A full LLM/Ollama orchestration + dashboard overhaul spec was submitted.
Full critique in `PHASE6_REVIEW.md`; summary and actionable guidance below.

### What's approved conceptually
The structured `llm_action_bundle` schema (mandatory JSON, explicit
`requires_approval`, bounded intent list, reported `confidence`), the
explicit non-roles list (no raw BACnet command issuance, no unsupervised
autonomous control), and "extend through `GroupView`, don't rewrite" are
all sound and should be followed as designed.

### Sequencing decision
**Confirm the remaining Phase 5 bench deployment steps (§2) are actually
complete — including the first real WebCTRL smoke test — before merging
any Phase 6 work into the primary tree.** If something breaks during that
first integration test after Phase 6 work has started, it needs to be
possible to know whether it's a pre-existing Phase 5 issue or a Phase 6
regression. Phase 6 work can happen on a branch/copy in parallel if
desired, but shouldn't become the working tree until Phase 5 is a
confirmed-good baseline.

### Risk-tiered phasing (build in this order)

- **Phase 6a — LLM-assisted scenario/fault generation.** `app/llm/`
  (Ollama client, action schema, validator), `app/services/
  orchestration_service.py` + `audit_service.py`, an LLM Console panel.
  Wired ONLY to `create_scenario` / `inject_fault` / `clear_fault` /
  `set_initial_condition` / `adjust_parameter` / `explain_behavior` /
  `summarize_events` — every one of these maps directly onto the
  *existing, already-tested* `ScenarioEngine` and `FaultManager` APIs with
  a validation layer in front. No BACnet object model changes. **Start
  here.**
- **Phase 6b — Dashboard upgrade.** Tab/panel expansion per the spec,
  *excluding* Trends & Alarms (needs a historian that doesn't exist yet)
  and excluding anything equipment-topology-related. Can run in parallel
  with 6a.
- **Phase 6c — Snapshot/restore, audit trail, trend historian.** New,
  self-contained subsystems that don't touch the live BACnet transport.
  Define precisely what a "snapshot" captures (object present-values? active
  faults? scenario state? each equipment model's internal state like AHU's
  MA/RA/SA temps or a chiller's proven state?) before building — this
  isn't specified in the original spec and needs an explicit answer, not
  an assumption.
- **Phase 6d — Dynamic equipment management** (`add_equipment` /
  `modify_equipment` / `remove_equipment`). **Do not start this until the
  five open questions below have answers.** This is the piece that
  directly intersects the `do_not_break` list's instance-mapping and
  name-uniqueness guarantees, and needs its own test suite proving those
  invariants survive runtime mutation, not just startup-time validation.

### Five questions that need answers before Phase 6d (not before 6a/6b)

1. **Ollama feasibility on this laptop** — RAM/CPU available alongside
   WebCTRL and this simulator already running? Confirm before assuming any
   particular local model is usable.
2. **Persistence model for LLM-added equipment** — survives restart
   (written back to `config/devices/*.json`, re-validated normally) or
   session-only (in-memory, gone on restart)? Changes the implementation
   substantially.
3. **Hot-reload vs. restart-required** — bacpypes3's support for adding/
   removing objects on a *running* Application is untested in this
   project. WebCTRL also caches its own Discovery results, so a live-added
   object may not be visible until WebCTRL re-runs Discovery regardless.
   A "stage the change, apply requires restart + re-Discovery" model may
   be the correct v1 answer rather than true hot-reload — decide this
   deliberately.
4. **Auth boundary** — the current no-auth design was accepted for a
   bounded blast radius (point values, reversible). Structural
   equipment changes are a bigger blast radius. Does the LLM Console (or
   at minimum, `requires_approval` confirmations) need instructor-only
   gating now?
5. **Which model(s)** are actually available via this Ollama install? Affects
   prompt design and realistic expectations for `explain_behavior` /
   `generate_training_lab` quality.

### Explicit instructions carried forward from the original spec (still apply)
- Treat this as in-place evolution, not a rewrite.
- Preserve all verified BACnet behavior unless a change is specifically
  required and tested.
- The LLM is an assistant/orchestrator; the simulator core remains the
  deterministic source of truth. LLM output never bypasses validation to
  touch state directly.
- Do not attempt to resolve the five open questions above by assumption —
  surface them (a Settings/Diagnostics panel showing current bind
  address/port/COV status was specifically requested in the original spec
  and is a reasonable place to also surface Phase 6 open items).
- All existing tests must keep passing; add new tests for every new
  pathway, especially validation/rejection cases.

## 7.5. Phase 6a — Built and Verified

**Status: complete.** `app/llm/` (action schema, Ollama client, prompt
templates, action validator) and `app/services/` (orchestration service,
audit service) are wired into `main.py`/`api.py`, with a new LLM Console
tab in the dashboard (connection status, prompt submission, action preview/
approval, audit trail).

**What's verified live** (not just the 16 new unit/integration tests, all
passing, 40/40 total in the suite):
- `POST /api/llm/apply` with a hand-crafted bundle (simulating a real
  Ollama response) genuinely activated a `frozen_value` fault through
  `FaultManager` — confirmed via `/api/faults` immediately after.
- The Phase 6d safety boundary was tested through the live API, not just
  unit tests: an `add_equipment` bundle was cleanly rejected with a clear
  error and zero partial application — `/api/faults` still showed exactly
  the one fault from the prior successful call, nothing added.
- The audit trail correctly recorded both the successful application and
  would record a rejection the same way.
- At the Phase 6a milestone, the then-current core app (143 objects, 16
  groups, dashboard) was completely unaffected — confirmed via
  `/api/status` before and after. That is historical evidence for the
  pre-command-center baseline, not the current checkout inventory.

**Current Ollama status (supersedes the original sandbox limitation):**
Ollama `0.32.1` is running locally and the AI Console reports
`hermes3:3b`. The mocked HTTP contract tests remain useful for deterministic
coverage. Keep in mind that Ollama is a per-user process while the simulator
is a LocalSystem service, so the simulator can start before login even when
the AI Console is temporarily unavailable.

**What was deliberately deferred at the Phase 6a milestone**: dashboard
expansion beyond the LLM Console (then planned as Phase 6b), snapshot/
audit-trail-beyond-LLM/trend historian work (Phase 6c), and dynamic
equipment management (Phase 6d). The command-center UI expansion was
subsequently delivered in the 2026-07-23 checkout (§0.1); the historian
and dynamic-equipment boundaries remain deferred. The
`generate_training_lab` and `add_equipment`/`modify_equipment`/
`remove_equipment`/`propose_dashboard_layout` intents exist in the schema
(matching the original spec's full contract) but are not in
`PHASE_6A_ALLOWED_INTENTS` — attempting them is cleanly rejected, not
silently ignored or partially handled.

---

## 8. Delivered Artifacts (chronological — use the latest)

| File | Contents |
|---|---|
| ~~`aci-bacnet-sim-phase2.zip`~~ through ~~`aci-bacnet-sim-phase5-service.zip`~~ | Superseded — historical drafts; do not deploy from zips |
| **`github.com/JeffJenkinsBAS/bacnet_simulator` (public), `main` branch** | Published baseline; reconcile with the verified laptop checkout before each release |
| `SIMULATION_AUDIT.md` (in repo) | Full equipment/BACnet audit + fix status |
| `ACI_BACnet_Simulator_Point_Mapping.xlsx` | Current 318-point BACnet object/address/direction catalog |
| `NEXT_STEPS_INTEGRATION_TESTING.md` | Bench laptop deployment step sequence |
| `PACKAGING.md` (inside the zip) | Full install/firewall/service/troubleshooting reference |
| `PHASE6_REVIEW.md` | Full architecture review and phasing rationale for §7 |
| `docs/COMMAND_CENTER.md` | Current 28-group command-center topology, diagnostics, responsive UI, and training-only scope |
| `THIRD_PARTY_NOTICES.md` | License notices for the self-hosted Font Awesome and Rajdhani assets |
| **This file** | Current authoritative status — start here |

---

## 9. Immediate Next Steps

1. **Map the expanded WebCTRL training content.** The simulator service is
   live at 28 groups / 318 points. Existing mappings remain valid; discover
   and map only AV:81 through AV:85 on each VAV for the new design-limit and
   damper-feedback exposure. Add VAV-6 through VAV-17 to desired WebCTRL
   programs/graphics as separate BAS-side training work, then record a
   discovery/binding snapshot.
2. **Preserve the verified network topology.** Before an upgrade, confirm the
   simulator still binds `192.168.168.201:47808` and both allowlists contain
   `.1` through `.7` plus `.200`. Use the dashboard's blocked-request and
   COV panels during smoke testing. Both COV modes recovered after the latest
   restart; the final snapshot records 29 confirmed and 21 unconfirmed active
   subscriptions.
3. **Verify the duplicate-instance startup check behaves on the real
   bench NIC** (see §6) — first boot on the bench, confirm `messages_in`
   climbs during Discovery; if BACnet is silent, disable the check and
   report back.
4. **Keep local AI resource use bounded.** Ollama and `hermes3:3b` are
   installed, but the current 8 GB laptop is better suited to local tools
   with cloud inference. Use the AI Console's connection test
   (`/api/llm/status`) before a class and do not load the 23 GB model.
5. Use Phase 6a for real training, and take Phase 6b's
   remaining scope (trends/alarms need the 6c historian). The
   command-center foundation for 6b is in the current checkout.
6. Do not start Phase 6d (dynamic equipment management) until the five
   questions in §7 have real answers — surface them rather than guess.
