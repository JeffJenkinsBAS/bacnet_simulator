"""Source-backed diagnostics for the command-center building view.

The monitor intentionally uses monotonic wall-clock time. Simulation speed
may be accelerated for a lesson, but a command/status mismatch must persist
for fifteen real seconds before the command center calls it a failure.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable

from app.registry import PointRegistry


FAILURE_DELAY_SECONDS = 15.0
AIRFLOW_TOLERANCE_FRACTION = 0.25
AIRFLOW_IDLE_SETPOINT_CFM = 1.0
_DIAGNOSTIC_TYPES = {"binary_command_status", "vav_airflow"}


class CommandCenterDiagnostics:
    """Evaluate live registry values against the configured building layout."""

    def __init__(
        self,
        registry: PointRegistry,
        layout: dict[str, Any],
        *,
        failure_delay_seconds: float = FAILURE_DELAY_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        equipment_provider: Callable[[], list[Any]] | None = None,
    ):
        if failure_delay_seconds <= 0:
            raise ValueError("failure_delay_seconds must be greater than zero")
        self.registry = registry
        self.layout = layout
        self.failure_delay_seconds = float(failure_delay_seconds)
        self._clock = clock
        self._equipment_provider = equipment_provider
        self._mismatch_started: dict[str, float] = {}
        self._latest_locations: list[dict[str, Any]] = []
        self._validate_layout()

    def set_equipment_provider(self, provider: Callable[[], list[Any]]) -> None:
        self._equipment_provider = provider

    def _equipment_by_id(self) -> dict[str, Any]:
        if self._equipment_provider is None:
            return {}
        return {
            equipment.equipment_id: equipment
            for equipment in self._equipment_provider()
            if getattr(equipment, "equipment_id", None)
        }

    def _configured_point_keys(self) -> set[str]:
        live_points = self.registry.all_points()
        if live_points:
            return set(live_points)
        return {
            f"{group.group_id}.{point.alias}"
            for group in getattr(self.registry, "groups", [])
            for point in group.points
        }

    def _validate_layout(self) -> None:
        building = self.layout.get("building")
        locations = self.layout.get("locations")
        if not isinstance(building, dict) or not isinstance(locations, list):
            raise ValueError("building layout requires a building object and locations list")

        pressure = building.get("pressure")
        if not isinstance(pressure, dict) or not pressure.get("group_id") or not pressure.get("alias"):
            raise ValueError("building layout requires a pressure group_id and alias")

        point_keys = self._configured_point_keys()
        referenced_keys = {f"{pressure['group_id']}.{pressure['alias']}"}
        location_ids: set[str] = set()
        for location in locations:
            location_id = location.get("id")
            if not location_id or location_id in location_ids:
                raise ValueError(f"building layout has a missing or duplicate location id: {location_id!r}")
            location_ids.add(location_id)
            for coordinate in ("x", "y"):
                value = location.get(coordinate)
                if not isinstance(value, (int, float)) or not 0 <= value <= 100:
                    raise ValueError(f"location '{location_id}' {coordinate} must be between 0 and 100")
            space = location.get("space")
            if space is not None:
                if not isinstance(space, dict):
                    raise ValueError(f"location '{location_id}' space must be an object")
                for dimension in ("width", "height"):
                    value = space.get(dimension)
                    if not isinstance(value, (int, float)) or not 1 <= value <= 40:
                        raise ValueError(
                            f"location '{location_id}' space {dimension} must be between 1 and 40"
                        )

            diagnostic = location.get("diagnostic", {})
            diagnostic_type = diagnostic.get("type")
            if diagnostic_type not in _DIAGNOSTIC_TYPES:
                raise ValueError(f"location '{location_id}' has unknown diagnostic type {diagnostic_type!r}")
            group_id = location.get("group_id")
            if diagnostic_type == "binary_command_status":
                aliases = (diagnostic.get("command_alias"), diagnostic.get("status_alias"))
            else:
                aliases = (diagnostic.get("setpoint_alias"), diagnostic.get("airflow_alias"))
            if not group_id or not all(aliases):
                raise ValueError(f"location '{location_id}' has incomplete diagnostic sources")
            referenced_keys.update(f"{group_id}.{alias}" for alias in aliases)

        if point_keys:
            missing = sorted(referenced_keys - point_keys)
            if missing:
                raise ValueError(f"building layout references unknown points: {', '.join(missing)}")

    def _get(self, group_id: str, alias: str) -> float:
        return float(self.registry._get(f"{group_id}.{alias}"))

    def _timed_state(
        self,
        location_id: str,
        *,
        mismatch: bool,
        normal_state: str,
        now: float,
    ) -> tuple[str, float]:
        if not mismatch:
            self._mismatch_started.pop(location_id, None)
            return normal_state, 0.0
        started = self._mismatch_started.setdefault(location_id, now)
        elapsed = max(0.0, now - started)
        state = "failure" if elapsed >= self.failure_delay_seconds else "tracking"
        return state, elapsed

    def _evaluate_binary(self, location: dict[str, Any], now: float) -> dict[str, Any]:
        diagnostic = location["diagnostic"]
        group_id = location["group_id"]
        command_alias = diagnostic["command_alias"]
        status_alias = diagnostic["status_alias"]
        command = self._get(group_id, command_alias) >= 0.5
        status = self._get(group_id, status_alias) >= 0.5

        if not command:
            state, mismatch_seconds = self._timed_state(
                location["id"], mismatch=False, normal_state="idle", now=now
            )
            message = "Command is off."
        else:
            state, mismatch_seconds = self._timed_state(
                location["id"], mismatch=not status, normal_state="running", now=now
            )
            if status:
                message = "Command and run status agree."
            elif state == "failure":
                message = (
                    f"Command is on but status remained off for "
                    f"{mismatch_seconds:.1f} real seconds."
                )
            else:
                message = (
                    f"Waiting for run proof ({mismatch_seconds:.1f}/"
                    f"{self.failure_delay_seconds:.0f} real seconds)."
                )

        values = {
            "command": command,
            "status": status,
            "airflow": None,
            "airflow_setpoint": None,
        }
        sources = {
            "command": f"{group_id}.{command_alias}",
            "status": f"{group_id}.{status_alias}",
        }
        diagnostic_type = location["diagnostic"]["type"]

        equipment = self._equipment_by_id().get(group_id)
        equipment_snapshot = (
            equipment.operating_snapshot()
            if equipment is not None and hasattr(equipment, "operating_snapshot")
            else None
        )
        if location.get("component_type") == "ahu" and equipment_snapshot is not None:
            overlap_timer_key = f"{location['id']}:simultaneous-heating-cooling"
            simultaneous = bool(
                equipment_snapshot.get("simultaneous_heating_cooling", False)
            )
            overlap_state, overlap_seconds = self._timed_state(
                overlap_timer_key,
                mismatch=simultaneous,
                normal_state=state,
                now=now,
            )
            values.update(
                {
                    "cooling_valve_command_pct": equipment_snapshot.get(
                        "cooling_valve_command_pct"
                    ),
                    "heating_valve_command_pct": equipment_snapshot.get(
                        "heating_valve_command_pct"
                    ),
                    "cooling_valve_effective_pct": equipment_snapshot.get(
                        "cooling_valve_effective_pct"
                    ),
                    "heating_valve_effective_pct": equipment_snapshot.get(
                        "heating_valve_effective_pct"
                    ),
                    "supply_air_temp_f": equipment_snapshot.get(
                        "supply_air_temp_f"
                    ),
                    "supply_air_temp_setpoint_f": equipment_snapshot.get(
                        "supply_air_temp_setpoint_f"
                    ),
                    "valve_overlap_pct": equipment_snapshot.get(
                        "valve_overlap_pct"
                    ),
                    "valve_changeover_active": equipment_snapshot.get(
                        "valve_changeover_active"
                    ),
                }
            )
            sources.update(
                {
                    "cooling_valve_command_pct": f"{group_id}.cooling_valve",
                    "heating_valve_command_pct": f"{group_id}.heating_valve",
                    "supply_air_temp_f": f"{group_id}.ahu_sa_temp",
                    "supply_air_temp_setpoint_f": f"{group_id}.sa_temp_setpoint",
                }
            )

            if simultaneous:
                diagnostic_type = "simultaneous_heating_cooling"
                if state == "failure":
                    mismatch_seconds = max(mismatch_seconds, overlap_seconds)
                    message += (
                        " Cooling and heating valve commands are also materially "
                        "open at the same time."
                    )
                else:
                    state = overlap_state
                    mismatch_seconds = overlap_seconds
                    overlap = float(equipment_snapshot.get("valve_overlap_pct", 0.0))
                    cooling = float(
                        equipment_snapshot.get("cooling_valve_command_pct", 0.0)
                    )
                    heating = float(
                        equipment_snapshot.get("heating_valve_command_pct", 0.0)
                    )
                    if overlap_state == "failure":
                        message = (
                            f"Cooling ({cooling:.1f}%) and heating ({heating:.1f}%) "
                            f"valves remained simultaneously open for "
                            f"{overlap_seconds:.1f} real seconds. The {overlap:.1f}% "
                            "overlap is wasting energy; check WebCTRL priority locks."
                        )
                    else:
                        message = (
                            f"Cooling ({cooling:.1f}%) and heating ({heating:.1f}%) "
                            "valves are simultaneously open; confirming persistence "
                            f"({overlap_seconds:.1f}/{self.failure_delay_seconds:.0f} "
                            "real seconds)."
                        )
            elif equipment_snapshot.get("valve_changeover_active", False):
                message += " Heating/cooling valve changeover is in progress."

        return self._location_payload(
            location,
            state=state,
            mismatch_seconds=mismatch_seconds,
            values=values,
            sources=sources,
            message=message,
            diagnostic_type=diagnostic_type,
        )

    def _evaluate_airflow(self, location: dict[str, Any], now: float) -> dict[str, Any]:
        diagnostic = location["diagnostic"]
        group_id = location["group_id"]
        setpoint_alias = diagnostic["setpoint_alias"]
        airflow_alias = diagnostic["airflow_alias"]
        setpoint = self._get(group_id, setpoint_alias)
        airflow = self._get(group_id, airflow_alias)
        equipment = self._equipment_by_id().get(group_id)
        air_delivery = (
            equipment.operating_snapshot()
            if equipment is not None and hasattr(equipment, "operating_snapshot")
            else None
        )
        if air_delivery is not None:
            # Keep the physical/effective shaft position in ``damper_pct``, but
            # source the operator-facing AV:85 feedback from the registry. This
            # preserves the visible effect of a sensor/output fault applied to
            # the published feedback point.
            air_delivery = dict(air_delivery)
            feedback_key = f"{group_id}.damper_position_feedback"
            if feedback_key in self.registry.all_points():
                air_delivery["damper_position_feedback_pct"] = self._get(
                    group_id,
                    "damper_position_feedback",
                )

        if setpoint <= AIRFLOW_IDLE_SETPOINT_CFM:
            state, mismatch_seconds = self._timed_state(
                location["id"], mismatch=False, normal_state="idle", now=now
            )
            message = "Airflow setpoint is idle."
        elif (
            air_delivery is not None
            and not air_delivery.get("dependencies", {}).get("ahu_proven", True)
        ):
            state, mismatch_seconds = self._timed_state(
                location["id"], mismatch=False, normal_state="inhibited", now=now
            )
            message = "Airflow request is inhibited because AHU supply-air proof is unavailable."
        else:
            lower = setpoint * (1.0 - AIRFLOW_TOLERANCE_FRACTION)
            upper = setpoint * (1.0 + AIRFLOW_TOLERANCE_FRACTION)
            mismatch = airflow < lower or airflow > upper
            state, mismatch_seconds = self._timed_state(
                location["id"], mismatch=mismatch, normal_state="running", now=now
            )
            if not mismatch:
                message = f"Airflow is within the expected {lower:.0f}-{upper:.0f} cfm band."
            elif state == "failure":
                message = (
                    f"Airflow {airflow:.0f} cfm remained outside the "
                    f"{lower:.0f}-{upper:.0f} cfm band for {mismatch_seconds:.1f} real seconds."
                )
            else:
                message = (
                    f"Airflow is outside the +/-25% band "
                    f"({mismatch_seconds:.1f}/{self.failure_delay_seconds:.0f} real seconds)."
                )

        return self._location_payload(
            location,
            state=state,
            mismatch_seconds=mismatch_seconds,
            values={
                "command": None,
                "status": None,
                "airflow": round(airflow, 2),
                "airflow_setpoint": round(setpoint, 2),
                "heating_min_airflow": (
                    air_delivery.get("heating_minimum_airflow_cfm")
                    if air_delivery is not None
                    else None
                ),
                "heating_max_airflow": (
                    air_delivery.get("heating_maximum_airflow_cfm")
                    if air_delivery is not None
                    else None
                ),
                "cooling_min_airflow": (
                    air_delivery.get("cooling_minimum_airflow_cfm")
                    if air_delivery is not None
                    else None
                ),
                "cooling_max_airflow": (
                    air_delivery.get("cooling_maximum_airflow_cfm")
                    if air_delivery is not None
                    else None
                ),
                "damper_position": (
                    air_delivery.get("damper_pct") if air_delivery is not None else None
                ),
                "damper_position_command": (
                    air_delivery.get("damper_command_pct")
                    if air_delivery is not None
                    else None
                ),
                "damper_position_feedback": (
                    air_delivery.get("damper_position_feedback_pct")
                    if air_delivery is not None
                    else None
                ),
                "reheat_valve": (
                    air_delivery.get("reheat_valve_pct") if air_delivery is not None else None
                ),
                "discharge_temp": (
                    air_delivery.get("discharge_temp_f") if air_delivery is not None else None
                ),
                "zone_temp": (
                    air_delivery.get("zone_temp_f") if air_delivery is not None else None
                ),
                "zone_humidity": (
                    air_delivery.get("zone_humidity_pct")
                    if air_delivery is not None
                    else None
                ),
            },
            sources={
                "airflow": f"{group_id}.{airflow_alias}",
                "airflow_setpoint": f"{group_id}.{setpoint_alias}",
                "heating_min_airflow": f"{group_id}.heating_min_airflow",
                "heating_max_airflow": f"{group_id}.heating_max_airflow",
                "cooling_min_airflow": f"{group_id}.cooling_min_airflow",
                "cooling_max_airflow": f"{group_id}.cooling_max_airflow",
                "damper_position_feedback": (
                    f"{group_id}.damper_position_feedback"
                ),
            },
            message=message,
            air_delivery=air_delivery,
        )

    def _location_payload(
        self,
        location: dict[str, Any],
        *,
        state: str,
        mismatch_seconds: float,
        values: dict[str, Any],
        sources: dict[str, str],
        message: str,
        air_delivery: dict[str, Any] | None = None,
        diagnostic_type: str | None = None,
    ) -> dict[str, Any]:
        return {
            "id": location["id"],
            "label": location["label"],
            "group_id": location["group_id"],
            "component_type": location["component_type"],
            "floor": location["floor"],
            "x": location["x"],
            "y": location["y"],
            "space": location.get("space"),
            "state": state,
            "diagnostic_type": diagnostic_type or location["diagnostic"]["type"],
            "mismatch_seconds": round(mismatch_seconds, 1),
            "threshold_seconds": self.failure_delay_seconds,
            "values": values,
            "sources": sources,
            "air_delivery": air_delivery,
            "message": message,
        }

    def tick(self) -> None:
        """Refresh diagnostic states from current registry values."""
        now = self._clock()
        locations = []
        for location in self.layout["locations"]:
            if location["diagnostic"]["type"] == "binary_command_status":
                locations.append(self._evaluate_binary(location, now))
            else:
                locations.append(self._evaluate_airflow(location, now))
        self._latest_locations = locations

    def _building_payload(self) -> dict[str, Any]:
        building = self.layout["building"]
        pressure_source = building["pressure"]
        pressure_key = f"{pressure_source['group_id']}.{pressure_source['alias']}"
        value = self._get(pressure_source["group_id"], pressure_source["alias"])

        normal_low = None
        normal_high = None
        registered_point = self.registry.all_points().get(pressure_key)
        if registered_point is not None:
            normal_range = getattr(registered_point.config, "normal_range", None)
            if normal_range is not None:
                normal_low = float(normal_range.low)
                normal_high = float(normal_range.high)
        if normal_low is None or normal_high is None:
            normal_low = float(pressure_source.get("normal_low", 0.03))
            normal_high = float(pressure_source.get("normal_high", 0.10))

        if value < normal_low:
            state = "low"
        elif value > normal_high:
            state = "high"
        else:
            state = "normal"
        return {
            "name": building["name"],
            "asset": building["asset"],
            "pressure": {
                "value": round(value, 4),
                "normal_low": normal_low,
                "normal_high": normal_high,
                "state": state,
                "source": pressure_key,
            },
        }

    def _systems_payload(self) -> dict[str, Any]:
        equipment = self._equipment_by_id()
        systems: dict[str, Any] = {}
        for key, equipment_id in (
            ("chilled_water", "ACI-SIM-CHW-PLANT"),
            ("hot_water", "ACI-SIM-BOILER-MGR"),
            ("air_handler", "ACI-SIM-AHU-1"),
        ):
            model = equipment.get(equipment_id)
            if model is not None and hasattr(model, "operating_snapshot"):
                systems[key] = model.operating_snapshot()
        return systems

    def snapshot(self) -> dict[str, Any]:
        """Return the UI contract plus traceable layout/source metadata."""
        self.tick()
        summary = {
            state: sum(1 for location in self._latest_locations if location["state"] == state)
            for state in ("running", "failure", "tracking", "inhibited", "idle")
        }
        summary["failures"] = summary["failure"]
        air_summary = {
            mode: sum(
                1
                for location in self._latest_locations
                if (location.get("air_delivery") or {}).get("mode") == mode
            )
            for mode in ("cooling", "heating", "ventilation", "off")
        }
        return {
            "building": self._building_payload(),
            "failure_delay_seconds": self.failure_delay_seconds,
            "summary": summary,
            "air_summary": air_summary,
            "systems": self._systems_payload(),
            "locations": list(self._latest_locations),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "layout": {
                "schema_version": self.layout.get("schema_version", 1),
                "coordinate_system": self.layout.get("coordinate_system", "percent"),
            },
        }
