# HVAC Realism Cutover Checklist

## AHU command-center and safety release — verified live 28/329

The checkout and Windows service are verified at **28 groups / 329 BACnet
objects** after the controlled 2026-07-24 cutover.

### Catalog and compatibility

- [x] Confirm the exact eight additions and no other identifier changes:
  `AI:9005`, `AI:9006`, `AI:9007`, and `BI:9044` through `BI:9048`
- [x] Confirm `AV:9003` keeps its identifier and publishes a 0.00-10.00
  in. H2O range
- [x] Validate 28 groups / 329 objects, unique aliases, object names, and
  global `(object type, instance)` pairs
- [x] Preserve the compatibility fingerprint of all 321 previously verified
  identifiers
- [x] Confirm existing `BV:9100` and `BV:9101` remain writable external/manual
  hard-interlock inputs and are not overwritten by the model

### Physics and command-center acceptance

- [x] Verify the graphic order: OA economizer, prefilter, RA sensors/smoke,
  return fan, mixing/MA sensors, preheat, downstream serpentine freezestat,
  cooling coil, reheat coil, supply fan, SA sensors/smoke, high-static
  switch, duct break, and two-thirds duct sensor
- [x] Verify valve, damper, return-fan, and supply-fan animations follow live
  command/proof state
- [x] With suitable OA, verify the economizer panel selects dual enthalpy,
  reports requested/effective position, and permits the WebCTRL damper request
- [x] With high OA enthalpy or OA dew point, verify the effective economizer
  stroke returns to minimum ventilation while the raw request remains visible
- [x] Inject OA/RA sensor reliability faults and verify the fallback ladder:
  dual enthalpy, single enthalpy, differential dry bulb, fixed dry bulb, then
  unavailable when OAT is unreliable
- [x] Verify the 45/47-degree-F mixed-air low-limit hysteresis and the
  180-simulated-second full-open proof before integrated cooling
- [x] With safety healthy, force pressure toward 4.0 in. H2O and verify the
  automatic trip latches and stops both fans before structural failure
- [x] Apply the restricted high-static safety-bypass fault, exceed the
  representative 5.0-in. H2O training duct-class limit, and verify structural
  failure, damaged-duct animation, and AHU red flash latch
- [x] With freezestat healthy, verify low cooling-coil entering air produces
  protective shutdown rather than coil damage
- [x] With the freezestat bypassed and the physical OA damper failed open,
  verify below-32-degree-F exposure uses 20
  simulated minutes without useful chilled-water flow and 60 simulated
  minutes with cooling-valve command and proven chilled-water flow
- [x] Verify freeze/ice and burst/flood states latch and clearing the bypass
  fault alone does not erase the demonstrated consequence
- [x] Verify 15-second GUI command/status diagnostics still use wall-clock
  time while safety exposure timers use simulated time
- [x] Verify Restart clears all safety/failure latches, faults, scenario,
  priority-3 forces, PID memory/history as designed, speed, and elapsed time

### Artifact and service release

- [x] Run the complete automated suite: 155 passed
- [x] Rebuild, inspect, and render every workbook sheet for the 329-point
  catalog and AHU Command Center & Safeties page
- [x] Generate the Obsidian vault to staging and review the merge manifest
- [x] Back up the live vault, checkout, and workbook at
  `artifacts/pre-329-cutover-20260724-111230`
- [x] Merge only the reviewed 23 generator-owned notes and preserve all
  live-only notes plus `.obsidian/**`
- [x] Obtain approval for the administrator/UAC service restart
- [x] Restart and verify fresh 28-group / 329-point `/api/status`, advancing
  engine ticks, 1x speed, and zero model errors
- [x] Verify zero active faults, forces, scenario, and blocked BACnet requests
- [x] Verify WebCTRL writes and confirmed COV subscriptions recover
- [ ] Discover/map only the eight new read-only points in WebCTRL
- [ ] Refresh cached `AV:9003` metadata in WebCTRL if its maximum remains 5.00
- [x] Record normal-trip, bypassed-failure, freezestat, Restart, and clean
  handoff evidence

Design and state-machine details: `docs/DUCT_STATIC_PID_LAB.md`.
Final live evidence:
`artifacts/live-ahu-command-center-acceptance-20260724.md`.

The remaining sections are historical acceptance records. Their catalog and
test counts describe the release named in each heading and must not be read
as the current 329-point release boundary.

---

## Duct-static PID and restart addendum — verified live 28/321

The checkout and Windows service are verified at **28 groups / 321 points**
after the controlled 2026-07-24 cutover.

- [x] Add only AHU-1 AV:9002 setpoint, AV:9003 actual pressure, and AV:9004
  fan-speed feedback
- [x] Preserve the identifier fingerprint of all 318 previously deployed
  points
- [x] Validate the configured catalog at 28 groups / 321 points
- [x] Model fixed-speed damper/pressure response and design-CFM-weighted VAV
  demand
- [x] Implement bounded direct-acting P/I/D control, off-state zeroing,
  anti-windup, filtered derivative, and output slew
- [x] Add the Duct Static PID command-center page, trend, sensor graphic, and
  adjustable P/I/D/interval controls
- [x] Add a guarded in-process Restart control that rebuilds equipment state,
  faults, instructor priorities, simulation time, and PID memory while
  preserving the live BACnet object graph, COV subscriptions, and
  WebCTRL-owned priority commands
- [x] Automated suite passes 129 tests
- [x] Update the reviewed Windows restart health check to require 321 points
- [x] Regenerate and inspect the 321-point point-mapping workbook
- [x] Generate and review the Obsidian vault staging pack
- [x] Back up the checkout, workbook, and live vault
- [x] Obtain operator approval for the administrator/UAC service restart
- [x] Restart once and verify fresh 28-group / 321-point `/api/status`
- [x] Verify `/api/ahu/duct-static`, engine ticks, and zero equipment errors
- [ ] Discover/map AV:9002, AV:9003, and AV:9004 in WebCTRL
- [ ] Verify WebCTRL writes the active pressure setpoint to AV:9002
- [ ] Prove AHU-1 and observe fan-speed/pressure response to opening and
  closing VAV demand
- [x] Exercise the Restart button and verify WebCTRL BACnet/COV recovery
- [x] Leave zero faults, forces, scenarios, and blocked messages at 1x speed

Design and acceptance details: `docs/DUCT_STATIC_PID_LAB.md`.

---

The VAV point-exposure package completed its controlled cutover and dedicated
acceptance on 2026-07-23. The checkout and running Windows service are
verified live at **28 groups / 318 points**. Earlier 233-, 220-, and
219-point sections remain below as historical acceptance records.

## VAV point-exposure addendum — verified live 28/318

- [x] Generated configuration validates at **28 groups / 318 points**
- [x] Exactly 85 points were added: five read-only AVs on each of VAV-1
  through VAV-17
- [x] The new local instances are `AV:81` Heating Min Airflow, `AV:82`
  Heating Max Airflow, `AV:83` Cooling Min Airflow, `AV:84` Cooling Max
  Airflow, and `AV:85` Damper Position Feedback
- [x] The identifier fingerprint of all 233 previously deployed points is
  preserved; no existing point identifiers were changed
- [x] WebCTRL remapping scope is limited to the 85 new AVs; existing
  mappings remain valid
- [x] Automated suite passes **107 tests**, including transport rejection of
  network writes to AV:81 through AV:85
- [x] Point-mapping workbook rebuilt from the 318-point configuration,
  rendered, inspected, and verified with zero formula errors
- [x] Obsidian 318-point design/point-allocation pack generated, reviewed,
  and merged; all 110 generated files match staging, all 20 live-only files
  remain, and `.obsidian/**` was not replaced
- [x] Post-restart acceptance script prepared and syntax-checked:
  `scripts/live_vav_point_exposure_acceptance.ps1`
- [x] Take a fresh pre-cutover project and live-vault backup:
  `artifacts/pre-vav-point-exposure-cutover-20260723-180858`
- [x] Obtain approval for the administrator/UAC Windows-service restart
- [x] Restart the service and verify `/api/status` returns **28 groups /
  318 points** with fresh uptime
- [x] Verify engine ticks advance and no equipment model reports errors
- [x] Verify live WebCTRL messages and writes recover with zero blocked
  requests
- [x] Observe 50 active COV subscriptions after the rebuilding window: 29
  confirmed and 21 unconfirmed across `.2`, `.5`, `.6`, and `.7`
- [ ] Discover/map the five new read-only AVs for each VAV in WebCTRL
- [x] Verify all **85/85** new point addresses read correctly; the automated
  transport regression confirms AV:81 through AV:85 reject network writes
- [x] Verify all **68/68** configured VAV design-airflow values
- [x] Exercise VAV-3 at a 75% damper command and observe 75% feedback
- [x] Confirm zero faults/forces, 1x speed, no active scenario, and zero
  blocked requests at handoff

Dedicated live evidence:
`artifacts/live-vav-point-exposure-acceptance-20260723-195858`.

Reviewed restart log:
`artifacts/vav-point-exposure-restart-20260723.log`.

The dedicated acceptance passed. Full post-restart COV recovery is recorded in
`artifacts/live-vav-point-exposure-acceptance-20260723-195858/03-webctrl-cov-recovery.json`.
The only remaining point-exposure task is WebCTRL discovery/mapping of the 85
new read-only AVs.

## VAV diversity and humidity addendum — historical live 28/233

- [x] Automated suite passes **105 tests**
- [x] Generated configuration validates at **28 groups / 233 points**
- [x] All 220 prior point identifiers match the working 28/220 baseline
- [x] Zone Humidity exists only at VAV-3 through VAV-15 local `AI:3`
- [x] Published humidity identifiers are exactly `AI:13003`–`AI:25003`
- [x] Virtual-zone areas, CFM limits, starting temperatures, loads, and
  thermal/moisture capacities are varied
- [x] Analytical heat/moisture regressions cover zero-airflow, 400/2,120 CFM
  response, slow humidity, and parent-proof behavior
- [x] VAV-11's 2,120-CFM airflow object range covers its design maximum
- [x] Analog BACnet min/max metadata is published and out-of-range network
  and instructor writes are rejected
- [x] Stop/reset relinquishes tracked priority-3 slots and restores a
  pre-existing lower-priority command
- [x] Writable priority-3 instructor overrides are visible as `forced` in
  `/api/points`
- [x] Virtual-zone physical temperature is independent of faulted sensor
  indication
- [x] VAV model profiles reject zero/negative physical values and impossible
  minimum/heating/design airflow order
- [x] `static/app.js` and `command-center.html` are ASCII-safe; temperature
  rendering uses Unicode escapes/entities and cannot decode as `Â°F`
- [x] Point-mapping workbook rebuilt and visually verified at 28/233
- [x] Obsidian staging pack generated and its reviewed 50-file copy set
  merged without replacing live-only notes or `.obsidian/**`
- [x] Take a fresh project and live-vault backup
- [x] Run the reviewed administrator service restart
- [x] Verify `/api/status` returns 28 groups / 233 points and fresh uptime
- [x] Verify the engine tick advances and no equipment model reports errors
- [x] Verify WebCTRL reads, writes, priority release, polling, ConfirmedCOV,
  and UnconfirmedCOV recover
- [ ] Rediscover/map all 13 new Zone Humidity AIs in WebCTRL
- [ ] Trend representative 400-CFM and 2,120-CFM zones for slow, plausible
  temperature response
- [x] Verify command-center DAT, zone temperature, thermal delta, outdoor
  temperature, and weather toasts render `°F` without mojibake
- [x] Confirm zero faults/forces, 1x speed, no active scenario, and zero
  blocked requests at handoff
- [x] Merge the reviewed 50-file Obsidian copy set while preserving all
  20 live-only notes and `.obsidian/**`

The unchecked humidity-mapping and trend items above are retained exactly as
historical acceptance follow-ups. They do not indicate that any existing
233-point WebCTRL binding must be remapped for the live 318-point package;
the current remapping scope is only the 85 AVs listed in the new addendum.

Backup: `artifacts/pre-vav-realism-cutover-20260723-170746`.

Live realism evidence:
`artifacts/live-realism-acceptance-20260723-170906`.

Live AHU SAT evidence:
`artifacts/live-ahu-sat-acceptance-20260723-173703`.

The final closed-damper correction completed its reviewed administrator
restart and focused live acceptance:
`artifacts/live-vav-airflow-acceptance-20260723-174258`.

- [x] 0% VAV damper with AHU proven publishes 0-3 CFM leakage
- [x] AHU proof off publishes exactly 0.00 CFM
- [x] Acceptance cleanup leaves zero forces and 1x speed

## AHU SAT setpoint addendum — completed 2026-07-23

- [x] Automated suite passes **92 tests**
- [x] Point-mapping workbook reports **28 groups / 220 points**
- [x] Exactly one writable AHU-1 SAT setpoint is live at `analog-value:9001`
- [x] 50% AHU heating settles near 85 F at 70 F OA
- [x] Cold OA increases the ventilation heating load; 72% heat restores 85 F
  SAT at 40 F OA
- [x] The same setpoint reset to 55 F is maintained by the cooling valve when
  chilled water is available
- [x] A normal coil-valve cross-ramp receives a 60-simulated-second travel
  window
- [x] Persistent cooling/heating overlap becomes a red energy-waste failure
  after 15 real seconds and identifies WebCTRL priority locks as a likely cause
- [x] UI inspector shows both valve commands/effective positions, SAT,
  setpoint, overlap, and changeover state
- [x] Service returned at 28/220 with WebCTRL traffic, zero blocked requests,
  zero remaining forces, 1x speed, no injected faults, and no active scenario

Backup: `artifacts/pre-ahu-sat-cutover-20260723-150500`.

Live evidence: `artifacts/live-ahu-sat-acceptance-20260723-150930`.

The remainder of this file preserves the earlier parent-dependency cutover
record, including its historical 28/219 catalog count.

## Before the administrator action

- [x] Working checkout is `C:\bacnet_simulator-main`
- [x] Automated suite passes 87 tests
- [x] 41 JSON configuration files parse
- [x] `static/app.js` passes Node syntax validation
- [x] Point-mapping workbook regenerated for 28 groups / 219 points
- [x] Obsidian staging pack regenerated; live vault not modified
- [x] Confirm no active class/demo or fault exercise is in progress
- [x] Capture current `/api/status`, active scenario, forces, faults, and COV
  subscription summary
- [x] Take a fresh project/vault backup

Backup: `artifacts/pre-cutover-20260723-133633` (project, live vault, and
pre-cutover API state).

## Controlled restart

The existing reviewed Windows service script was run once with administrator
approval. The service returned cleanly at 13:37 local time.
Do not run a second simulator process beside the service, and do not change the
verified BACnet bind/allowlist during this cutover.

## Local API acceptance

- [x] `/api/status` reports 28 groups / 219 points
- [x] `/api/command-center` contains top-level `systems` and `air_summary`
- [x] Every VAV location contains `space` and `air_delivery`
- [x] No startup/configuration errors appear in `aci_sim.log`
- [x] No blocked requests appear for known WebCTRL BACnet peers

## Physical-chain acceptance

- [x] AHU fan off: VAV airflow decays to zero and VAV diagnostics show
  `inhibited`, not seventeen failures
- [x] Chiller unavailable + cooling valve open: AHU cannot create mechanical
  cooling
- [x] Chiller proven with CHW/CW pumps and isolation: AHU SAT settles near
  52-58 degrees F at nominal 44-degree CHWS
- [x] Boiler unavailable + VAV reheat command: DAT stays near upstream air
- [x] Boiler and distribution pumps proven: VAV DAT can rise toward 88-95
  degrees F
- [x] Cooling, heating, ventilation, and off states match DAT/zone/parent state

Live evidence: `artifacts/live-realism-acceptance-20260723-134142`.

## WebCTRL acceptance

- [x] WebCTRL reads the device and existing bound points
- [x] A WebCTRL write changes the intended command at the intended priority
- [x] Release returns the point to the configured lower priority/default
- [x] Polling, ConfirmedCOV, or UnconfirmedCOV traffic is present as configured
- [x] VAV-1 and VAV-2 still use their external zone-temperature controllers
- [x] Additional VAV-6 through VAV-17 remain simulator-side until their
  WebCTRL programming/graphics are deliberately mapped

During acceptance the process recorded live traffic from `192.168.168.2`,
COV subscriptions rebuilding from 13 to 26 across confirmed and unconfirmed
modes, and zero blocked requests.

## UI acceptance

- [x] Building view shows the seven-item state/air legend
- [x] Cooling air is blue, heating air is red, ventilation is white/gray
- [x] Plumes loop subtly and stay inside their configured spaces
- [x] Floor filters hide markers and plumes together
- [x] VAV inspector shows conditioning source, airflow, DAT, zone temperature,
  sensible effect, damper, and reheat valve
- [x] No browser console errors or horizontal overflow at bench resolution

Browser evidence: `artifacts/design-qa/live-cooling-1280x720.png`,
`live-heating-1280x720.png`, `live-ventilation-1280x720.png`, and
`live-responsive-1024x768.png`.

## Rollback

If the device does not recover cleanly, stop after one controlled attempt.
Restore the pre-cutover project backup, restart the service once, and verify
the previous 28/219 baseline before further diagnosis. Do not alter WebCTRL
bindings, device instance `242000`, UDP `47808`, or the verified peer
allowlists as a troubleshooting shortcut.
