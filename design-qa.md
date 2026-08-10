# Command Center Design QA

## AHU command-center and safety release — pending verification

- Verify the detailed AHU artwork follows the required physical order:
  economizer, prefilter, RA sensors/smoke and return fan, mixing/MA sensors,
  preheat, downstream serpentine freezestat, cooling coil, reheat coil,
  supply fan, SA sensors/smoke, high-static switch, duct break, and two-thirds
  duct sensor.
- Verify economizer, coil, return-fan, and supply-fan animations follow live
  command/proof values and respect reduced-motion preferences.
- Verify healthy 4.0-in. H2O high-static trip, restricted bypass, >5.0-in.
  H2O damaged-duct animation, and whole-AHU red flash are visually distinct.
- Verify healthy freezestat protection, below-freezing exposure progress,
  frozen-coil state, and burst/flood state are visually distinct and include
  readable text/status indicators rather than color alone.
- Verify the 1024x768 laptop layout keeps the AHU graphic, sensor bubbles,
  safety state, trend, PID controls, and Restart action reachable.
- Verify the 1920x1080 layout does not stretch the hero asset, disconnect
  overlays from components, or leave excessive empty space.
- Verify the Equipment view reports all 329 objects and the Operations form
  exposes safety bypass only for the two simulated automatic safety devices.
- Verify Restart clears graphical latches and returns the page to normal
  state without requiring a browser refresh.

Do not mark this section complete until browser QA is performed against the
running 329-point service.

## Visual target

- Reference: `C:\Users\Test Bench\Pictures\data-screen-chart-graphic-ui-260nw-1876135675.png`
- Generated building asset: `C:\bacnet_simulator-main\static\assets\building-digital-twin.png`
- Live implementation: `C:\bacnet_simulator-main\artifacts\design-qa\implementation-1672x941.png`
- Bench-size implementation: `C:\bacnet_simulator-main\artifacts\design-qa\implementation-1024x768.png`
- Live failure state: `C:\bacnet_simulator-main\artifacts\design-qa\failure-1672x941.png`
- Combined comparison input: `C:\bacnet_simulator-main\artifacts\design-qa\comparison.png`
- Generated airflow source asset:
  `C:\bacnet_simulator-main\static\assets\airflow-wisp.png`
- Live cooling state:
  `C:\bacnet_simulator-main\artifacts\design-qa\live-cooling-1280x720.png`
- Live heating state:
  `C:\bacnet_simulator-main\artifacts\design-qa\live-heating-1280x720.png`
- Live ventilation state:
  `C:\bacnet_simulator-main\artifacts\design-qa\live-ventilation-1280x720.png`
- Live bench-size state:
  `C:\bacnet_simulator-main\artifacts\design-qa\live-responsive-1024x768.png`

## Comparison

The implementation preserves the reference's near-black canvas, cyan telemetry
accents, technical Rajdhani typography, compact command framing, and a dominant
centerpiece. The globe was intentionally replaced with the source-backed
isometric building. Live telemetry, equipment markers, the detail inspector,
floor filters, and the failure queue provide functional information density
instead of decorative side panels.

The 1672 x 941 view keeps the building, primary telemetry, navigation, and
inspector visible together. The 1024 x 768 bench view has no horizontal
overflow, retains every operator control, and uses vertical scrolling for the
building stage and inspector.

The airflow extension preserves that hierarchy. The plume is a real generated
image asset placed below the equipment-marker layer, uses screen blending at
low opacity, and reuses the established cyan/white/red command-center
language. Cooling retains the base cyan asset, heating uses a red-shifted
filter, and ventilation is desaturated to white/gray. The animation changes
translation and opacity rather than moving the plume away from its associated
space.

## Interaction and state QA

- Verified 34 source-backed equipment markers render.
- Verified clicking AHU-1 opens command, status, floor, group, and diagnostic
  details.
- Verified the Roof filter leaves exactly EF-1 and three cooling towers visible.
- Verified navigation to the 220-point Equipment view.
- Verified a forced VAV-6 airflow mismatch remains in tracking for 15 real
  seconds, then produces a red marker, red building outline, failure inspector,
  and diagnostic-queue entry.
- Verified releasing the test override clears the diagnostic and restores the
  configured airflow setpoint.
- Verified no dashboard console warnings or errors.
- The realism process is live. After the controlled service restart, the
  process reported 28 groups / 220 points, accepted BACnet writes from
  `192.168.168.2`, rebuilt 50 COV subscriptions, and logged zero blocked
  requests or application errors during acceptance.
- In the in-app Browser at 1280 x 720, verified a controlled four-state
  airflow preview produced two cooling plumes, one heating plume, and one
  ventilation plume. All four loaded the real source asset and ran the
  `airflow-loop` animation.
- Verified computed airflow opacity remained subtle (0.219-0.272 in the
  preview), all plume bounds stayed inside the building stage, and each mode
  received its distinct cooling, heating, or ventilation filter.
- Verified the updated page had no horizontal overflow and the browser console
  remained free of warnings/errors.
- Verified floor filtering and the persistent node map update plume visibility
  without recreating the entire building scene.
- Verified the inspector exposes air mode, conditioning source, CFM, DAT, zone
  temperature, thermal delta, and sensible effect.
- Verified the 92-test suite, including the real chiller/CHW-manager/AHU/VAV
  cooling chain and boiler/HW-manager/VAV reheat chain.
- Verified the AHU-1 inspector exposes cooling/heating command and effective
  positions, SAT, the single SAT setpoint, simultaneous overlap, and
  changeover state with readable percent and degree-Fahrenheit formatting.
- Verified a persistent 40% cooling / 40% heating command produces the red
  `simultaneous_heating_cooling` failure and WebCTRL priority-lock warning,
  while a cross-ramp remains inside its actuator-travel window.
- Verified the versioned command-center script loads without browser console
  warnings or errors.
- Verified live VAV-3 states against the running service: cooling at
  1,000 CFM / 57.0 F DAT, heating at 300 CFM / 95.0 F DAT, and neutral
  ventilation at 800 CFM / 75.2 F DAT.
- Verified the live DOM contains 34 equipment markers, 17 VAV airflow layers,
  and the seven-item equipment/air legend.
- Verified clicking VAV-3 updates the live inspector and `OPEN IN EQUIPMENT`
  drills into the correct preselected six-point VAV-3 group.
- Verified no horizontal overflow at 1280x720 or 1024x768. The in-app browser
  honors the operating system's reduced-motion preference by collapsing the
  loop duration; the normal CSS contract remains `airflow-loop ... infinite`.

## Issues found and resolved

- P0: Instructor Release passed an untyped null to bacpypes3, leaving commandable
  points forced. Fixed with a typed `Null(())` priority-array release and added
  binary and analog regression coverage.
- P2: STOP ALL referenced an icon absent from the bundled Font Awesome subset.
  Replaced it with the available triangle-exclamation icon.
- P1: The previous-process blocker was resolved by the controlled service
  restart. Live API and browser checks now cover the actual running realism
  process; no open P0/P1/P2 design issue remains.

## Final result

final result: passed
