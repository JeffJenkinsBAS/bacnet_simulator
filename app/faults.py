"""
Fault Injection framework (Phase 4).

Design principle carried over from the single-device refactor: equipment
models never know about faults, exactly like they never needed to know
about the group-merge. Every fault is applied at the GroupView boundary --
the same chokepoint between equipment code and the registry that already
exists -- so ahu.py, chiller.py, vav_single_duct.py, etc. are untouched.

Rather than one code path per named fault in the spec's fault list (24
names), this implements a small set of generic MECHANICS. Named faults are
just a mechanic + a target point + parameters, defined in scenario files or
via the Instructor Panel. The mapping table below is the source of truth
for which named fault uses which mechanic:

    Named fault (spec)              Mechanic            Typical target
    -----------------------------   -----------------   --------------------------------
    Failed sensor                   reliability_fail     any sim_to_webctrl AI/AV
    Frozen sensor value              frozen_value          any sim_to_webctrl AI/AV
    Sensor offset                     offset                 any sim_to_webctrl AI/AV
    Sensor drift                       drift                   any sim_to_webctrl AI/AV
    Open circuit / short circuit        reliability_fail        any sim_to_webctrl AI/AV (parameters.value = out-of-range reading)
    Fan/pump commanded but no proof      forced_status            the *_status/*_ok output alias, value=false
    Proof stuck on                        forced_status             same alias, value=true
    Valve/damper commanded but stuck       stuck_value (input)       the *_command alias
    Reversed actuator response              reversed_actuator          the *_command alias
    VFD fault                                 forced_status              ct_vfd_fault / equivalent alias, value=true
    Low airflow                                stuck_value (output)       airflow alias, low value
    Loss of chilled water / hot water           stuck_value (output)       chws_temp/hws-related alias, bad value
    Device offline                               device_offline             whole application (transport-level)
    Slow response                                  slow_response              whole application (transport-level)
    Write rejected                                  write_rejected              whole application (transport-level)
    Intermittent communication                       intermittent_comm            whole application (transport-level)

Not yet implemented (flagged honestly, not silently skipped): duplicate
BACnet instance, incorrect BACnet network number, incorrect units,
priority-array override as a "fault" (this one is really just the existing
Force Value feature at a chosen priority, not a separate mechanic). See
README's Phase 4 section for the reasoning.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger("aci_sim.faults")


class FaultType(str, Enum):
    frozen_value = "frozen_value"          # output: holds the value at whatever it was when activated
    offset = "offset"                        # output: real value + parameters["offset"]
    drift = "drift"                            # output: real value + accumulated parameters["rate_per_second"] * elapsed
    reliability_fail = "reliability_fail"        # output: force a specific bad reading, flags reliability
    stuck_value = "stuck_value"                    # output OR input: pinned at parameters["value"] (or captured value if none given)
    reversed_actuator = "reversed_actuator"          # input: 100 - commanded (0-100% points only)
    forced_status = "forced_status"                    # output: boolean forced to parameters["value"]
    device_offline = "device_offline"                    # transport: stop responding entirely
    slow_response = "slow_response"                        # transport: add parameters["delay_seconds"] before responding
    write_rejected = "write_rejected"                        # transport: reject all writes regardless of source
    intermittent_comm = "intermittent_comm"                    # transport: randomly drop parameters["drop_probability"] of requests


# Faults that apply at the transport/whole-device level rather than to one point.
TRANSPORT_FAULT_TYPES = {
    FaultType.device_offline,
    FaultType.slow_response,
    FaultType.write_rejected,
    FaultType.intermittent_comm,
}


@dataclass
class FaultInstance:
    fault_id: str
    fault_type: FaultType
    group_id: Optional[str]  # None for transport-level faults
    alias: Optional[str]     # None for transport-level faults
    parameters: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    activated_at_wall_time: float = field(default_factory=time.time)

    def target_key(self) -> tuple[str, str]:
        return (self.group_id or "", self.alias or "")


class FaultManager:
    """
    Owns every active fault. Equipment models never touch this directly --
    it's consulted only through GroupView.get_commanded() and GroupView.set(),
    and through the transport layer for the four transport-level fault types.
    """

    def __init__(self):
        self._faults: dict[str, FaultInstance] = {}

    # ---- management -------------------------------------------------

    def set_fault(
        self,
        fault_id: str,
        fault_type: FaultType,
        group_id: Optional[str],
        alias: Optional[str],
        parameters: Optional[dict[str, Any]] = None,
    ) -> FaultInstance:
        instance = FaultInstance(
            fault_id=fault_id,
            fault_type=fault_type,
            group_id=group_id,
            alias=alias,
            parameters=dict(parameters or {}),
        )
        self._faults[fault_id] = instance
        logger.warning(
            "FAULT ACTIVATED: %s (%s) on %s", fault_id, fault_type.value,
            f"{group_id}.{alias}" if alias else "transport-level"
        )
        return instance

    def clear_fault(self, fault_id: str) -> bool:
        if fault_id in self._faults:
            del self._faults[fault_id]
            logger.info("FAULT CLEARED: %s", fault_id)
            return True
        return False

    def clear_all(self) -> None:
        count = len(self._faults)
        self._faults.clear()
        logger.info("All %d active faults cleared (Stop All Simulation / scenario reset)", count)

    def list_faults(self) -> list[FaultInstance]:
        return list(self._faults.values())

    def is_transport_fault_active(self, fault_type: FaultType) -> Optional[FaultInstance]:
        for f in self._faults.values():
            if f.fault_type == fault_type and f.enabled:
                return f
        return None

    # ---- called once per simulation tick, before equipment ticks -----

    def tick(self, dt_seconds: float) -> None:
        for f in self._faults.values():
            if f.fault_type == FaultType.drift and f.enabled:
                rate = f.parameters.get("rate_per_second", 0.0)
                f.parameters["_accumulated"] = f.parameters.get("_accumulated", 0.0) + rate * dt_seconds

    # ---- applied by GroupView --------------------------------------

    def _matching(self, group_id: str, alias: str) -> list[FaultInstance]:
        return [
            f for f in self._faults.values()
            if f.enabled and f.group_id == group_id and f.alias == alias
            and f.fault_type not in TRANSPORT_FAULT_TYPES
        ]

    def apply_to_output(self, group_id: str, alias: str, value: float) -> float:
        """Called from GroupView.set() -- the simulator publishing a sim->WebCTRL value."""
        for f in self._matching(group_id, alias):
            if f.fault_type == FaultType.frozen_value:
                if "_frozen" not in f.parameters:
                    f.parameters["_frozen"] = value
                value = f.parameters["_frozen"]
            elif f.fault_type == FaultType.offset:
                value = value + f.parameters.get("offset", 0.0)
            elif f.fault_type == FaultType.drift:
                value = value + f.parameters.get("_accumulated", 0.0)
            elif f.fault_type == FaultType.stuck_value:
                value = f.parameters.get("value", value)
            elif f.fault_type == FaultType.forced_status:
                value = 1.0 if f.parameters.get("value") else 0.0
            elif f.fault_type == FaultType.reliability_fail:
                value = f.parameters.get("value", value)
        return value

    def apply_to_input(self, group_id: str, alias: str, commanded_value: Optional[float]) -> Optional[float]:
        """Called from GroupView.get_commanded() -- what the equipment model sees WebCTRL asking for."""
        for f in self._matching(group_id, alias):
            if f.fault_type == FaultType.stuck_value:
                if "_captured" not in f.parameters:
                    f.parameters["_captured"] = commanded_value
                commanded_value = f.parameters["_captured"]
            elif f.fault_type == FaultType.reversed_actuator:
                if commanded_value is not None:
                    commanded_value = 100.0 - max(0.0, min(100.0, commanded_value))
        return commanded_value
