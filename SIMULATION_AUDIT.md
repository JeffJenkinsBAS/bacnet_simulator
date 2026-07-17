# Simulation Control System Audit

**Date:** 2026-07-17
**Method:** Full source review of every equipment model, the transport/registry/fault/scenario
layers, and all 16 group configs, evaluated against the `webctrl-skill` domain references
(HVAC fundamentals, sequences of operation, chiller/heating plant practice, BACnet protocol
and WebCTRL/EIKON integration standards, ALC field commissioning practice).
Audit only — no behavior was changed. Findings are ranked by training impact.

---

## 1. What Checks Out Well

These match real-world equipment behavior and good BACnet practice, and should be preserved:

- **Interlocks-first-every-tick** (AHU freezestat/high-static, plant Emerg/Refrig trips) mirrors
  hardwired safety circuits overriding whatever the BAS commands — exactly right.
- **Proof delays everywhere**: chiller 30 s start delay, boiler 15 s purge + 10 s ignition before
  proof, pump 3 s, exhaust fan 4 s. Command and status are separate points, and status lags
  command — this is the single most important realism property for WebCTRL training (proof-fail
  logic, status mismatch alarms), and it's done correctly, with a regression test suite
  guarding the exact bug class that once broke it.
- **First-order lag on every analog response** — temps, dampers, valves and airflow trend like
  equipment, not like step functions. Time constants are sane (coil ~20 s, space ~120 s,
  economizer ~15 s).
- **Priority discipline**: instructor forces write the real priority array at priority 3 —
  below the 1–2 life-safety band (reserved per ASHRAE 135 practice), above operator levels —
  and release properly relinquishes. Writable vs non-writable targets take different, correct
  paths (real write vs output override).
- **Single device, globally unique instances and object names**, Who-Is response guard,
  write source allowlist, and a startup duplicate-device-instance probe — all consistent with
  BACnet addressing rules (device instance uniqueness is global; the sim enforces it).
- **Scenario engine runs on simulated time**, so scenarios behave identically at ×1 and ×60,
  and scenario-created faults are torn down cleanly on stop/reset.
- **Speed multiplier scales dt**, so every lag and delay stays proportional under time
  compression.
- The six shipped scenarios map to genuine field patterns (command-received-but-no-mechanical-
  response, slow sensor drift caught by trending rather than alarms, ignition failure, comm loss).

---

## 2. Findings — BACnet / WebCTRL Integration Side

### 2.1 HIGH — Manager-group points are published but dead

No equipment model services the two manager groups, so their points sit at initial values
forever:

| Group | Point(s) | Problem |
|---|---|---|
| ACI-SIM-BOILER-MGR | `enable_boiler1/2/3` (BO, WebCTRL→sim) | **Never read by any model.** A Boiler Manager EIKON program commanding these does nothing. Boilers only obey their own group's `boiler_ss`. |
| ACI-SIM-BOILER-MGR | `boiler1/2/3_ok` (BI, sim→WebCTRL) | **Never written.** Stay inactive forever even with all three boilers proven. |
| ACI-SIM-CHW-PLANT | `chiller1/2/3_ok` (BI, sim→WebCTRL) | Same — never written, stay inactive. |
| ACI-SIM-CHW-PLANT | `chws_temp_common`, `chwr_temp_common`, `chws_flow_common` (AI) | Frozen at 44.0 / 54.0 / 0.0 forever. A Chiller Manager program trending the common header sees a flatline. |
| ACI-SIM-CHW-PLANT | `remote_shutdown` (BO, WebCTRL→sim) | Never read. (The BV trips `emerg_shutdown_trip` / `refrig_shutdown_trip` **are** wired and work.) |

**Impact:** if Jeff's Chiller Manager / Boiler Manager EIKON programs bind to manager-level
points (which is what those points exist for), the manager programs will appear broken during
the first bench integration — while unit-level points work fine, making it confusing to
diagnose.

**Fix:** a small aggregator model per manager group (~40 lines each): mirror each unit's
proof to `chillerN_ok`/`boilerN_ok`; compute the common header from proven chillers
(min of proven CHWS temps, flow = proven-count × per-unit design GPM, CHWR = header + rise);
fan `enable_boilerN` and `remote_shutdown` down into the unit models' enable logic.

### 2.2 HIGH — Transport faults don't intercept ReadPropertyMultiple

`transport.py` guards only `Who-Is`, `ReadProperty`, and `WriteProperty`. WebCTRL's poll
engine primarily uses **ReadPropertyMultiple** for point refresh. Consequences on the real
bench:

- `device_offline` / `intermittent_comm` / `slow_response` faults will NOT affect WebCTRL's
  routine polling — a "device offline" scenario may show the device mostly alive (values
  keep updating; only writes and discovery fail). The comm-loss training scenario silently
  loses its teeth.
- `messages_in` and the traffic log undercount real WebCTRL traffic.

**Fix:** override `do_ReadPropertyMultipleRequest` (and ideally `do_WritePropertyMultipleRequest`
and `do_SubscribeCOVRequest`) with the same `_apply_transport_faults` guard + counters.
Small, mechanical change.

### 2.3 MEDIUM — `reliability_fail` never sets the BACnet Reliability property

The fault substitutes a bad present-value, but `reliability` and the `statusFlags` FAULT bit
are never touched (confirmed: no code writes `reliability` anywhere). A real failed sensor
reports Reliability ≠ no-fault-detected, which WebCTRL surfaces distinctly from a plausible-
but-wrong value. Students should learn to distinguish "flagged unreliable" from "silently
wrong" — currently every failure is silently wrong.

**Fix:** when a `reliability_fail` fault activates/clears on a point, set/restore
`bacnet_object.reliability` (e.g. `no-sensor` or `unreliable-other`) and the statusFlags
fault bit. Needs a small registry hook so FaultManager can reach the object.

### 2.4 MINOR — Chiller unit points that exist but are ignored

`chw_iso_valve` (command) is ignored — `chw_iso_vlv_sts` follows the chiller run command
instead of the commanded valve. `ct_vfd_output`, `byp_vlv_output`, and `manager_reset` are
never read (tower fan is binary-only via `ct_fan_ss`). Either wire them (iso valve status
should track the iso valve command with a stroke delay; tower VFD % could drive approach
temperature) or mark them "reserved / not simulated" in the point mapping workbook so nobody
burns bench time on them.

### 2.5 NOTE — COV strategy for the bench (no change needed)

COV subscribe/ack is confirmed; notification-on-change delivery is not. Per the shop standard
(bacnet-networking skill): refresh time ≤ 30 s = polling, `X:01` = confirmed COV. Until COV
delivery is verified against a live subscription, configure WebCTRL point refresh at ≤ 30 s
(plain polling) rather than an `:01` value — matches the existing plan and the shop's own
decision table.

---

## 3. Findings — Equipment Realism (HVAC Side)

### 3.1 HIGH — VAV reheat discharge temperature is unbounded

`vav_single_duct.py`: `target_rise = valve% × max_reheat_rise (40°F) × dilution_factor`,
where `dilution_factor = max_cfm / effective_cfm` with a 50 cfm floor. At minimum airflow —
which is **normal heating-mode operation** for a VAV box (airflow at Vmin, reheat modulating)
— dilution reaches 24×, so a fully open valve targets a **~960°F rise** and the discharge
temp trends toward ~1,000°F. Physically, discharge air can never exceed the hot-water supply
temperature (~140–180°F); a reheat coil's output also collapses as ΔT across it shrinks.

**Impact:** the most common heating-mode state produces absurd values in WebCTRL trends and
teaches students wrong intuition about reheat behavior.

**Fix (one line + one parameter):** clamp
`target_discharge_temp = min(target_discharge_temp, hot_water_supply_temp_f)` with a
`hot_water_supply_temp_f ≈ 140` parameter in `VavParameters`.

### 3.2 HIGH — Cooling tower physics are backwards when the fan stops

`chiller.py`: fan running → CWS approaches `OA + 7°F`; fan **off** → CWS approaches `OA`
exactly. So condenser water gets **colder** when the tower fan stops — the opposite of
reality, where a proven chiller with no tower fan drives condenser water up toward a
high-head trip (that's *why* the fan exists). Also, a real tower approaches **wet-bulb**
(often below dry-bulb), which is the entire evaporative-cooling story.

**Impact:** a "tower fan failure" training exercise currently shows improving condenser
conditions — actively misleading.

**Fix:** the site model already publishes OA humidity, so approximate wet-bulb (Stull formula
or a simple `WB ≈ DB − (100 − RH)/5` field approximation). Fan running → CWS → `WB + approach`.
Fan off while chiller proven → CWS climbs (e.g., toward `OA + 25°F` with a short time
constant); fan off and chiller off → drift to `OA`. Optionally trip the chiller
(`chiller_status` off) if CWS exceeds ~105°F, teaching the high-head cutout.

### 3.3 MEDIUM — Chillers prove and make 44°F water with both pumps off

`_proven` depends only on `enable + ss + 30 s`. Real chillers have evaporator/condenser
flow-proving interlocks — no CHW flow, no start permit; flow lost while running, unit trips
(classic sequence per the chiller-plant SOO template). Killing a pump mid-run is a bread-and-
butter training scenario the model can't currently express.

**Fix:** gate `_proven` on `chw_pump_running and cw_pump_running`; losing either while proven
drops proof (and holds CHWS at its last value drifting toward return temp, since no flow =
stagnant sensor).

### 3.4 MEDIUM — Boilers prove with the circulating pump off

Same class as 3.3: real boilers have low-water-cutoff / flow interlocks. Gate `_proven` on
`circ_pump_running`.

### 3.5 MEDIUM — Freezestat trip doesn't close the outside-air damper

The AHU freezestat response stops the supply fan and drives the heating valve to 100% — but
leaves the economizer/OA damper wherever WebCTRL commands it, and mixed-air temp keeps
blending OA at full effect with the fan off. The standard freeze response (SOO reference:
"stop supply fan, **close OA damper fully**, open HW valve") exists to stop feeding cold air
to the coil; OA dampers also spring-return closed on fan stop in real AHUs.

**Fix:** force `econ_pct = 0` while `freezestat_trip` (and arguably while both fans are off,
emulating spring return); with the fan off, decay `ma_temp` slowly toward a plenum
temperature instead of continuing the full blend calculation.

### 3.6 MINOR — Accepted simplifications (document, don't necessarily change)

- **Freezestat never self-trips** — it's instructor/WebCTRL-commanded only. A real freezestat
  trips itself below ~38°F coil/mixed-air temp. An optional auto-trip parameter would let the
  "economizer stuck open in winter" lesson end the way it does in the field.
- **SA temp holds forever with fan off** (could drift slowly to ambient); **RA humidity is a
  constant** (never reflects OA humidity or coil latent removal); **virtual zone temps** are
  pulled toward 72°F with bounded influence (~±15%) — self-regulating by design. All are
  reasonable training-sim simplifications; listed so nobody mistakes them for bugs.
- **Damper→airflow is linear** — real installed characteristics are closer to equal-percentage.
  Fine for training; a square-root curve would marginally improve realism.
- **AHU-1 has no fan status point** — deliberate (governing Network I/O report has no such
  point), but it means "fan commanded / no proof" can only be taught on EF-1, the chillers,
  and the boilers, not on AHU-1. Known limitation, not a defect.

---

## 4. Recommended Fix Order

1. **3.1 VAV reheat clamp** — one line; biggest realism payoff per effort.
2. **2.1 Manager-group aggregators** — required before the Chiller/Boiler Manager EIKON
   programs are exercised on the bench.
3. **2.2 RPM transport-fault coverage** — required for comm-loss scenarios to work against
   real WebCTRL polling.
4. **3.2 Tower physics** + **3.3/3.4 flow interlocks** — makes the plant side teach true
   lessons instead of inverted ones.
5. **3.5 Freezestat OA damper** — small, aligns the flagship safety scenario with the SOO
   standard response.
6. **2.3 Reliability flagging** — unlocks the "flagged vs silently wrong" sensor lesson.
7. **2.4 / 3.6** — documentation or opportunistic polish.

All fixes are localized (equipment model internals, one transport override, one small
registry hook) and none touch the object model, instance numbering, or naming — the
`do_not_break` invariants are unaffected. Every fix should land with a matching test, per
the project's standing rule that all existing tests keep passing.
