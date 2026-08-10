"""
Point Registry.

This is the boundary between the BACnet Transport Layer and the Equipment
Models layer. Equipment models never touch bacpypes3 objects directly and
never hardcode an object type/instance -- they ask the registry for a named
alias (e.g. "airflow") and get back a plain Python value.

Architecture note: every equipment group's objects now live under one
BACnet device (see config_models.py docstring), so this module builds ALL
of them into a single flat table, keyed by `"{group_id}.{alias}"`, with the
group's `instance_offset` applied to get the real global object instance.
Equipment models still only ever see bare aliases like "airflow" -- they get
a `GroupView`, a thin wrapper scoped to one group_id, so nothing about the
merge is visible to equipment code. This is what let the Phase 3 equipment
models (ahu.py, chiller.py, etc.) survive this refactor with zero changes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from bacpypes3.local.analog import AnalogInputObject, AnalogOutputObject, AnalogValueObject, AnalogValueObjectCmd
from bacpypes3.local.binary import BinaryInputObject, BinaryOutputObject, BinaryValueObject, BinaryValueObjectCmd
from bacpypes3.basetypes import EngineeringUnits, Reliability, StatusFlags

from app.config_models import EquipmentGroupConfig, ObjectType, PointConfig

logger = logging.getLogger("aci_sim.registry")


_UNITS_MAP = {
    "degrees-fahrenheit": EngineeringUnits.degreesFahrenheit,
    "percent-relative-humidity": EngineeringUnits.percentRelativeHumidity,
    "percent": EngineeringUnits.percent,
    "cubic-feet-per-minute": EngineeringUnits.cubicFeetPerMinute,
    "gallons-per-minute": EngineeringUnits.usGallonsPerMinute,
    "inches-of-water": EngineeringUnits.inchesOfWater,
    "no-units": EngineeringUnits.noUnits,
}


def _units(name: str) -> EngineeringUnits:
    if name not in _UNITS_MAP:
        raise ValueError(
            f"units '{name}' is not in the known units map -- add it to "
            f"_UNITS_MAP in registry.py rather than guessing a bacpypes3 enum name"
        )
    return _UNITS_MAP[name]


@dataclass
class RegisteredPoint:
    """Everything the rest of the app needs to know about one live point."""

    group_id: str
    config: PointConfig
    global_instance: int
    bacnet_object: Any


class PointRegistry:
    """
    Holds every live BACnet object for the entire simulated fleet, built
    from every equipment group's config, all destined for one BACnet
    device. Keys are `"{group_id}.{alias}"`; use `.view(group_id)` to get a
    `GroupView` that lets equipment code use bare aliases.
    """

    def __init__(self, groups: list[EquipmentGroupConfig]):
        self.groups = groups
        self._points: dict[str, RegisteredPoint] = {}
        self._reliability_failed: set[str] = set()  # keys currently flagged by _set_reliability

    def build_objects(self) -> list[Any]:
        """
        Construct the bacpypes3 objects for every point in every group.
        Returns the flat list so the transport layer can hand it to
        Application.from_object_list() alongside the one device and
        network-port objects.
        """
        # A restart rebuilds every BACnet object and priority array. Discard
        # all references and reliability bookkeeping from the previous object
        # database before registering the replacements.
        self._points.clear()
        self._reliability_failed.clear()
        objects = []
        for group in self.groups:
            for point in group.points:
                global_instance = group.instance_offset + point.object_instance
                obj = self._build_object(point, global_instance)
                key = f"{group.group_id}.{point.alias}"
                self._points[key] = RegisteredPoint(
                    group_id=group.group_id, config=point, global_instance=global_instance, bacnet_object=obj
                )
                objects.append(obj)
        logger.info(
            "Built %d BACnet objects across %d equipment groups for the supervisory device",
            len(objects),
            len(self.groups),
        )
        return objects

    def _build_object(self, point: PointConfig, global_instance: int):
        common_kwargs = dict(
            objectIdentifier=(point.object_type.value, global_instance),
            objectName=point.object_name,
            description=point.description,
            statusFlags=[0, 0, 0, 0],
            outOfService=False,
        )
        analog_kwargs = {
            **({"minPresValue": point.minimum} if point.minimum is not None else {}),
            **({"maxPresValue": point.maximum} if point.maximum is not None else {}),
        }

        if point.object_type == ObjectType.analog_input:
            return AnalogInputObject(
                presentValue=point.initial_value,
                units=_units(point.units),
                covIncrement=point.cov_increment if point.cov_increment is not None else 0.1,
                **analog_kwargs,
                **common_kwargs,
            )

        if point.object_type == ObjectType.analog_output:
            return AnalogOutputObject(
                presentValue=point.initial_value,
                units=_units(point.units),
                covIncrement=point.cov_increment if point.cov_increment is not None else 0.1,
                relinquishDefault=point.relinquish_default if point.relinquish_default is not None else 0.0,
                **analog_kwargs,
                **common_kwargs,
            )

        if point.object_type == ObjectType.analog_value:
            if point.commandable:
                return AnalogValueObjectCmd(
                    presentValue=point.initial_value,
                    units=_units(point.units),
                    covIncrement=point.cov_increment if point.cov_increment is not None else 0.1,
                    relinquishDefault=point.relinquish_default if point.relinquish_default is not None else 0.0,
                    **analog_kwargs,
                    **common_kwargs,
                )
            return AnalogValueObject(
                presentValue=point.initial_value,
                units=_units(point.units),
                covIncrement=point.cov_increment if point.cov_increment is not None else 0.1,
                **analog_kwargs,
                **common_kwargs,
            )

        if point.object_type == ObjectType.binary_input:
            return BinaryInputObject(
                presentValue="active" if point.initial_value else "inactive",
                **common_kwargs,
            )

        if point.object_type == ObjectType.binary_output:
            binary_default = (
                point.relinquish_default
                if point.relinquish_default is not None
                else 0.0
            )
            return BinaryOutputObject(
                presentValue="active" if point.initial_value else "inactive",
                relinquishDefault=(
                    "active" if binary_default else "inactive"
                ),
                **common_kwargs,
            )

        if point.object_type == ObjectType.binary_value:
            if point.commandable:
                binary_default = (
                    point.relinquish_default
                    if point.relinquish_default is not None
                    else 0.0
                )
                return BinaryValueObjectCmd(
                    presentValue="active" if point.initial_value else "inactive",
                    relinquishDefault=(
                        "active" if binary_default else "inactive"
                    ),
                    **common_kwargs,
                )
            return BinaryValueObject(
                presentValue="active" if point.initial_value else "inactive",
                **common_kwargs,
            )

        raise NotImplementedError(
            f"object type '{point.object_type}' not yet supported in registry._build_object "
            f"(multi-state types are planned but not needed by any current point yet)"
        )

    # ---- internal, key-based API (used by GroupView and the API layer) ---

    def _get(self, key: str) -> float:
        rp = self._points[key]
        pv = rp.bacnet_object.presentValue
        if isinstance(pv, str):
            return pv == "active"
        return float(pv)

    def _set(self, key: str, value: float) -> None:
        rp = self._points[key]
        obj = rp.bacnet_object
        if rp.config.object_type in (ObjectType.binary_input, ObjectType.binary_value, ObjectType.binary_output):
            obj.presentValue = "active" if value else "inactive"
        else:
            obj.presentValue = float(value)

    def _set_reliability(self, key: str, failed: bool) -> None:
        """
        Reflect a reliability_fail fault on the actual BACnet object, so
        WebCTRL sees Reliability = no-sensor and the statusFlags FAULT bit --
        a real failed sensor is FLAGGED unreliable, not just silently wrong.
        Transition-guarded so the object properties are only written when the
        state actually changes (property writes feed COV detection).
        """
        currently_failed = key in self._reliability_failed
        if failed == currently_failed:
            return
        rp = self._points[key]
        obj = rp.bacnet_object
        if failed:
            self._reliability_failed.add(key)
            obj.reliability = Reliability("noSensor")
            obj.statusFlags = StatusFlags([0, 1, 0, 0])  # in-alarm, FAULT, overridden, out-of-service
        else:
            self._reliability_failed.discard(key)
            obj.reliability = Reliability("noFaultDetected")
            obj.statusFlags = StatusFlags([0, 0, 0, 0])
        logger.info("Reliability %s for %s", "FAULT (no-sensor)" if failed else "restored", key)

    def _get_commanded(self, key: str) -> Optional[float]:
        rp = self._points[key]
        pv = rp.bacnet_object.presentValue
        if pv is None:
            return None
        if isinstance(pv, str):
            return 1.0 if pv == "active" else 0.0
        return float(pv)

    def all_points(self) -> dict[str, RegisteredPoint]:
        """Every point across all groups, keyed by 'group_id.alias'."""
        return dict(self._points)

    def synchronize_reliability(self, fault_manager) -> None:
        """Make BACnet Reliability/statusFlags match the current fault set.

        Equipment publishing normally performs this reconciliation. Lifecycle
        actions such as STOP ALL may intentionally halt the engine, so they
        call this method after clearing faults to avoid leaving a stopped
        BACnet object visibly faulted.
        """
        from app.faults import FaultType

        for key, registered in self._points.items():
            self._set_reliability(
                key,
                fault_manager.has_point_fault(
                    registered.group_id,
                    registered.config.alias,
                    FaultType.reliability_fail,
                ),
            )

    async def reset_runtime_state(self) -> None:
        """Clear simulator-owned flags without disturbing the BACnet session.

        WebCTRL owns commands written into BACnet priority arrays.  Releasing
        all sixteen priorities here used to erase those live commands and
        generated a burst of COV notifications across the entire object
        database.  On the bench that burst could leave bacpypes3 servicing
        timed-out confirmed COV transactions while the UDP socket remained
        bound but stopped answering discovery.

        Scenario/instructor priority writes are released separately by
        ``ScenarioEngine.drain_priority_writes()``.  Preserve every other
        present value and priority slot; the freshly rebuilt equipment models
        will resume updating simulator-owned values on their normal ticks.
        """
        for key, registered in self._points.items():
            obj = registered.bacnet_object
            obj.outOfService = False
            self._set_reliability(key, False)

    def points_for_group(self, group_id: str) -> dict[str, RegisteredPoint]:
        prefix = f"{group_id}."
        return {k[len(prefix):]: v for k, v in self._points.items() if k.startswith(prefix)}

    def view(self, group_id: str, fault_manager=None) -> "GroupView":
        return GroupView(self, group_id, fault_manager=fault_manager)


class GroupView:
    """
    Thin wrapper scoping a PointRegistry to one equipment group, so
    equipment models can keep using bare aliases ("airflow", not
    "ACI-SIM-VAV-1.airflow") exactly as they did before every group merged
    into one BACnet device. This is the only reason the Phase 3 equipment
    models needed zero code changes for that refactor.

    Phase 4: also the only place fault injection is applied (`set()` and
    `get_commanded()`), for the same reason -- equipment models never need
    to know a fault exists. Pass `fault_manager=None` (the default) to get
    the old fault-free behavior unchanged, which is what every existing
    test still does.
    """

    def __init__(self, registry: PointRegistry, group_id: str, fault_manager=None):
        self._registry = registry
        self._group_id = group_id
        self._fault_manager = fault_manager

    def _key(self, alias: str) -> str:
        return f"{self._group_id}.{alias}"

    def get(self, alias: str) -> float:
        return self._registry._get(self._key(alias))

    def set(self, alias: str, value: float, source: str = "simulation-engine") -> None:
        if self._fault_manager is not None:
            from app.faults import FaultType

            value = self._fault_manager.apply_to_output(self._group_id, alias, value)
            self._registry._set_reliability(
                self._key(alias),
                self._fault_manager.has_point_fault(self._group_id, alias, FaultType.reliability_fail),
            )
        self._registry._set(self._key(alias), value)

    def get_commanded(self, alias: str) -> Optional[float]:
        commanded = self._registry._get_commanded(self._key(alias))
        if self._fault_manager is not None:
            commanded = self._fault_manager.apply_to_input(self._group_id, alias, commanded)
        return commanded

    def has_point_fault(self, alias: str, fault_type) -> bool:
        """Return whether a specific injected mechanic targets this point."""
        if self._fault_manager is None:
            return False
        return self._fault_manager.has_point_fault(
            self._group_id,
            alias,
            fault_type,
        )

    def point_fault_parameters(self, alias: str, fault_type):
        """Return active fault parameters for equipment-level physics."""
        if self._fault_manager is None:
            return None
        return self._fault_manager.point_fault_parameters(
            self._group_id,
            alias,
            fault_type,
        )

    def all_points(self) -> dict[str, RegisteredPoint]:
        return self._registry.points_for_group(self._group_id)

    def point_config(self, alias: str) -> PointConfig:
        return self._registry.all_points()[self._key(alias)].config

    def group_config(self) -> EquipmentGroupConfig:
        """Return this view's source configuration for model-parameter wiring."""
        return next(
            group
            for group in self._registry.groups
            if group.group_id == self._group_id
        )
