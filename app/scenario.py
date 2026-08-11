"""
Scenario Engine (Phase 4).

A scenario is initial conditions plus a timeline of events, executed
against simulated time (SimulationEngine.simulated_seconds_elapsed), not
wall-clock time -- so a scenario behaves identically whether run at 1x or
60x simulation speed.

"Force Value" / "Release Value" (the Instructor Panel feature) is
implemented as the exact same mechanism as the `stuck_value` fault type in
faults.py, just under a well-known fault_id prefix ("force:") so the UI can
tell manual instructor forces apart from scenario-authored faults. There's
no separate forcing subsystem -- one mechanism, two front doors.

Completion criteria and student objectives are informational text shown to
the instructor, not auto-evaluated against simulated state -- honestly
scoped rather than pretending to grade the student's response.
"""
from __future__ import annotations

import asyncio
import glob
import json
import logging
import re
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.faults import FaultManager, FaultType

logger = logging.getLogger("aci_sim.scenario")

FORCE_ID_PREFIX = "force:"
# These are deliberate equipment-model fault hooks rather than BACnet points.
# They let a lesson distinguish physical actuator/safety behavior from a bad
# displayed point without expanding the deployed object catalog.
VIRTUAL_FAULT_TARGETS = {
    ("ACI-SIM-AHU-1", "economizer_damper_feedback"),
    ("ACI-SIM-AHU-1", "automatic_high_static_trip"),
    ("ACI-SIM-AHU-1", "automatic_freezestat_trip"),
}


class ScenarioEvent(BaseModel):
    time_seconds: float = Field(ge=0.0)
    action: Literal["set_fault", "clear_fault", "set_value", "release_value", "set_weather"]
    equipment: Optional[str] = None  # group_id
    alias: Optional[str] = None
    fault: Optional[str] = None  # FaultType name, for set_fault
    value: Optional[Any] = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    description: str = ""

    @model_validator(mode="after")
    def validate_action_contract(self):
        is_fault = self.action in ("set_fault", "clear_fault")
        if is_fault:
            if not self.fault:
                raise ValueError(f"{self.action} requires fault")
            try:
                fault_type = FaultType(self.fault)
            except ValueError as exc:
                raise ValueError(f"unknown fault type '{self.fault}'") from exc
            is_transport = fault_type in {
                FaultType.device_offline,
                FaultType.slow_response,
                FaultType.write_rejected,
                FaultType.intermittent_comm,
            }
            if is_transport and (self.equipment is not None or self.alias is not None):
                raise ValueError(f"{fault_type.value} is device-wide and cannot name a point")
            if not is_transport and (not self.equipment or not self.alias):
                raise ValueError(f"{fault_type.value} requires equipment and alias")
            required = (
                {
                    FaultType.offset: "offset",
                    FaultType.drift: "rate_per_second",
                    FaultType.forced_status: "value",
                }.get(fault_type)
                if self.action == "set_fault"
                else None
            )
            supplied = {**self.parameters, **({"value": self.value} if self.value is not None else {})}
            if required and required not in supplied:
                raise ValueError(f"{fault_type.value} requires parameter '{required}'")
        elif self.action in ("set_value", "release_value"):
            if not self.equipment or not self.alias:
                raise ValueError(f"{self.action} requires equipment and alias")
            if self.action == "set_value" and self.value is None:
                raise ValueError("set_value requires value")
        elif self.action == "set_weather":
            allowed = {"outside_air_temperature", "outside_air_humidity"}
            if not (allowed & self.parameters.keys()):
                raise ValueError("set_weather requires an outside-air temperature or humidity parameter")
            unknown = set(self.parameters) - allowed
            if unknown:
                raise ValueError(f"set_weather has unknown parameters: {', '.join(sorted(unknown))}")
        return self


class Scenario(BaseModel):
    scenario_id: str
    title: str
    description: str = ""
    initial_conditions: dict[str, Any] = Field(default_factory=dict)
    events: list[ScenarioEvent] = Field(default_factory=list)
    expected_results: list[str] = Field(default_factory=list)
    completion_criteria: list[str] = Field(default_factory=list)
    instructor_notes: str = ""
    student_objectives: list[str] = Field(default_factory=list)
    difficulty: Literal["introductory", "intermediate", "advanced"] = "intermediate"
    recommended_speed: float = Field(default=1.0, ge=0.1, le=60.0)
    observation_points: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    @field_validator("scenario_id")
    @classmethod
    def validate_scenario_id(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", value):
            raise ValueError("scenario_id must contain only lowercase letters, numbers, '_' or '-'")
        return value

    @model_validator(mode="after")
    def validate_timeline(self):
        times = [event.time_seconds for event in self.events]
        if times != sorted(times):
            raise ValueError("scenario events must be ordered by nondecreasing time_seconds")
        return self

    @property
    def duration_seconds(self) -> float:
        return max((event.time_seconds for event in self.events), default=0.0)


class ScenarioRunState(BaseModel):
    scenario_id: str
    status: str  # "running" | "stopped" | "completed"
    started_at_sim_seconds: float
    fired_event_indices: list[int] = Field(default_factory=list)


class ScenarioEngine:
    def __init__(self, fault_manager: FaultManager, registry, get_sim_seconds, get_equipment):
        """
        `get_sim_seconds` and `get_equipment` are zero-arg callables rather
        than direct references, since ScenarioEngine is constructed before
        the SimulationEngine's equipment list is populated (see main.py's
        two-phase startup -- bacpypes3 objects need a running event loop,
        so equipment gets built inside the lifespan handler, after this).

        `registry` (the shared PointRegistry) IS available at construction
        time -- only its objects are built later -- and is needed for
        `set_value`/`release_value`: a commandable (writable) point gets a
        REAL BACnet write into its priority array, so WebCTRL sees the
        exact same value the equipment models react to (this matters most
        for interlocks like Freezestat Trip, which WebCTRL needs to be able
        to read as tripped, not just have the AHU behave as if it were). A
        non-writable (sim_to_webctrl) point instead uses the stuck_value
        output fault, since it was never BACnet-writable to begin with.
        """
        self.fault_manager = fault_manager
        self.registry = registry
        self._get_sim_seconds = get_sim_seconds
        self._get_equipment = get_equipment
        self.scenarios: dict[str, Scenario] = {}
        self._run: Optional[ScenarioRunState] = None
        self._scenario_fault_ids: list[str] = []  # faults created by the running scenario, for clean teardown
        self._priority_overrides: set[tuple[str, str]] = set()
        self._pending_priority_writes: set[asyncio.Task] = set()
        self._saved_site_conditions: Optional[tuple[float, float]] = None

    def load_all(self, directory: Path) -> None:
        loaded: dict[str, Scenario] = {}
        for path in sorted(glob.glob(str(directory / "*.json"))):
            with open(path) as f:
                scenario = Scenario.model_validate(json.load(f))
            if scenario.scenario_id in loaded:
                raise ValueError(f"duplicate scenario_id '{scenario.scenario_id}' in {path}")
            self._validate_catalog_references(scenario)
            loaded[scenario.scenario_id] = scenario
        self.scenarios = loaded
        logger.info("Loaded %d scenarios from %s", len(self.scenarios), directory)

    def list_scenarios(self) -> list[Scenario]:
        return list(self.scenarios.values())

    def register_scenario(self, scenario: Scenario, persist_to: Optional[Path] = None) -> None:
        """
        Registers a scenario built at runtime (e.g. by the Phase 6a LLM
        orchestration layer) so it can be started like any shipped
        scenario. Session-only by default -- gone on restart, matching
        Phase 6a's "don't persist anything structural without a deliberate
        decision" stance (see HANDOFF.md, Phase 6 §7, open question #2,
        which is about equipment persistence specifically but the same
        caution applies here until there's a real reason to write back to
        disk). Pass `persist_to` (a directory) to also write it to
        config/scenarios/ if durability across restarts is wanted.
        """
        if scenario.scenario_id in self.scenarios:
            raise ValueError(f"scenario_id '{scenario.scenario_id}' already exists")
        self._validate_catalog_references(scenario)
        self.scenarios[scenario.scenario_id] = scenario
        logger.info("Registered runtime scenario: %s (%s)", scenario.scenario_id, scenario.title)
        if persist_to is not None:
            path = persist_to / f"{scenario.scenario_id}.json"
            with open(path, "w") as f:
                f.write(scenario.model_dump_json(indent=2))
            logger.info("Persisted runtime scenario to %s", path)

    def _find_equipment(self, group_id: str):
        for eq in self._get_equipment():
            if eq.equipment_id == group_id:
                return eq
        return None

    def start(self, scenario_id: str) -> ScenarioRunState:
        if scenario_id not in self.scenarios:
            raise KeyError(f"unknown scenario_id '{scenario_id}'")
        self.stop()  # clear anything currently running first

        scenario = self.scenarios[scenario_id]
        site = self._find_equipment("ACI-SIM-SITE")
        if site is not None:
            self._saved_site_conditions = (
                float(site.target_oa_temp_f),
                float(site.target_oa_humidity_pct),
            )
        self._run = ScenarioRunState(
            scenario_id=scenario_id, status="running", started_at_sim_seconds=self._get_sim_seconds()
        )
        self._apply_initial_conditions(scenario)
        logger.info("Scenario started: %s (%s)", scenario_id, scenario.title)
        return self._run

    def stop(self) -> None:
        # Scenario/instructor writes to commandable points live in the
        # BACnet priority array, not FaultManager. Relinquish every tracked
        # priority-3 slot so Stop/Reset cannot silently outrank WebCTRL.
        for group_id, alias in tuple(self._priority_overrides):
            self._apply_force_or_release(group_id, alias, "release_value", None)
        for fault_id in self._scenario_fault_ids:
            self.fault_manager.clear_fault(fault_id)
        self._scenario_fault_ids.clear()
        if self._saved_site_conditions is not None:
            site = self._find_equipment("ACI-SIM-SITE")
            if site is not None:
                site.target_oa_temp_f, site.target_oa_humidity_pct = self._saved_site_conditions
            self._saved_site_conditions = None
        if self._run is not None:
            logger.info("Scenario stopped: %s", self._run.scenario_id)
        self._run = None

    def reset(self) -> None:
        """Stop the current scenario and clear ALL faults, including manual instructor forces."""
        self.stop()
        self.fault_manager.clear_all()

    async def drain_priority_writes(self) -> None:
        """Wait until scheduled instructor priority writes have completed."""
        while self._pending_priority_writes:
            pending = tuple(self._pending_priority_writes)
            await asyncio.gather(*pending, return_exceptions=True)

    def is_priority_forced(self, group_id: str, alias: str) -> bool:
        """True when this process owns the point's instructor P3 slot."""
        return (group_id, alias) in self._priority_overrides

    @property
    def active_priority_override_count(self) -> int:
        return len(self._priority_overrides)

    def _apply_initial_conditions(self, scenario: Scenario) -> None:
        ic = scenario.initial_conditions
        site = self._find_equipment("ACI-SIM-SITE")
        if site is not None:
            if "outside_air_temperature" in ic:
                site.target_oa_temp_f = float(ic["outside_air_temperature"])
            if "outside_air_humidity" in ic:
                site.target_oa_humidity_pct = float(ic["outside_air_humidity"])
        recognized = {"outside_air_temperature", "outside_air_humidity"}
        for key in ic:
            if key not in recognized:
                logger.warning(
                    "Scenario '%s' initial_conditions key '%s' is not wired to any equipment yet "
                    "(occupancy modeling is not implemented) -- stored nowhere, informational only",
                    scenario.scenario_id, key,
                )

    def tick(self, dt_seconds: float) -> None:
        if self._run is None or self._run.status != "running":
            return
        scenario = self.scenarios[self._run.scenario_id]
        # The engine advances its public clock after each bounded physics
        # substep, so this observes 0, 1, 2... even during a 60x wall tick.
        elapsed = self._get_sim_seconds() - self._run.started_at_sim_seconds

        for index, event in enumerate(scenario.events):
            if index in self._run.fired_event_indices:
                continue
            if elapsed >= event.time_seconds:
                self._fire_event(scenario, event)
                self._run.fired_event_indices.append(index)

        if len(self._run.fired_event_indices) == len(scenario.events) and scenario.events:
            self._run.status = "completed"
            logger.info("Scenario completed (all events fired): %s", scenario.scenario_id)

    def _configured_points(self) -> dict[str, Any]:
        live = self.registry.all_points()
        if live:
            return live
        return {
            f"{group.group_id}.{point.alias}": type("ConfiguredPoint", (), {"config": point})()
            for group in getattr(self.registry, "groups", [])
            for point in group.points
        }

    def _validate_catalog_references(self, scenario: Scenario) -> None:
        points = self._configured_points()
        for index, event in enumerate(scenario.events):
            if event.action == "set_weather":
                continue
            if event.action in ("set_fault", "clear_fault"):
                fault_type = FaultType(event.fault)
                if fault_type in {
                    FaultType.device_offline,
                    FaultType.slow_response,
                    FaultType.write_rejected,
                    FaultType.intermittent_comm,
                }:
                    continue
            key = f"{event.equipment}.{event.alias}"
            if key not in points and (event.equipment, event.alias) not in VIRTUAL_FAULT_TARGETS:
                raise ValueError(f"scenario '{scenario.scenario_id}' event {index} references unknown point '{key}'")
            if event.action in ("set_value", "release_value"):
                config = points[key].config
                if not config.writable:
                    raise ValueError(f"scenario '{scenario.scenario_id}' event {index} cannot write read-only point '{key}'")
                if event.action == "set_value" and not isinstance(event.value, bool):
                    try:
                        numeric = float(event.value)
                    except (TypeError, ValueError) as exc:
                        raise ValueError(f"scenario '{scenario.scenario_id}' event {index} requires a numeric value") from exc
                    if config.minimum is not None and numeric < config.minimum:
                        raise ValueError(f"scenario '{scenario.scenario_id}' event {index} is below '{key}' minimum")
                    if config.maximum is not None and numeric > config.maximum:
                        raise ValueError(f"scenario '{scenario.scenario_id}' event {index} is above '{key}' maximum")
        for key in scenario.observation_points:
            if key not in points and tuple(key.split(".", 1)) not in VIRTUAL_FAULT_TARGETS:
                raise ValueError(f"scenario '{scenario.scenario_id}' observes unknown point '{key}'")

    def _fire_event(self, scenario: Scenario, event: ScenarioEvent) -> None:
        logger.info(
            "Scenario '%s' event fired at t+%.0fs: %s%s",
            scenario.scenario_id, event.time_seconds, event.action,
            f" ({event.description})" if event.description else "",
        )
        if event.action == "set_fault":
            fault_id = f"scenario:{scenario.scenario_id}:{event.equipment}.{event.alias}.{event.fault}"
            self.fault_manager.set_fault(
                fault_id=fault_id,
                fault_type=FaultType(event.fault),
                group_id=event.equipment,
                alias=event.alias,
                parameters={**event.parameters, **({"value": event.value} if event.value is not None else {})},
            )
            self._scenario_fault_ids.append(fault_id)
        elif event.action == "clear_fault":
            fault_id = f"scenario:{scenario.scenario_id}:{event.equipment}.{event.alias}.{event.fault}"
            self.fault_manager.clear_fault(fault_id)
        elif event.action == "set_weather":
            site = self._find_equipment("ACI-SIM-SITE")
            if site is not None:
                if "outside_air_temperature" in event.parameters:
                    site.target_oa_temp_f = float(event.parameters["outside_air_temperature"])
                if "outside_air_humidity" in event.parameters:
                    site.target_oa_humidity_pct = float(event.parameters["outside_air_humidity"])
        elif event.action in ("set_value", "release_value"):
            self._apply_force_or_release(event.equipment, event.alias, event.action, event.value)
        else:
            logger.warning("Scenario '%s' event has unknown action '%s' -- ignored", scenario.scenario_id, event.action)

    def _apply_force_or_release(self, group_id: str, alias: str, action: str, value: Any) -> bool:
        """
        Shared by scenario set_value/release_value events AND the manual
        Instructor Panel Force/Release API -- see module docstring for why
        a commandable point gets a real BACnet write while a non-writable
        point gets a stuck_value output fault instead.
        """
        try:
            point_config = self.registry.all_points()[f"{group_id}.{alias}"].config
        except KeyError:
            logger.warning("Force/release target '%s.%s' does not exist -- ignored", group_id, alias)
            return False

        if point_config.writable:
            is_set = action == "set_value"
            from app.config_models import ObjectType

            is_binary = point_config.object_type in (
                ObjectType.binary_input,
                ObjectType.binary_output,
                ObjectType.binary_value,
            )
            if is_set and not is_binary:
                try:
                    numeric_value = float(value)
                except (TypeError, ValueError):
                    logger.warning(
                        "Force target '%s.%s' requires a numeric value -- ignored",
                        group_id,
                        alias,
                    )
                    return False
                if (
                    point_config.minimum is not None
                    and numeric_value < point_config.minimum
                ) or (
                    point_config.maximum is not None
                    and numeric_value > point_config.maximum
                ):
                    logger.warning(
                        "Force target '%s.%s' value %s is outside [%s, %s] -- ignored",
                        group_id,
                        alias,
                        numeric_value,
                        point_config.minimum,
                        point_config.maximum,
                    )
                    return False

            obj = self.registry.all_points()[f"{group_id}.{alias}"].bacnet_object
            priority = 3  # "instructor override" -- below life-safety (1-2), above typical operator/schedule levels

            from bacpypes3.basetypes import BinaryPV
            from bacpypes3.primitivedata import Null, Real

            target = (group_id, alias)
            if action == "set_value":
                # Track the requested write before it runs. This makes an
                # immediate Stop/Reset schedule a later relinquish even if
                # the set task has not reached the object yet.
                self._priority_overrides.add(target)

            async def _write():
                try:
                    if action == "set_value":
                        # Binary priority arrays only accept typed values --
                        # a raw "active" string raises TypeError inside
                        # PriorityValue (this silently broke every scenario
                        # set_value on a binary point until caught live).
                        if is_binary:
                            cast_value = BinaryPV("active" if value in (True, 1, 1.0, "active") else "inactive")
                        else:
                            cast_value = Real(float(value))
                        await obj.write_property("presentValue", cast_value, priority=priority)
                    else:
                        await obj.write_property(
                            "presentValue",
                            Null(()),
                            priority=priority,
                        )  # relinquish
                        self._priority_overrides.discard(target)
                except Exception:  # noqa: BLE001 - scenario authoring error shouldn't crash the sim loop
                    if action == "set_value":
                        self._priority_overrides.discard(target)
                    logger.exception("Force/release write failed for %s.%s", group_id, alias)

            task = asyncio.create_task(_write())
            self._pending_priority_writes.add(task)
            task.add_done_callback(self._pending_priority_writes.discard)
            return True
        else:
            fault_id = f"{FORCE_ID_PREFIX}{group_id}.{alias}"
            if action == "set_value":
                self.fault_manager.set_fault(
                    fault_id=fault_id, fault_type=FaultType.stuck_value,
                    group_id=group_id, alias=alias, parameters={"value": value},
                )
                self._scenario_fault_ids.append(fault_id)
            else:
                self.fault_manager.clear_fault(fault_id)
            return True

    def status(self) -> dict:
        if self._run is None:
            return {"running": False}
        scenario = self.scenarios[self._run.scenario_id]
        elapsed = self._get_sim_seconds() - self._run.started_at_sim_seconds
        next_event = next(
            (e for i, e in enumerate(scenario.events) if i not in self._run.fired_event_indices), None
        )
        return {
            "running": self._run.status == "running",
            "effects_active": True,
            "status": self._run.status,
            "scenario_id": scenario.scenario_id,
            "title": scenario.title,
            "elapsed_seconds": round(elapsed, 1),
            "events_fired": len(self._run.fired_event_indices),
            "events_total": len(scenario.events),
            "next_event": (
                {"time_seconds": next_event.time_seconds, "description": next_event.description}
                if next_event else None
            ),
            "expected_results": scenario.expected_results,
            "completion_criteria": scenario.completion_criteria,
            "recommended_speed": scenario.recommended_speed,
            "duration_seconds": scenario.duration_seconds,
            "observation_points": scenario.observation_points,
            "cleanup_required": True,
        }
