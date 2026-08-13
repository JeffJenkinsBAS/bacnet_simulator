# Plant Controller Timing Package

## Release boundary

This package expands the configured catalog from 355 to **400 BACnet
objects** and the graded scenario catalog from 16 to **18 scenarios**. The
45 additions are read-only local-controller telemetry on Chillers 1-3 and
Boilers 1-3. Every prior object type/instance and alias remains unchanged.

The prior 355-point release is live-accepted. The 400-point checkout is not
live until the Windows service is restarted and the new objects pass
WebCTRL discovery, mapping, readback, and COV acceptance.

## Control authority

WebCTRL continues to own plant staging requests:

- Chillers respond to `chiller_enable` and `chiller_ss` plus the existing
  pump, isolation-valve, tower, and hard-interlock points.
- Boilers respond to the Boiler Manager `enable_boilerN` points or the
  existing unit `boiler_ss` front door.
- The simulator does not autonomously rotate lead/lag equipment or invent a
  competing load-based staging sequence.
- Each simulated OEM controller enforces local minimum-run and minimum-off
  timing after WebCTRL makes a request.
- Emergency shutdown, refrigerant shutdown, remote shutdown, high-head
  lockout, failed proof, and lost physical flow override minimum-run holds
  immediately.

## Configured timing

| Equipment | Startup sequence | Minimum run | Minimum off |
|---|---:|---:|---:|
| Chiller 1-3 | 45 s proof + 30 s capacity ramp | 180 s | 300 s |
| Boiler 1-3 | 30 s purge + 5 s ignition | 120 s | 60 s |

The values live in each equipment group's `model_parameters` and are wired
through the production equipment factory. Invalid negative values fail
construction. Checkpoint restore captures timers, states, counters, and
outputs so a named baseline remains deterministic.

## Operating-state codes

BACnet multi-state objects are intentionally not implemented, so
`operating_state` is a read-only AV.

| Code | Chiller | Boiler |
|---:|---|---|
| 0 | Off | Off |
| 1 | Minimum-off / anti-recycle | Minimum-off / anti-cycle |
| 2 | Starting / proof delay | Pre-purge and ignition |
| 3 | Running and proven | Running and proven |
| 4 | Minimum-run hold | Minimum-run hold |
| 5 | Safety lockout / hard interlock | Reserved |
| 6 | Waiting for a physical permissive | Waiting for a physical permissive |

## BACnet allocation

Each chiller adds local `AV:80-84` and `BI:46-48`:

| Local object | Alias | Purpose |
|---|---|---|
| AV:80 | `operating_state` | Numeric state code |
| AV:81 | `start_count` | Successful compressor starts |
| AV:82 | `minimum_run_remaining` | Seconds remaining in minimum run |
| AV:83 | `minimum_off_remaining` | Seconds remaining before start permit |
| AV:84 | `compressor_capacity` | Effective compressor capacity percent |
| BI:46 | `anti_recycle_active` | Immediate restart blocked |
| BI:47 | `minimum_run_hold_active` | Normal stop held by minimum run |
| BI:48 | `safety_lockout` | High-head/hard-safety lockout |

Each boiler adds local `AV:80-83` and `BI:43-45`:

| Local object | Alias | Purpose |
|---|---|---|
| AV:80 | `operating_state` | Numeric state code |
| AV:81 | `start_count` | Successful burner starts |
| AV:82 | `minimum_run_remaining` | Seconds remaining in minimum run |
| AV:83 | `minimum_off_remaining` | Seconds remaining before start permit |
| BI:43 | `anti_recycle_active` | Immediate restart blocked |
| BI:44 | `minimum_run_hold_active` | Normal stop held by minimum run |
| BI:45 | `start_permissive` | Circulator and proof chain permit start |

Global addresses equal the equipment offset plus the local number. The
updated `ACI_BACnet_Simulator_Point_Mapping.xlsx` contains every exact
address and a dedicated Plant Controller Timing sheet.

## Graded labs

- `chiller_anti_recycle_timing`: first proof, early normal stop, visible
  minimum-run hold, immediate restart request, anti-recycle countdown,
  second proof, and start-counter verification.
- `boiler_anti_cycle_timing`: circulator permissive, purge/ignition, early
  stop, minimum-run hold, anti-cycle block, second proof, and start count.
- Existing excessive-bypass and CHW-pump-proof-loss labs now grade the
  anti-recycle period before chiller proof can recover.

## Required live acceptance

1. Merge/pull the package, back up the service checkout, and restart once.
2. Verify `/api/status` reports 28 groups, 400 points, and 18 scenarios.
3. Confirm simulation ticks advance at 1x with no active scenario or fault.
4. Discover/map the 45 new read-only objects only; do not remap old IDs.
5. Verify operating state, timers, counters, proof, permissives, and capacity
   read correctly in WebCTRL.
6. Trend one chiller and one boiler lab and confirm COV delivery with zero
   notification failures or blocked BACnet messages.
7. Restore a named baseline and verify state/timers/counters restore
   deterministically while external WebCTRL priorities follow the selected
   reconciliation policy.
8. Record evidence, return to 1x, and confirm no scenario/fault/force remains.

## Source verification

- All 204 collected tests pass with each test module executed in an isolated
  process, including the production factory and both new graded labs.
- The controller/scenario focus set passes 6 tests; hydronic, coupling,
  command-center, and VAV regressions pass 29 tests.
- Configuration generation is repeatable and `git diff --check` is clean.
- A monolithic Windows `pytest -q` invocation did not terminate within ten
  minutes even though all 204 cases pass by module. Treat that as a test-runner
  process-lifecycle issue to investigate, not as live acceptance evidence.
