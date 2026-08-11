"""Repeatable training sessions, baselines, evidence, scoring, and roles."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from bacpypes3.basetypes import BinaryPV
from bacpypes3.primitivedata import Null, Real
from pydantic import BaseModel, Field, model_validator

from app.config_models import ObjectType


class BaselineCommand(BaseModel):
    group_id: str
    alias: str
    value: float | bool


class BaselineProfile(BaseModel):
    baseline_id: str
    version: str
    title: str
    description: str = ""
    weather: dict[str, float] = Field(default_factory=dict)
    speed_multiplier: float = Field(default=1.0, ge=0.1, le=60.0)
    settle_seconds: float = Field(default=60.0, ge=0.0, le=3600.0)
    commands: list[BaselineCommand] = Field(default_factory=list)
    destructive: bool = False


class OutcomeAssertion(BaseModel):
    assertion_id: str
    title: str
    point: str
    operator: Literal["eq", "ne", "gt", "ge", "lt", "le", "between"]
    value: float | bool | None = None
    other_point: str | None = None
    offset: float = 0.0
    upper_value: float | None = None
    tolerance: float = Field(default=0.0, ge=0.0)
    start_seconds: float = Field(default=0.0, ge=0.0)
    end_seconds: float | None = Field(default=None, ge=0.0)
    for_seconds: float = Field(default=0.0, ge=0.0)
    weight: float = Field(default=1.0, gt=0.0)

    @model_validator(mode="after")
    def validate_comparison(self):
        if self.other_point is None and self.value is None:
            raise ValueError("assertion requires value or other_point")
        if self.operator == "between" and self.upper_value is None:
            raise ValueError("between assertion requires upper_value")
        if self.end_seconds is not None and self.end_seconds < self.start_seconds:
            raise ValueError("end_seconds must be at or after start_seconds")
        return self


@dataclass
class AssertionRuntime:
    spec: OutcomeAssertion
    status: str = "pending"
    consecutive_seconds: float = 0.0
    first_passed_at: float | None = None
    last_value: float | bool | None = None
    last_reference: float | bool | None = None


@dataclass
class TrainingSession:
    run_id: str
    scenario_id: str
    baseline_id: str
    team: str
    attempt: int
    started_at_wall_time: float
    started_at_sim_seconds: float
    status: str = "running"
    stopped_at_wall_time: float | None = None
    selected_points: list[str] = field(default_factory=list)
    samples: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    assertions: list[AssertionRuntime] = field(default_factory=list)
    last_sample_sim_seconds: float | None = None
    last_external_command_key: str | None = None


class PriorityReconciliationRequired(RuntimeError):
    def __init__(self, report: dict[str, Any]):
        super().__init__("external BACnet priorities require an explicit retain or release decision")
        self.report = report


class TrainingAuth:
    """Short-lived local role tokens; instructor login requires a configured PIN."""

    def __init__(self, instructor_pin: str | None, *, required: bool = True, ttl_seconds: int = 8 * 3600):
        self.required = required
        self.ttl_seconds = ttl_seconds
        self._pin_digest = (
            hashlib.sha256(instructor_pin.encode("utf-8")).digest()
            if instructor_pin
            else None
        )
        self._tokens: dict[str, dict[str, Any]] = {}

    def login(self, role: str, pin: str | None, label: str = "") -> dict[str, Any]:
        if role not in {"student", "instructor"}:
            raise ValueError("role must be student or instructor")
        if role == "instructor":
            if self._pin_digest is None:
                raise PermissionError("instructor PIN is not configured")
            supplied = hashlib.sha256((pin or "").encode("utf-8")).digest()
            if not hmac.compare_digest(supplied, self._pin_digest):
                raise PermissionError("invalid instructor PIN")
        token = secrets.token_urlsafe(32)
        expires = time.time() + self.ttl_seconds
        self._tokens[token] = {"role": role, "label": label.strip()[:80], "expires_at": expires}
        return {"token": token, "role": role, "label": label.strip()[:80], "expires_at": expires}

    def identity(self, authorization: str | None) -> dict[str, Any] | None:
        if not self.required:
            return {"role": "instructor", "label": "legacy-local", "expires_at": None}
        if not authorization or not authorization.lower().startswith("bearer "):
            return None
        token = authorization.split(None, 1)[1]
        identity = self._tokens.get(token)
        if identity is None:
            return None
        if identity["expires_at"] < time.time():
            self._tokens.pop(token, None)
            return None
        return dict(identity)

    def logout(self, authorization: str | None) -> None:
        if authorization and authorization.lower().startswith("bearer "):
            self._tokens.pop(authorization.split(None, 1)[1], None)


class TrainingManager:
    def __init__(
        self,
        *,
        engine,
        registry,
        fault_manager,
        scenario_engine,
        equipment_factory: Callable[[], list],
        baseline_path: Path,
        outcomes_path: Path,
        auth: TrainingAuth,
        get_last_command: Callable[[], dict[str, Any] | None] | None = None,
        evidence_limit: int = 20_000,
        evidence_dir: Path | None = None,
    ):
        self.engine = engine
        self.registry = registry
        self.fault_manager = fault_manager
        self.scenario_engine = scenario_engine
        self.equipment_factory = equipment_factory
        self.auth = auth
        self.get_last_command = get_last_command or (lambda: None)
        self.evidence_limit = evidence_limit
        self.evidence_dir = evidence_dir
        self.baselines = self._load_baselines(baseline_path)
        self.outcomes = self._load_outcomes(outcomes_path)
        self.current_baseline_id: str | None = None
        self.current_baseline_version: str | None = None
        self.baseline_settled = False
        self.baseline_restored_at_wall_time: float | None = None
        self.reconciled_priority_mode: str | None = None
        self.reconciled_priority_fingerprint: str | None = None
        self._baseline_checkpoints: dict[str, dict[str, Any]] = {}
        self.sessions: dict[str, TrainingSession] = {}
        self.active_run_id: str | None = None
        self.evidence_paths: dict[str, str] = {}

    @staticmethod
    def _load_baselines(path: Path) -> dict[str, BaselineProfile]:
        data = json.loads(path.read_text(encoding="utf-8"))
        profiles = [BaselineProfile.model_validate(item) for item in data.get("baselines", [])]
        result = {profile.baseline_id: profile for profile in profiles}
        if len(result) != len(profiles):
            raise ValueError("training baseline IDs must be unique")
        return result

    @staticmethod
    def _load_outcomes(path: Path) -> dict[str, list[OutcomeAssertion]]:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            scenario_id: [OutcomeAssertion.model_validate(item) for item in items]
            for scenario_id, items in data.get("scenarios", {}).items()
        }

    def list_baselines(self) -> list[dict[str, Any]]:
        return [
            {
                **profile.model_dump(),
                "current": profile.baseline_id == self.current_baseline_id,
                "checkpoint_available": profile.baseline_id in self._baseline_checkpoints,
                "settled": self.baseline_settled if profile.baseline_id == self.current_baseline_id else False,
            }
            for profile in self.baselines.values()
        ]

    def consume_baseline(self) -> None:
        """Mark the settled starting state as consumed once a scenario begins."""
        self.baseline_settled = False

    def invalidate(self, reason: str) -> None:
        self.baseline_settled = False
        if self.active_run_id and self.sessions[self.active_run_id].status == "running":
            self.record_action("system", "training-state-invalidated", {"reason": reason})
            self.finish_session("aborted")

    @staticmethod
    def _slot_contents(slot: Any) -> dict[str, Any]:
        try:
            return slot.dict_contents()
        except Exception:  # noqa: BLE001
            return {"value": str(slot)}

    def priority_report(self) -> dict[str, Any]:
        active: list[dict[str, Any]] = []
        for key, registered in self.registry.all_points().items():
            obj = registered.bacnet_object
            array = getattr(obj, "priorityArray", None)
            if array is None:
                continue
            for index, slot in enumerate(array, start=1):
                contents = self._slot_contents(slot)
                if contents == {"null": ()} or "null" in contents:
                    continue
                owner = "instructor" if index == 3 else ("life-safety" if index <= 2 else "webctrl/external")
                active.append({"point": key, "priority": index, "owner": owner, "value": contents})
        external = [item for item in active if item["priority"] != 3]
        return {
            "active": active,
            "external": external,
            "external_count": len(external),
            "life_safety_count": sum(item["priority"] <= 2 for item in external),
            "decision_required": bool(external),
        }

    @staticmethod
    def _priority_fingerprint(report: dict[str, Any]) -> str:
        payload = json.dumps(report.get("external", []), sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    async def _release_external_priorities(self) -> dict[str, Any]:
        released: list[dict[str, Any]] = []
        preserved: list[dict[str, Any]] = []
        for item in self.priority_report()["external"]:
            if item["priority"] <= 2:
                preserved.append(item)
                continue
            registered = self.registry.all_points()[item["point"]]
            await registered.bacnet_object.write_property("presentValue", Null(()), priority=item["priority"])
            released.append(item)
        return {"released": released, "preserved_life_safety": preserved}

    def _capture_equipment_state(self) -> dict[str, Any]:
        state: dict[str, Any] = {}
        for equipment in self.engine.equipment:
            attrs: dict[str, Any] = {}
            for name, value in vars(equipment).items():
                if name in {"registry", "site_registry", "plant_registry", "manager_registry", "params",
                            "chillers", "boilers", "ahu_model", "chw_plant_model", "boiler_plant_model",
                            "_vav_models", "_cooling_coils", "_heating_coils"}:
                    continue
                if self._is_snapshot_value(value):
                    attrs[name] = copy.deepcopy(value)
            zone = getattr(equipment, "zone_model", None)
            if zone is not None:
                attrs["__zone_model__"] = {
                    name: copy.deepcopy(value)
                    for name, value in vars(zone).items()
                    if name != "params" and self._is_snapshot_value(value)
                }
            state[equipment.equipment_id] = attrs
        return state

    @classmethod
    def _is_snapshot_value(cls, value: Any) -> bool:
        if value is None or isinstance(value, (bool, int, float, str)):
            return True
        if isinstance(value, (list, tuple)):
            return all(cls._is_snapshot_value(item) for item in value)
        if isinstance(value, dict):
            return all(isinstance(key, (str, int, float, bool)) and cls._is_snapshot_value(item) for key, item in value.items())
        return False

    def _restore_equipment_state(self, state: dict[str, Any]) -> None:
        by_id = {equipment.equipment_id: equipment for equipment in self.engine.equipment}
        for equipment_id, attrs in state.items():
            equipment = by_id.get(equipment_id)
            if equipment is None:
                raise RuntimeError(f"checkpoint equipment '{equipment_id}' is unavailable")
            for name, value in attrs.items():
                if name == "__zone_model__":
                    zone = getattr(equipment, "zone_model", None)
                    if zone is None:
                        raise RuntimeError(f"checkpoint zone for '{equipment_id}' is unavailable")
                    for zone_name, zone_value in value.items():
                        setattr(zone, zone_name, copy.deepcopy(zone_value))
                else:
                    setattr(equipment, name, copy.deepcopy(value))

    def _capture_output_values(self) -> dict[str, float | bool]:
        return {
            key: self.registry._get(key)
            for key, registered in self.registry.all_points().items()
            if not registered.config.commandable
        }

    def _restore_output_values(self, values: dict[str, float | bool]) -> None:
        for key, value in values.items():
            if key in self.registry.all_points():
                self.registry._set(key, value)

    async def restore_baseline(self, baseline_id: str, priority_mode: str | None) -> dict[str, Any]:
        profile = self.baselines.get(baseline_id)
        if profile is None:
            raise KeyError(f"unknown baseline '{baseline_id}'")
        report = self.priority_report()
        if report["external_count"] and priority_mode not in {"retain", "release"}:
            raise PriorityReconciliationRequired(report)
        if self.active_run_id:
            self.finish_session("aborted")
        was_running = bool(self.engine.running)
        previous_equipment = list(self.engine.equipment)
        previous_speed = self.engine.speed_multiplier
        previous_clock = self.engine.simulated_seconds_elapsed
        previous_tick_count = self.engine.tick_count
        previous_baseline = (
            self.current_baseline_id,
            self.current_baseline_version,
            self.baseline_settled,
            self.reconciled_priority_mode,
            self.reconciled_priority_fingerprint,
        )
        await self.engine.stop()
        reconciliation = {"released": [], "preserved_life_safety": report["external"]}
        try:
            self.scenario_engine.reset()
            await self.scenario_engine.drain_priority_writes()
            if priority_mode == "release":
                reconciliation = await self._release_external_priorities()
            self.engine.equipment[:] = self.equipment_factory()
            self.engine.speed_multiplier = profile.speed_multiplier
            self.engine.reset_clock()
            site = next((item for item in self.engine.equipment if item.equipment_id == "ACI-SIM-SITE"), None)
            if site is not None:
                if "outside_air_temperature" in profile.weather:
                    site.target_oa_temp_f = float(profile.weather["outside_air_temperature"])
                if "outside_air_humidity" in profile.weather:
                    site.target_oa_humidity_pct = float(profile.weather["outside_air_humidity"])
            for command in profile.commands:
                accepted = self.scenario_engine._apply_force_or_release(
                    command.group_id, command.alias, "set_value", command.value
                )
                if not accepted:
                    raise RuntimeError(f"baseline command rejected: {command.group_id}.{command.alias}")
            await self.scenario_engine.drain_priority_writes()
            self.engine._advance_physics(profile.settle_seconds)
            if self.engine.diagnostics is not None:
                self.engine.diagnostics.tick()
            self.current_baseline_id = profile.baseline_id
            self.current_baseline_version = profile.version
            self.baseline_settled = True
            self.baseline_restored_at_wall_time = time.time()
            reconciled_report = self.priority_report()
            self.reconciled_priority_mode = priority_mode or "none-needed"
            self.reconciled_priority_fingerprint = self._priority_fingerprint(reconciled_report)
            self._baseline_checkpoints[profile.baseline_id] = {
                "version": profile.version,
                "equipment": self._capture_equipment_state(),
                "outputs": self._capture_output_values(),
                "simulated_seconds_elapsed": self.engine.simulated_seconds_elapsed,
                "tick_count": self.engine.tick_count,
            }
        except Exception:
            self.engine.equipment[:] = previous_equipment
            self.engine.speed_multiplier = previous_speed
            self.engine.simulated_seconds_elapsed = previous_clock
            self.engine.tick_count = previous_tick_count
            (
                self.current_baseline_id,
                self.current_baseline_version,
                self.baseline_settled,
                self.reconciled_priority_mode,
                self.reconciled_priority_fingerprint,
            ) = previous_baseline
            raise
        finally:
            if was_running:
                await self.engine.start()
        return {
            "restored": True,
            "baseline": profile.model_dump(),
            "priority_mode": priority_mode or "none-needed",
            "priority_reconciliation": reconciliation,
            "simulation": self.engine.status(),
            "settled": self.baseline_settled,
        }

    async def restore_checkpoint(self, baseline_id: str, priority_mode: str | None) -> dict[str, Any]:
        checkpoint = self._baseline_checkpoints.get(baseline_id)
        if checkpoint is None or checkpoint["version"] != self.baselines[baseline_id].version:
            return await self.restore_baseline(baseline_id, priority_mode)
        report = self.priority_report()
        if report["external_count"] and priority_mode not in {"retain", "release"}:
            raise PriorityReconciliationRequired(report)
        if self.active_run_id:
            self.finish_session("aborted")
        was_running = bool(self.engine.running)
        rollback_equipment = self._capture_equipment_state()
        rollback_outputs = self._capture_output_values()
        rollback_clock = self.engine.simulated_seconds_elapsed
        rollback_ticks = self.engine.tick_count
        previous_baseline = (
            self.current_baseline_id,
            self.current_baseline_version,
            self.baseline_settled,
            self.reconciled_priority_mode,
            self.reconciled_priority_fingerprint,
        )
        await self.engine.stop()
        try:
            self.scenario_engine.reset()
            await self.scenario_engine.drain_priority_writes()
            if priority_mode == "release":
                await self._release_external_priorities()
            self.fault_manager.clear_all()
            self._restore_equipment_state(checkpoint["equipment"])
            self._restore_output_values(checkpoint["outputs"])
            self.engine.simulated_seconds_elapsed = checkpoint["simulated_seconds_elapsed"]
            self.engine.tick_count = checkpoint["tick_count"]
            self.engine.last_tick_wall_time = None
            self.current_baseline_id = baseline_id
            self.current_baseline_version = checkpoint["version"]
            self.baseline_settled = True
            self.baseline_restored_at_wall_time = time.time()
            reconciled_report = self.priority_report()
            self.reconciled_priority_mode = priority_mode or "none-needed"
            self.reconciled_priority_fingerprint = self._priority_fingerprint(reconciled_report)
            if self.engine.diagnostics is not None:
                self.engine.diagnostics.tick()
        except Exception:
            self._restore_equipment_state(rollback_equipment)
            self._restore_output_values(rollback_outputs)
            self.engine.simulated_seconds_elapsed = rollback_clock
            self.engine.tick_count = rollback_ticks
            (
                self.current_baseline_id,
                self.current_baseline_version,
                self.baseline_settled,
                self.reconciled_priority_mode,
                self.reconciled_priority_fingerprint,
            ) = previous_baseline
            raise
        finally:
            if was_running:
                await self.engine.start()
        return {"restored": True, "from_checkpoint": True, "baseline_id": baseline_id, "simulation": self.engine.status()}

    def preflight(self, scenario_id: str) -> dict[str, Any]:
        scenario = self.scenario_engine.scenarios.get(scenario_id)
        if scenario is None:
            raise KeyError(f"unknown scenario '{scenario_id}'")
        blockers: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []
        status = self.scenario_engine.status()
        if status.get("effects_active"):
            blockers.append({"code": "scenario-effects-active", "message": "Stop/reset the current scenario effects."})
        active_faults = self.fault_manager.list_faults()
        if active_faults:
            blockers.append({"code": "active-faults", "message": f"{len(active_faults)} fault(s) are active."})
        if not self.current_baseline_id or not self.baseline_settled:
            blockers.append({"code": "baseline-not-settled", "message": "Restore and settle a named baseline."})
        priorities = self.priority_report()
        current_priority_fingerprint = self._priority_fingerprint(priorities)
        if priorities["external_count"] and current_priority_fingerprint != self.reconciled_priority_fingerprint:
            blockers.append({"code": "external-priorities", "message": f"{priorities['external_count']} external priority slot(s) require reconciliation."})
        elif priorities["external_count"]:
            warnings.append({"code": "retained-priorities", "message": f"{priorities['external_count']} unchanged external priority slot(s) were explicitly {self.reconciled_priority_mode}."})
        if abs(float(self.engine.speed_multiplier) - float(scenario.recommended_speed)) > 1e-9:
            warnings.append({"code": "speed-mismatch", "message": f"Recommended speed is {scenario.recommended_speed}x; current speed is {self.engine.speed_multiplier}x."})
        if not self.engine.running:
            warnings.append({"code": "engine-stopped", "message": "The engine is stopped."})
        return {
            "scenario_id": scenario_id,
            "can_start": not blockers,
            "blockers": blockers,
            "warnings": warnings,
            "baseline": {"baseline_id": self.current_baseline_id, "version": self.current_baseline_version, "settled": self.baseline_settled},
            "priorities": {**priorities, "reconciled_mode": self.reconciled_priority_mode, "unchanged_since_restore": current_priority_fingerprint == self.reconciled_priority_fingerprint},
            "prerequisites": scenario.prerequisites,
            "observation_points": scenario.observation_points,
            "recommended_speed": scenario.recommended_speed,
            "estimated_wall_seconds": round(scenario.duration_seconds / max(0.1, scenario.recommended_speed), 1),
        }

    def start_session(self, scenario_id: str, team: str, attempt: int, override_reason: str | None = None) -> dict[str, Any]:
        preflight = self.preflight(scenario_id)
        if not preflight["can_start"] and not (override_reason and override_reason.strip()):
            raise RuntimeError("training preflight failed")
        if self.active_run_id and self.sessions[self.active_run_id].status == "running":
            raise RuntimeError("another training session is active")
        scenario = self.scenario_engine.scenarios[scenario_id]
        run_id = time.strftime("%Y%m%d") + f"-{scenario_id}-" + secrets.token_hex(3)
        assertion_specs = self.outcomes.get(scenario_id, [])
        selected = list(dict.fromkeys([
            *scenario.observation_points,
            *(spec.point for spec in assertion_specs),
            *(spec.other_point for spec in assertion_specs if spec.other_point),
        ]))
        session = TrainingSession(
            run_id=run_id,
            scenario_id=scenario_id,
            baseline_id=self.current_baseline_id or "unsettled",
            team=team.strip()[:80],
            attempt=attempt,
            started_at_wall_time=time.time(),
            started_at_sim_seconds=float(self.engine.simulated_seconds_elapsed),
            selected_points=selected,
            assertions=[AssertionRuntime(spec=spec) for spec in assertion_specs],
        )
        self.sessions[run_id] = session
        self.active_run_id = run_id
        self.record_action("instructor", "session-start", {"preflight": preflight, "override_reason": override_reason})
        return self.session_summary(run_id)

    def record_action(self, actor: str, action: str, detail: dict[str, Any] | None = None) -> None:
        if not self.active_run_id:
            return
        session = self.sessions[self.active_run_id]
        session.actions.append({
            "wall_time": time.time(),
            "simulated_seconds": float(self.engine.simulated_seconds_elapsed),
            "actor": actor,
            "action": action,
            "detail": copy.deepcopy(detail or {}),
        })

    def tick(self, simulated_seconds: float) -> None:
        if not self.active_run_id:
            return
        session = self.sessions[self.active_run_id]
        if session.status != "running":
            return
        elapsed = simulated_seconds - session.started_at_sim_seconds
        values: dict[str, float | bool | None] = {}
        live = self.registry.all_points()
        for key in session.selected_points:
            values[key] = self.registry._get(key) if key in live else None
        sample = {"wall_time": time.time(), "simulated_seconds": simulated_seconds, "elapsed_seconds": elapsed, "values": values}
        session.samples.append(sample)
        if len(session.samples) > self.evidence_limit:
            del session.samples[: len(session.samples) - self.evidence_limit]
        dt = 0.0 if session.last_sample_sim_seconds is None else max(0.0, simulated_seconds - session.last_sample_sim_seconds)
        session.last_sample_sim_seconds = simulated_seconds
        self._evaluate_assertions(session, values, elapsed, dt)
        command = self.get_last_command()
        if command:
            command_key = json.dumps(command, sort_keys=True, default=str)
            if command_key != session.last_external_command_key:
                session.last_external_command_key = command_key
                self.record_action("webctrl", "bacnet-command", command)

    @staticmethod
    def _compare(spec: OutcomeAssertion, left: float | bool, right: float | bool) -> bool:
        if spec.operator == "eq":
            return abs(float(left) - float(right)) <= spec.tolerance
        if spec.operator == "ne":
            return abs(float(left) - float(right)) > spec.tolerance
        if spec.operator == "gt": return float(left) > float(right)
        if spec.operator == "ge": return float(left) >= float(right)
        if spec.operator == "lt": return float(left) < float(right)
        if spec.operator == "le": return float(left) <= float(right)
        return float(right) - spec.tolerance <= float(left) <= float(spec.upper_value) + spec.tolerance

    def _evaluate_assertions(self, session: TrainingSession, values: dict[str, Any], elapsed: float, dt: float) -> None:
        for runtime in session.assertions:
            spec = runtime.spec
            if runtime.status != "pending" or elapsed < spec.start_seconds:
                continue
            if spec.end_seconds is not None and elapsed > spec.end_seconds:
                runtime.status = "failed"
                continue
            left = values.get(spec.point)
            right = values.get(spec.other_point) if spec.other_point else spec.value
            if left is None or right is None:
                runtime.consecutive_seconds = 0.0
                continue
            if spec.other_point:
                right = float(right) + spec.offset
            runtime.last_value = left
            runtime.last_reference = right
            if self._compare(spec, left, right):
                runtime.consecutive_seconds += dt
                if spec.for_seconds == 0 or runtime.consecutive_seconds >= spec.for_seconds:
                    runtime.status = "passed"
                    runtime.first_passed_at = elapsed
            else:
                runtime.consecutive_seconds = 0.0

    def finish_session(self, status: str = "completed") -> dict[str, Any]:
        if not self.active_run_id:
            raise RuntimeError("no training session is active")
        session = self.sessions[self.active_run_id]
        for runtime in session.assertions:
            if runtime.status == "pending":
                runtime.status = "failed"
        session.status = status
        session.stopped_at_wall_time = time.time()
        self.record_action("instructor", "session-finish", {"status": status})
        run_id = session.run_id
        self.active_run_id = None
        self._persist_evidence(run_id)
        return self.session_summary(run_id)

    def _persist_evidence(self, run_id: str) -> None:
        if self.evidence_dir is None:
            return
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        target = self.evidence_dir / f"{run_id}.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self.evidence(run_id, self.evidence_limit), indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        temporary.replace(target)
        self.evidence_paths[run_id] = str(target)

    def session_summary(self, run_id: str) -> dict[str, Any]:
        session = self.sessions[run_id]
        total_weight = sum(item.spec.weight for item in session.assertions)
        passed_weight = sum(item.spec.weight for item in session.assertions if item.status == "passed")
        score = round(100.0 * passed_weight / total_weight, 1) if total_weight else None
        return {
            "run_id": session.run_id,
            "scenario_id": session.scenario_id,
            "baseline_id": session.baseline_id,
            "team": session.team,
            "attempt": session.attempt,
            "status": session.status,
            "started_at_wall_time": session.started_at_wall_time,
            "stopped_at_wall_time": session.stopped_at_wall_time,
            "sample_count": len(session.samples),
            "action_count": len(session.actions),
            "score": score,
            "evidence_path": self.evidence_paths.get(run_id),
            "assertions": [
                {
                    **item.spec.model_dump(),
                    "status": item.status,
                    "consecutive_seconds": round(item.consecutive_seconds, 1),
                    "first_passed_at": item.first_passed_at,
                    "last_value": item.last_value,
                    "last_reference": item.last_reference,
                }
                for item in session.assertions
            ],
        }

    def evidence(self, run_id: str, limit: int = 1000) -> dict[str, Any]:
        session = self.sessions[run_id]
        return {"summary": self.session_summary(run_id), "samples": session.samples[-limit:], "actions": session.actions}
