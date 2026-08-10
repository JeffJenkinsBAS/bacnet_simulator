# AHU-1 Command Center and Duct-Static PID Lab

## Status and deployment boundary

The current checkout contains **28 equipment groups / 329 BACnet objects**.
It preserves every object identifier in the historical 321-point catalog and
adds eight read-only AHU-1 sensor and safety objects. The 329-point
Windows-service restart and live acceptance completed on 2026-07-24.
WebCTRL writes and confirmed COV traffic recovered with zero blocked
requests; mapping the eight new objects remains operator work.

The existing process points remain in place:

| Local object | Global object | Alias | Direction | Units | Range/default |
|---|---|---|---|---|---|
| `AV:2` | `AV:9002` | `duct_static_pressure_setpoint` | WebCTRL to simulator | in. H2O | 0.25-2.00 / 1.00 |
| `AV:3` | `AV:9003` | `duct_static_pressure` | Simulator to WebCTRL | in. H2O | 0.00-10.00 |
| `AV:4` | `AV:9004` | `sa_fan_speed_feedback` | Simulator to WebCTRL | percent | 0-100 |

`AV:9003` keeps the same identifier and meaning; only its published maximum
changes from 5.00 to 10.00 in. H2O so a deliberately bypassed overpressure
lesson can report the failure instead of clipping the sensor value.

The eight additions are:

| Local object | Global object | Alias | Direction | Units |
|---|---|---|---|---|
| `AI:5` | `AI:9005` | `ahu_ma_humidity` | Simulator to WebCTRL | percent RH |
| `AI:6` | `AI:9006` | `ahu_sa_humidity` | Simulator to WebCTRL | percent RH |
| `AI:7` | `AI:9007` | `cooling_coil_entering_air_temp` | Simulator to WebCTRL | degrees F |
| `BI:44` | `BI:9044` | `automatic_high_static_trip` | Simulator to WebCTRL | no units |
| `BI:45` | `BI:9045` | `duct_structural_failure` | Simulator to WebCTRL | no units |
| `BI:46` | `BI:9046` | `automatic_freezestat_trip` | Simulator to WebCTRL | no units |
| `BI:47` | `BI:9047` | `cooling_coil_freeze_condition` | Simulator to WebCTRL | no units |
| `BI:48` | `BI:9048` | `cooling_coil_rupture_flood` | Simulator to WebCTRL | no units |

No prior point is renumbered or repurposed. Existing writable
`BV:9100 high_static_pressure_trip` and `BV:9101 freezestat_trip` remain
external/manual hard-interlock inputs; the simulator does not overwrite
them. P/I/D/interval settings remain dashboard/API settings rather than new
BACnet points.

## Detailed AHU graphic and sensor order

The AHU page uses one left-to-right air path:

1. outside air enters from the left through the animated economizer damper;
2. the outside-air stream passes through a prefilter;
3. return air enters from the top and mixes with outside air;
4. return-air temperature/humidity and the return-air smoke detector are
   upstream of the animated return fan;
5. mixed-air temperature/humidity are shown at the mixing plenum;
6. the preheat coil follows the mixing section and animates when its valve is
   open with usable hot water;
7. the freezestat's serpentine sensing element is shown immediately
   downstream of preheat and across the coil face/air path;
8. the cooling coil follows, using `AI:9007` as its entering-air temperature;
9. the reheat coil follows the cooling coil;
10. the animated supply fan follows the coils;
11. supply-air temperature/humidity and the supply-air smoke detector are
    downstream of the supply fan;
12. the high-static safety device is in the supply duct after the fan and
    before the graphical duct break;
13. a jagged break compresses the unshown main-duct length; and
14. the two-thirds duct-static sensor appears after the break and before the
    summarized terminal bank.

The graphic is a training visualization, not a construction drawing. Sensor
and safety locations on a real project must follow approved drawings,
manufacturer instructions, TAB results, code, and the project sequence.

## Economizer suitability and integrated cooling

`AO:9023` remains the WebCTRL-requested economizer position. The simulator
uses a separate effective position so unsuitable outdoor air cannot be forced
through the training unit merely because the BAS request is high.

- Dual enthalpy is preferred: enable at OA minus RA enthalpy `<= -1 Btu/lb`
  and disable at `>= +1 Btu/lb`.
- OA must also remain below the 75-degree-F dry-bulb high limit and the
  55/57-degree-F dew-point enable/disable limits.
- Sensor fallback is dual enthalpy, single enthalpy (28 +/- 1 Btu/lb),
  differential dry bulb (65/67 degrees F), fixed dry bulb, then unavailable
  when OAT is unreliable.
- Unsuitable weather returns the effective stroke to 0%, representing the
  normal 15% ventilation minimum while the fan is proven. Fan-off, safety,
  and mixed air below 45 degrees F close OA fully; the low limit releases at
  47 degrees F.
- Three minutes (180 simulated seconds) at at least 95% effective stroke with
  SAT still above setpoint permits integrated mechanical cooling.

The economizer panel exposes requested/effective position, suitability,
method, OA/RA enthalpy, delta, OA dew point, mixed-air limit, proof timer,
integrated permission, limiting reason, and FDD flags. No new BACnet point is
required, so the configured total remains 329.

## Duct-static process and PID

The supply fan must be both commanded and proven before the pressure loop is
active. Until status is proven, duct pressure and the displayed trend are
exactly zero and controller integral/derivative memory is cleared. A drive
with no run command reports zero frequency; a commanded drive awaiting proof
holds its configured 20 Hz minimum.

With the fan proven, terminal relief is the design-CFM-weighted average of
all 17 VAV damper feedback values. At fixed fan speed, closing dampers raises
pressure and opening dampers lowers it. The direct-acting PID raises fan
speed when actual pressure falls below the WebCTRL setpoint.

The PID produces a 0-100% speed signal, linearly equivalent to 0-60 Hz. The
physical drive is configured for a 20 Hz minimum while its run command is
active, so the fan cannot operate below 33.33% physical speed even when the
PID signal requests less. Whenever supply-fan status is off, `AV:9003` duct
static pressure is exactly zero; when the run command is also off, physical
frequency is zero as well.

Closing dampers reduces common-duct conductance, so the two-thirds sensor
shows a short pressure rise before the PID signal and VFD frequency decrease.
Opening dampers produces the inverse response. A WebCTRL write to `AV:9002`
changes the setpoint used by the PID on the next simulation tick.

The normal control range remains 0.25-2.00 in. H2O. The physical training
model has additional headroom only so an instructor can demonstrate the
consequences of defeating a safety.

| PID setting | Default | Adjustable range | Displayed units |
|---|---:|---:|---|
| Proportional gain | 30.0 | 0-100 | % output / in. H2O |
| Integral gain | 0.25 | 0-1 | % output / (in. H2O x s) |
| Derivative gain | 0.0 | 0-20 | % output x s / in. H2O |
| Calculation interval | 1.0 s | 0.5-10 s | simulated seconds |
| Output bias | 55% | fixed calibration | percent |
| Pressure deadband | 0.01 in. H2O | fixed calibration | in. H2O |
| Output slew | 3%/s | fixed calibration | percent per simulated second |

**Reset Loop** clears only controller integral/derivative memory. It does not
clear a latched safety or catastrophic failure. **Defaults** restores the
recommended controller tuning without changing the WebCTRL setpoint.

## High-static state machine

The simulator separates a correctly operating safety from the consequences
of bypassing it:

1. **Normal:** fan and PID operate normally below the safety threshold.
2. **Automatic high-static trip:** at 4.0 in. H2O, the simulated high-static
   switch latches, publishes `BI:9044 = 1`, stops the supply and return fans,
   and prevents continued pressure rise.
3. **Safety bypass/failure:** an instructor must deliberately apply the
   restricted safety-bypass fault to the simulated high-static device.
   Ordinary PID tuning or a WebCTRL command cannot silently defeat it.
4. **Structural failure:** if the bypassed system rises above the
   representative 5.0-in. H2O training duct-class limit, `BI:9045` latches,
   the duct changes to its damaged/exploded animation, and the AHU outline
   flashes red.

The 5.0-in. H2O threshold is a configurable **training pressure-class
limit**, not a universal rupture rating. Real rectangular duct construction
depends on pressure class, sheet thickness, reinforcement, dimensions,
joint spacing, and installation. The lesson is that the 4.0-in. H2O safety
must act before the modeled construction limit.

## Freezestat and cooling-coil freeze state machine

The normal freezestat is a low-temperature cutout. Its sensing element is
represented across the downstream coil air path so it responds to the
coldest section, not only a single-point average.

1. **Normal protection:** a low coil-entering-air condition near the
   freezestat threshold trips and latches `BI:9046`. The AHU stops the supply
   fan, closes outdoor air, disables cooling, and drives the protective
   heating response.
2. **Safety bypass/failure:** an instructor must deliberately apply the
   restricted safety-bypass fault to the simulated freezestat device.
3. **Below-freezing exposure:** while the bypassed cooling-coil entering air
   remains below 32 degrees F, the simulator accumulates exposure in
   **simulated time**:
   - 20 simulated minutes without useful chilled-water flow; or
   - 60 simulated minutes when the cooling valve is open and chilled-water
     flow is proven.
4. **Catastrophic state:** after the applicable persistence period,
   `BI:9047` and `BI:9048` latch. Ice appears on the cooling coil and the
   command-center flood effect represents a burst hydronic coil.

The longer proven-flow timer is a training approximation of the thermal
protection provided by moving, normally warmer chilled water. Actual freeze
risk depends on water temperature, flow, glycol, coil geometry, airflow, and
ambient exposure; the simulator is not an engineering freeze calculation.

Because these timers use simulated seconds, a 20x or 60x lesson reaches the
same state faster in wall-clock time without changing the modeled exposure.
The 15-second GUI command/status diagnostic remains a separate wall-clock
observation timer.

## Latching and Restart

Automatic safety trips, duct structural failure, cooling-coil freeze, and
coil burst/flood are manual-reset training states. Clearing a bypass fault
prevents additional unsafe operation but does not erase the consequence
already demonstrated.

The guarded **Restart** action is the reset boundary. It stops the engine,
resets scenarios, drains instructor priority writes, clears faults and every
simulator-owned latch, resets reliability, recreates equipment/PID/safety
models, restores 1x/zero elapsed simulated time, announces I-Am, and restarts
the engine. WebCTRL-owned command priority slots are preserved. The live
BACnet object graph is also preserved so existing WebCTRL COV subscriptions
remain attached through the reset.

## Operations workflow

The safety-bypass/failure fault is intentionally restricted to the two
simulated automatic safety devices. It does not transform arbitrary point
values and is not a substitute for forcing `BV:9100` or `BV:9101`.

Recommended lessons:

- run the high-static sequence with the safety healthy and confirm the
  4.0-in. H2O shutdown occurs before structural failure;
- repeat with the high-static safety explicitly bypassed and observe the
  >5.0-in. H2O failure;
- run a cold-air lesson with the freezestat healthy and observe safe
  shutdown;
- repeat with the freezestat bypassed, comparing the 20-minute no-flow and
  60-minute proven-flow exposure paths; and
- use Restart between lessons to clear every simulator latch while retaining
  WebCTRL's current command-priority state.

## WebCTRL mapping and cutover implications

All existing WebCTRL bindings remain valid because no prior object identifier
moves. WebCTRL must discover/map only the eight new read-only objects listed
above. If WebCTRL caches BACnet object metadata, refresh `AV:9003` so its
0.00-10.00 in. H2O maximum is visible.

The 329-point live acceptance completed these release checks:

- validate 28 groups / 329 objects and the exact eight-object delta;
- run the complete automated suite;
- rebuild and visually inspect the point-mapping workbook;
- generate the Obsidian vault to staging, review the merge manifest, and
  preserve all live-only notes and `.obsidian/**`;
- take a fresh checkout/workbook/vault backup;
- perform the approved administrator service restart;
- verify fresh `/api/status`, advancing engine ticks, zero equipment errors,
  zero faults/forces/scenario, 1x speed, and zero blocked BACnet requests;
- verify WebCTRL commands and COV subscriptions recover; and
- run both healthy-safety and bypassed-catastrophe acceptance exercises.

## Historical acceptance

The 2026-07-24 duct-static release expanded the verified 318-point catalog to
321 by adding `AV:9002`, `AV:9003`, and `AV:9004`. Its service restart, PID
tracking, GUI Restart, WebCTRL command recovery, confirmed COV recovery, and
clean 1x handoff remain valid historical evidence for all unchanged points.

## Design references

- ASHRAE Handbook, Duct Construction:
  <https://handbook.ashrae.org/Handbooks/S16/SI/s16_ch19/s16_ch19_si.aspx>
- ASHRAE Handbook, Duct Design:
  <https://handbook.ashrae.org/Handbooks/F17/IP/f17_ch21/f17_ch21_ip.aspx>
- Johnson Controls, Duct Pressure Control:
  <https://docs.johnsoncontrols.com/bas/r/Johnson-Controls/en-US/Smart-Equipment-Controls-Sequence-of-Operation-Technical-Bulletin/5.0/VAV-sequences/Duct-pressure-control>
- Johnson Controls/PENN, A11 Low-Temperature Cutout Mounting:
  <https://docs.johnsoncontrols.com/bas/r/PENN-Controls/en-US/A11-Series-Low-Temperature-Cutout-Control-Installation-Guide/J/Mounting/Mounting-considerations>
- Johnson Controls/PENN, A70 Low-Temperature Cutout Application:
  <https://docs.johnsoncontrols.com/bas/r/PENN-Controls/en-US/A70BA-A70GA-and-A70HA-Temperature-Control-Technical-Bulletin/J/Application?contentId=fVnmDSy6W063XApx5jbxvQ>
- Johnson Controls/YORK, Water-Coil Freeze Protection:
  <https://docs.johnsoncontrols.com/airhandling/r/YORK/en-US/YORK-Solution-Air-Handling-Units-Installation-and-Assembly-Guide/726/Piping-connections/Water-treatment/Freeze-protection?contentId=S45QA7bhHJ6qDjbeL19o9g>
