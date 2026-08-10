# BACnet Communication Stall — 2026-07-24

## Impact

WebCTRL could no longer discover device `242000` or communicate with its
points. The dashboard and simulation engine remained available, which made
the failure look like an object or mapping problem even though the point
catalog was intact.

## Evidence

- The Windows service and HTTP API were running.
- The simulator still owned `192.168.168.201:47808/UDP`.
- The BACnet inbound counter was frozen at 602.
- The catalog remained at 28 equipment groups and 329 points.
- The service error log contained repeated confirmed-COV `AbortPDU:
  no-response` callback tracebacks.
- A controlled service restart immediately restored WebCTRL discovery,
  ReadPropertyMultiple traffic, COV renewals, and controller writes from the
  configured bench peers.

No device instance, object type, object instance, point name, or WebCTRL
mapping was changed.

## Root cause and contributing behavior

The in-process Restart implementation released all 16 BACnet priority slots
and rewrote values across the live object database while retaining the
existing COV session. That was unsafe for two reasons:

1. it erased commands owned by WebCTRL; and
2. it generated a large COV change burst while confirmed-COV clients were not
   acknowledging notifications.

BACpypes3 does not catch `AbortPDU` in its confirmed-COV completion callback,
so each timeout produced a full asyncio traceback. The UDP socket could remain
bound while the application stopped servicing useful BACnet traffic.

## Corrective changes

- Restart now preserves WebCTRL-owned BACnet priority-array commands.
- Scenario/instructor priority writes still drain through the scenario engine.
- Faults, safety latches, scenarios, reliability flags, PID memory, equipment
  models, speed, and elapsed simulation time still reset.
- Confirmed-COV `AbortPDU` timeouts are contained, counted, and rate-limited
  instead of flooding the event loop and error log.
- `/api/status` now reports `cov_notification_failures` and the most recent
  failure record.
- The Restart confirmation text and operating documentation now state the
  command-preservation contract explicitly.

## Live recovery acceptance

After the corrected service was loaded:

- Windows service: running/automatic
- BACnet bind: `192.168.168.201:47808/UDP`
- device instance: `242000`
- fleet: 28 groups / 329 points
- WebCTRL/controller reads and writes: observed
- COV renewal: observed
- blocked requests: 0
- confirmed-COV failure counter: 0
- active faults: 0
- active instructor priority overrides: 0
- automated suite: 156 passed
