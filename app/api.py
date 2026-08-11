"""
FastAPI application: the Basic UI, extended in Phase 4 with fault
injection and scenario control endpoints for the Instructor Panel, and in
Phase 6a with LLM orchestration endpoints. Still deliberately simple:
polling-based REST endpoints and one static HTML dashboard. The P0 training
layer adds short-lived instructor/student role tokens around every mutation
while leaving telemetry readable to a student station.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config_models import ObjectType
from app.diagnostics import CommandCenterDiagnostics
from app.engine import SimulationEngine
from app.faults import TRANSPORT_FAULT_TYPES, FaultManager, FaultType
from app.llm.action_schema import LlmActionBundle
from app.llm.ollama_client import OllamaClient, OllamaConnectionError
from app.logging_setup import recent_app_events, recent_bacnet_traffic
from app.scenario import ScenarioEngine
from app.services.orchestration_service import OrchestrationService
from app.transport import BacnetTransport
from app.training import PriorityReconciliationRequired, TrainingManager

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
logger = logging.getLogger("aci_sim.api")


class SetFaultRequest(BaseModel):
    fault_id: Optional[str] = None
    fault_type: str
    group_id: Optional[str] = None
    alias: Optional[str] = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class ForceValueRequest(BaseModel):
    group_id: str
    alias: str
    value: Any = None


class LlmProposeRequest(BaseModel):
    instructor_request: str


class LlmApplyRequest(BaseModel):
    bundle: dict[str, Any]  # the LlmActionBundle exactly as returned by /api/llm/propose
    proposal_token: str


class DuctStaticPidRequest(BaseModel):
    kp: float = Field(ge=0.0, le=100.0, allow_inf_nan=False)
    ki: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    kd: float = Field(ge=0.0, le=20.0, allow_inf_nan=False)
    interval_seconds: float = Field(
        ge=0.5,
        le=10.0,
        allow_inf_nan=False,
    )


class TrainingLoginRequest(BaseModel):
    role: str
    pin: str | None = None
    label: str = ""


class BaselineRestoreRequest(BaseModel):
    priority_mode: Literal["retain", "release"] | None = None


class TrainingSessionStartRequest(BaseModel):
    scenario_id: str
    team: str = ""
    attempt: int = Field(default=1, ge=1, le=999)
    override_reason: str | None = None


class TrainingMarkerRequest(BaseModel):
    action: str = Field(min_length=1, max_length=80)
    detail: dict[str, Any] = Field(default_factory=dict)


class TrainingSessionFinishRequest(BaseModel):
    status: Literal["completed", "aborted"] = "completed"


def _bundle_digest(bundle: LlmActionBundle) -> str:
    """Create a stable digest for one-time proposal approval."""
    payload = json.dumps(
        bundle.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def create_app(
    transport: BacnetTransport, engine: SimulationEngine,
    fault_manager: FaultManager, scenario_engine: ScenarioEngine,
    orchestration_service: OrchestrationService, ollama_client: OllamaClient,
    diagnostics: CommandCenterDiagnostics | None = None,
    equipment_factory: Callable[[], list] | None = None,
    training_manager: TrainingManager | None = None,
) -> FastAPI:
    app = FastAPI(title="ACI BACnet Building Simulation Platform")
    app.state.transport = transport
    app.state.engine = engine
    app.state.fault_manager = fault_manager
    app.state.scenario_engine = scenario_engine
    app.state.orchestration_service = orchestration_service
    app.state.ollama_client = ollama_client
    app.state.command_center_diagnostics = (
        diagnostics if diagnostics is not None else getattr(engine, "diagnostics", None)
    )
    app.state.start_time = time.time()
    app.state.pending_llm_proposals = {}
    app.state.equipment_factory = equipment_factory
    app.state.training_manager = training_manager
    app.state.restart_lock = asyncio.Lock()
    app.state.lifecycle_lock = asyncio.Lock()

    @app.middleware("http")
    async def serialize_state_changes(request: Request, call_next):
        """Prevent restart from racing another state-changing API request."""
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return await call_next(request)
        identity = None
        if training_manager is not None and training_manager.auth.required:
            path = request.url.path
            if path == "/api/training/auth/login":
                return await call_next(request)
            identity = training_manager.auth.identity(request.headers.get("authorization"))
            if identity is None:
                return JSONResponse({"error": "training authentication required"}, status_code=401)
            student_allowed = (
                path == "/api/training/auth/logout"
                or (path.startswith("/api/training/sessions/") and path.endswith("/markers"))
            )
            if identity["role"] != "instructor" and not student_allowed:
                return JSONResponse({"error": "instructor role required"}, status_code=403)
        async with app.state.lifecycle_lock:
            response = await call_next(request)
        if training_manager is not None and identity is not None:
            training_manager.record_action(
                identity["role"],
                "api-mutation",
                {"method": request.method, "path": request.url.path, "status_code": response.status_code},
            )
            if response.status_code < 400 and (
                request.url.path.startswith("/api/faults")
                or request.url.path in {"/api/force", "/api/release", "/api/site/weather"}
                or request.url.path.startswith("/api/ahu/duct-static/pid")
            ):
                training_manager.consume_baseline()
        return response

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "command-center.html")

    @app.get("/api/status")
    def status() -> JSONResponse:
        bacnet_app = transport.app
        registry = transport.registry
        site_model = next((e for e in engine.equipment if e.equipment_id == "ACI-SIM-SITE"), None)
        groups_summary = [
            {
                "group_id": g.group_id,
                "instance_offset": g.instance_offset,
                "description": g.description,
                "point_count": len(g.points),
            }
            for g in sorted(registry.groups, key=lambda g: g.instance_offset)
        ]
        return JSONResponse(
            {
                "simulation": engine.status(),
                "device": {
                    "name": transport.supervisory_config.device_name,
                    "instance": transport.supervisory_config.device_instance,
                    "bind_address": transport.network_config.bind_address,
                    "udp_port": transport.network_config.udp_port,
                    "private_lab_mode": transport.network_config.private_lab_mode,
                    "respond_to_who_is": transport.network_config.respond_to_who_is,
                },
                "bacnet": {
                    "messages_in": bacnet_app.messages_in if bacnet_app else 0,
                    "messages_blocked": bacnet_app.messages_blocked if bacnet_app else 0,
                    "cov_notification_failures": (
                        getattr(bacnet_app, "cov_notification_failures", 0)
                        if bacnet_app
                        else 0
                    ),
                    "last_cov_notification_failure": (
                        getattr(
                            bacnet_app,
                            "last_cov_notification_failure",
                            None,
                        )
                        if bacnet_app
                        else None
                    ),
                    "peer_allowlist": transport.network_config.peer_allowlist,
                    "last_command_received": bacnet_app.last_command_received if bacnet_app else None,
                },
                "fleet": {
                    "group_count": len(groups_summary),
                    "total_point_count": len(registry.all_points()),
                },
                "groups": groups_summary,
                "active_fault_count": len(fault_manager.list_faults()),
                "active_priority_override_count": (
                    scenario_engine.active_priority_override_count
                ),
                "scenario": scenario_engine.status(),
                "site": {
                    "target_oa_temp_f": getattr(site_model, "target_oa_temp_f", None),
                    "target_oa_humidity_pct": getattr(site_model, "target_oa_humidity_pct", None),
                },
                "training": (
                    {
                        "auth_required": training_manager.auth.required,
                        "current_baseline_id": training_manager.current_baseline_id,
                        "current_baseline_version": training_manager.current_baseline_version,
                        "baseline_settled": training_manager.baseline_settled,
                        "active_run_id": training_manager.active_run_id,
                    }
                    if training_manager is not None
                    else {"auth_required": False, "available": False}
                ),
                "uptime_seconds": round(time.time() - app.state.start_time, 1),
            }
        )

    @app.get("/api/points")
    def points(group: str | None = None) -> JSONResponse:
        registry = transport.registry
        # Both branches below normalize to {alias: RegisteredPoint} with the
        # group prefix already stripped, so the loop body is identical either way.
        if group:
            items = registry.points_for_group(group).items()
        else:
            items = ((k.split(".", 1)[1], v) for k, v in registry.all_points().items())

        active_faults_by_target: dict[tuple, list] = {}
        for f in fault_manager.list_faults():
            if f.group_id and f.alias:
                active_faults_by_target.setdefault((f.group_id, f.alias), []).append(f)

        result = []
        for alias, rp in items:
            cfg = rp.config
            faults_here = active_faults_by_target.get((rp.group_id, alias), [])
            priority_forced = scenario_engine.is_priority_forced(
                rp.group_id,
                alias,
            )
            result.append(
                {
                    "group": rp.group_id,
                    "alias": alias,
                    "object_type": cfg.object_type.value,
                    "object_instance": rp.global_instance,
                    "object_name": cfg.object_name,
                    "direction": cfg.signal_direction.value,
                    "units": cfg.units,
                    "writable": cfg.writable,
                    "commandable": cfg.commandable,
                    "interlock": cfg.interlock,
                    "present_value": registry._get(f"{rp.group_id}.{alias}"),
                    "normal_range": cfg.normal_range.model_dump() if cfg.normal_range else None,
                    "active_faults": [f.fault_type.value for f in faults_here],
                    "forced": (
                        priority_forced
                        or any(f.fault_id.startswith("force:") for f in faults_here)
                    ),
                    "instructor_priority_3": priority_forced,
                }
            )
        return JSONResponse(result)

    @app.get("/api/groups")
    def groups() -> JSONResponse:
        registry = transport.registry
        return JSONResponse(
            sorted(
                [
                    {
                        "group_id": g.group_id,
                        "instance_offset": g.instance_offset,
                        "description": g.description,
                        "point_count": len(g.points),
                    }
                    for g in registry.groups
                ],
                key=lambda g: g["instance_offset"],
            )
        )

    @app.get("/api/command-center")
    async def command_center() -> JSONResponse:
        monitor = app.state.command_center_diagnostics
        if monitor is None:
            return JSONResponse(
                {"error": "command-center diagnostics are not configured"},
                status_code=503,
            )
        return JSONResponse(monitor.snapshot())

    # NOTE: every endpoint that starts the engine loop or triggers a BACnet
    # priority-array write (force/release, scenarios) MUST be `async def` so
    # it runs on the event loop. A sync def runs in FastAPI's threadpool,
    # where asyncio.create_task/ensure_future raise "no running event loop"
    # -- this bit us live: engine.start() 500'd and left a corrupted flag.

    @app.post("/api/simulation/start")
    async def start_simulation() -> JSONResponse:
        await engine.start()
        return JSONResponse(engine.status())

    @app.post("/api/simulation/stop")
    async def stop_simulation() -> JSONResponse:
        await engine.stop()
        return JSONResponse(engine.status())

    @app.post("/api/simulation/speed/{multiplier}")
    async def set_speed(multiplier: float) -> JSONResponse:
        engine.speed_multiplier = max(0.1, min(60.0, multiplier))
        return JSONResponse(engine.status())

    @app.post("/api/simulation/stop-all")
    async def stop_all_simulation() -> JSONResponse:
        """Prominent Stop-All-Simulation control: stops the engine AND clears every active fault/force/scenario."""
        await engine.stop()
        scenario_engine.reset()
        await scenario_engine.drain_priority_writes()
        transport.registry.synchronize_reliability(fault_manager)
        if training_manager is not None:
            training_manager.invalidate("stop-all")
        return JSONResponse({"simulation": engine.status(), "faults_cleared": True})

    @app.post("/api/simulation/restart")
    async def restart_simulation() -> JSONResponse:
        """
        Reset the simulated plant while preserving the live BACnet session.

        Equipment state, faults, instructor forces, and the simulation clock
        return to defaults.  WebCTRL-owned BACnet priority-array commands are
        deliberately preserved.  The BACnet object graph remains online so
        existing WebCTRL COV subscriptions do not disappear for the remainder
        of their previous lifetime.  An I-Am announcement still refreshes the
        supervisory device binding.
        """
        factory = app.state.equipment_factory
        if factory is None:
            return JSONResponse(
                {"error": "equipment restart factory is not configured"},
                status_code=503,
            )
        async with app.state.restart_lock:
            previous_equipment = list(engine.equipment)
            previous_speed = engine.speed_multiplier
            try:
                replacement_equipment = factory()
                await engine.stop()
                scenario_engine.reset()
                await scenario_engine.drain_priority_writes()
                await transport.registry.reset_runtime_state()
                i_am_announced = False
                try:
                    transport.app.i_am()
                    i_am_announced = True
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "BACnet I-Am announcement failed during restart"
                    )
                engine.equipment[:] = replacement_equipment
                engine.speed_multiplier = 1.0
                engine.reset_clock()
                if engine.diagnostics is not None:
                    try:
                        engine.diagnostics.tick()
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "Diagnostics refresh failed during restart"
                        )
                await engine.start()
            except Exception as exc:  # noqa: BLE001
                logger.exception("Simulation restart failed")
                recovery = {
                    "attempted": True,
                    "engine_restored": False,
                    "bacnet_session_preserved": transport.app is not None,
                }
                recovery_error = None
                try:
                    await engine.stop()
                    engine.equipment[:] = previous_equipment
                    engine.speed_multiplier = previous_speed
                    if engine.diagnostics is not None:
                        try:
                            engine.diagnostics.tick()
                        except Exception:  # noqa: BLE001
                            logger.exception(
                                "Diagnostics refresh failed during "
                                "restart recovery"
                            )
                    await engine.start()
                    recovery["engine_restored"] = True
                except Exception as recovery_exc:  # noqa: BLE001
                    logger.exception("Simulation restart recovery failed")
                    recovery_error = str(recovery_exc)
                    await engine.stop()
                    engine.equipment.clear()
                return JSONResponse(
                    {
                        "error": (
                            "simulation restart did not complete; review the "
                            f"application log before retrying: {exc}"
                        ),
                        "recovery": recovery,
                        "recovery_error": recovery_error,
                        "simulation": engine.status(),
                    },
                    status_code=500,
                )
            app.state.start_time = time.time()
            if training_manager is not None:
                training_manager.invalidate("manual-restart")
            return JSONResponse(
                {
                    "restarted": True,
                    "bacnet_rebound": False,
                    "bacnet_session_preserved": True,
                    "cov_subscriptions_preserved": True,
                    "webctrl_commands_preserved": True,
                    "i_am_announced": i_am_announced,
                    "faults_cleared": True,
                    "priority_overrides_cleared": (
                        scenario_engine.active_priority_override_count == 0
                    ),
                    "webctrl_reconnect": (
                        "BACnet session and COV subscriptions preserved; "
                        "I-Am announced to refresh the WebCTRL binding"
                    ),
                    "simulation": engine.status(),
                    "fleet": {
                        "group_count": len(transport.registry.groups),
                        "total_point_count": len(
                            transport.registry.all_points()
                        ),
                    },
                }
            )

    def _ahu_model():
        return next(
            (
                equipment
                for equipment in engine.equipment
                if equipment.equipment_id == "ACI-SIM-AHU-1"
            ),
            None,
        )

    @app.get("/api/ahu/duct-static")
    def duct_static_status() -> JSONResponse:
        ahu = _ahu_model()
        if ahu is None or not hasattr(ahu, "duct_static_snapshot"):
            return JSONResponse(
                {"error": "AHU-1 duct-static model is unavailable"},
                status_code=503,
            )
        return JSONResponse(ahu.duct_static_snapshot())

    @app.get("/api/ahu/command-center")
    def ahu_command_center_status(history_limit: int = 180) -> JSONResponse:
        ahu = _ahu_model()
        if ahu is None or not hasattr(ahu, "ahu_command_center_snapshot"):
            return JSONResponse(
                {"error": "AHU-1 command-center model is unavailable"},
                status_code=503,
            )
        return JSONResponse(
            ahu.ahu_command_center_snapshot(history_limit=history_limit)
        )

    @app.put("/api/ahu/duct-static/pid")
    def configure_duct_static_pid(request: DuctStaticPidRequest) -> JSONResponse:
        ahu = _ahu_model()
        if ahu is None or not hasattr(ahu, "configure_duct_static_pid"):
            return JSONResponse(
                {"error": "AHU-1 duct-static model is unavailable"},
                status_code=503,
            )
        ahu.configure_duct_static_pid(**request.model_dump())
        return JSONResponse(ahu.duct_static_snapshot())

    @app.post("/api/ahu/duct-static/pid/reset")
    def reset_duct_static_pid() -> JSONResponse:
        ahu = _ahu_model()
        if ahu is None or not hasattr(ahu, "reset_duct_static_pid"):
            return JSONResponse(
                {"error": "AHU-1 duct-static model is unavailable"},
                status_code=503,
            )
        ahu.reset_duct_static_pid(clear_history=False)
        return JSONResponse(ahu.duct_static_snapshot())

    @app.post("/api/ahu/duct-static/pid/defaults")
    def restore_duct_static_pid_defaults() -> JSONResponse:
        ahu = _ahu_model()
        if ahu is None or not hasattr(ahu, "restore_duct_static_pid_defaults"):
            return JSONResponse(
                {"error": "AHU-1 duct-static model is unavailable"},
                status_code=503,
            )
        ahu.restore_duct_static_pid_defaults()
        return JSONResponse(ahu.duct_static_snapshot())

    @app.post("/api/site/weather")
    async def set_weather(
        oa_temp_f: float | None = Query(default=None, ge=-50.0, le=150.0),
        oa_humidity_pct: float | None = Query(default=None, ge=0.0, le=100.0),
    ) -> JSONResponse:
        """Instructor control: adjust outside air conditions for seasonal training."""
        site_model = next((e for e in engine.equipment if e.equipment_id == "ACI-SIM-SITE"), None)
        if site_model is None:
            return JSONResponse({"error": "site model not yet initialized"}, status_code=503)
        if oa_temp_f is not None:
            site_model.target_oa_temp_f = oa_temp_f
        if oa_humidity_pct is not None:
            site_model.target_oa_humidity_pct = oa_humidity_pct
        return JSONResponse(
            {"target_oa_temp_f": site_model.target_oa_temp_f, "target_oa_humidity_pct": site_model.target_oa_humidity_pct}
        )

    # ---- Phase 4: fault injection --------------------------------------

    @app.get("/api/fault-types")
    def fault_types() -> JSONResponse:
        return JSONResponse([t.value for t in FaultType])

    @app.get("/api/faults")
    def list_faults() -> JSONResponse:
        return JSONResponse(
            [
                {
                    "fault_id": f.fault_id, "fault_type": f.fault_type.value,
                    "group_id": f.group_id, "alias": f.alias,
                    "parameters": {k: v for k, v in f.parameters.items() if not k.startswith("_")},
                    "is_manual_force": f.fault_id.startswith("force:"),
                }
                for f in fault_manager.list_faults()
            ]
        )

    @app.post("/api/faults/set")
    async def set_fault(req: SetFaultRequest) -> JSONResponse:
        try:
            fault_type = FaultType(req.fault_type)
        except ValueError:
            return JSONResponse({"error": f"unknown fault_type '{req.fault_type}'"}, status_code=400)

        is_transport_fault = fault_type in TRANSPORT_FAULT_TYPES
        if is_transport_fault:
            if req.group_id or req.alias:
                return JSONResponse(
                    {"error": f"{fault_type.value} is a whole-device fault; leave group and alias empty"},
                    status_code=422,
                )
        else:
            if not req.group_id or not req.alias:
                return JSONResponse(
                    {"error": f"{fault_type.value} requires both group_id and alias"},
                    status_code=422,
                )
            if f"{req.group_id}.{req.alias}" not in transport.registry.all_points():
                return JSONResponse(
                    {"error": f"unknown point '{req.group_id}.{req.alias}'"},
                    status_code=404,
                )
            if (
                fault_type == FaultType.safety_bypass
                and (
                    req.group_id != "ACI-SIM-AHU-1"
                    or req.alias
                    not in {
                        "automatic_high_static_trip",
                        "automatic_freezestat_trip",
                    }
                )
            ):
                return JSONResponse(
                    {
                        "error": (
                            "safety_bypass is valid only for "
                            "ACI-SIM-AHU-1.automatic_high_static_trip or "
                            "ACI-SIM-AHU-1.automatic_freezestat_trip"
                        )
                    },
                    status_code=422,
                )

        parameters = dict(req.parameters)
        # Accept the original dashboard's generic "value" field while
        # normalizing it to the parameter names each mechanic actually uses.
        if fault_type == FaultType.offset and "offset" not in parameters and "value" in parameters:
            parameters["offset"] = parameters.pop("value")
        elif fault_type == FaultType.slow_response and "delay_seconds" not in parameters and "value" in parameters:
            parameters["delay_seconds"] = parameters.pop("value")
        elif (
            fault_type == FaultType.intermittent_comm
            and "drop_probability" not in parameters
            and "value" in parameters
        ):
            parameters["drop_probability"] = parameters.pop("value")

        required_parameters = {
            FaultType.offset: "offset",
            FaultType.drift: "rate_per_second",
            FaultType.forced_status: "value",
        }
        required = required_parameters.get(fault_type)
        if required and required not in parameters:
            return JSONResponse(
                {"error": f"{fault_type.value} requires parameter '{required}'"},
                status_code=422,
            )
        if fault_type == FaultType.slow_response:
            delay = parameters.get("delay_seconds", 3.0)
            if not isinstance(delay, (int, float)) or isinstance(delay, bool) or not 0 < delay <= 30:
                return JSONResponse(
                    {"error": "slow_response delay_seconds must be greater than 0 and at most 30"},
                    status_code=422,
                )
        if fault_type == FaultType.intermittent_comm:
            probability = parameters.get("drop_probability", 0.3)
            if (
                not isinstance(probability, (int, float))
                or isinstance(probability, bool)
                or not 0 <= probability <= 1
            ):
                return JSONResponse(
                    {"error": "intermittent_comm drop_probability must be between 0 and 1"},
                    status_code=422,
                )

        fault_id = req.fault_id or f"manual:{req.group_id}.{req.alias}.{req.fault_type}"
        try:
            instance = fault_manager.set_fault(
                fault_id,
                fault_type,
                req.group_id,
                req.alias,
                parameters,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return JSONResponse({"fault_id": instance.fault_id, "fault_type": instance.fault_type.value})

    @app.post("/api/faults/clear")
    async def clear_fault(fault_id: str) -> JSONResponse:
        cleared = fault_manager.clear_fault(fault_id)
        transport.registry.synchronize_reliability(fault_manager)
        return JSONResponse({"cleared": cleared})

    @app.post("/api/faults/clear-all")
    async def clear_all_faults() -> JSONResponse:
        fault_manager.clear_all()
        transport.registry.synchronize_reliability(fault_manager)
        return JSONResponse({"cleared": True})

    @app.post("/api/force")
    async def force_value(req: ForceValueRequest) -> JSONResponse:
        key = f"{req.group_id}.{req.alias}"
        point = transport.registry.all_points().get(key)
        if point is None:
            return JSONResponse({"error": f"unknown point '{key}'"}, status_code=404)
        if req.value is None or req.value == "":
            return JSONResponse({"error": "a force value is required"}, status_code=422)
        if point.config.object_type in (
            ObjectType.binary_input,
            ObjectType.binary_output,
            ObjectType.binary_value,
        ) and req.value not in (True, False, 0, 1, 0.0, 1.0, "active", "inactive"):
            return JSONResponse(
                {"error": f"binary point '{key}' requires true/false, active/inactive, or 1/0"},
                status_code=422,
            )
        accepted = scenario_engine._apply_force_or_release(
            req.group_id,
            req.alias,
            "set_value",
            req.value,
        )
        if not accepted:
            return JSONResponse(
                {"error": f"value is invalid or outside the configured range for '{key}'"},
                status_code=422,
            )
        await scenario_engine.drain_priority_writes()
        return JSONResponse({"forced": True, "group_id": req.group_id, "alias": req.alias, "value": req.value})

    @app.post("/api/release")
    async def release_value(req: ForceValueRequest) -> JSONResponse:
        key = f"{req.group_id}.{req.alias}"
        if key not in transport.registry.all_points():
            return JSONResponse({"error": f"unknown point '{key}'"}, status_code=404)
        scenario_engine._apply_force_or_release(req.group_id, req.alias, "release_value", None)
        await scenario_engine.drain_priority_writes()
        return JSONResponse({"released": True, "group_id": req.group_id, "alias": req.alias})

    # ---- P0 training workflow -------------------------------------------

    @app.get("/api/training/auth/status")
    def training_auth_status(request: Request) -> JSONResponse:
        if training_manager is None:
            return JSONResponse({"available": False, "auth_required": False})
        identity = training_manager.auth.identity(request.headers.get("authorization"))
        return JSONResponse({
            "available": True,
            "auth_required": training_manager.auth.required,
            "authenticated": identity is not None,
            "identity": identity,
        })

    @app.post("/api/training/auth/login")
    def training_login(req: TrainingLoginRequest) -> JSONResponse:
        if training_manager is None:
            return JSONResponse({"error": "training layer is unavailable"}, status_code=503)
        try:
            result = training_manager.auth.login(req.role, req.pin, req.label)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        except PermissionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        return JSONResponse(result)

    @app.post("/api/training/auth/logout")
    def training_logout(request: Request) -> JSONResponse:
        if training_manager is not None:
            training_manager.auth.logout(request.headers.get("authorization"))
        return JSONResponse({"logged_out": True})

    @app.get("/api/training/baselines")
    def training_baselines() -> JSONResponse:
        if training_manager is None:
            return JSONResponse({"error": "training layer is unavailable"}, status_code=503)
        return JSONResponse(training_manager.list_baselines())

    @app.get("/api/training/priorities")
    def training_priorities() -> JSONResponse:
        if training_manager is None:
            return JSONResponse({"error": "training layer is unavailable"}, status_code=503)
        return JSONResponse(training_manager.priority_report())

    @app.post("/api/training/baselines/{baseline_id}/restore")
    async def training_restore_baseline(baseline_id: str, req: BaselineRestoreRequest) -> JSONResponse:
        if training_manager is None:
            return JSONResponse({"error": "training layer is unavailable"}, status_code=503)
        try:
            return JSONResponse(await training_manager.restore_baseline(baseline_id, req.priority_mode))
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except PriorityReconciliationRequired as exc:
            return JSONResponse({"error": str(exc), "priority_report": exc.report}, status_code=409)

    @app.post("/api/training/checkpoints/{baseline_id}/restore")
    async def training_restore_checkpoint(baseline_id: str, req: BaselineRestoreRequest) -> JSONResponse:
        if training_manager is None:
            return JSONResponse({"error": "training layer is unavailable"}, status_code=503)
        try:
            return JSONResponse(await training_manager.restore_checkpoint(baseline_id, req.priority_mode))
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except PriorityReconciliationRequired as exc:
            return JSONResponse({"error": str(exc), "priority_report": exc.report}, status_code=409)

    @app.get("/api/training/preflight/{scenario_id}")
    def training_preflight(scenario_id: str) -> JSONResponse:
        if training_manager is None:
            return JSONResponse({"error": "training layer is unavailable"}, status_code=503)
        try:
            return JSONResponse(training_manager.preflight(scenario_id))
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.post("/api/training/sessions")
    def training_start_session(req: TrainingSessionStartRequest) -> JSONResponse:
        if training_manager is None:
            return JSONResponse({"error": "training layer is unavailable"}, status_code=503)
        try:
            return JSONResponse(training_manager.start_session(
                req.scenario_id, req.team, req.attempt, req.override_reason
            ))
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except RuntimeError as exc:
            preflight = None
            try:
                preflight = training_manager.preflight(req.scenario_id)
            except KeyError:
                pass
            return JSONResponse({"error": str(exc), "preflight": preflight}, status_code=409)

    @app.get("/api/training/sessions/active")
    def training_active_session() -> JSONResponse:
        if training_manager is None:
            return JSONResponse({"error": "training layer is unavailable"}, status_code=503)
        if training_manager.active_run_id is None:
            return JSONResponse({"active": False})
        return JSONResponse({"active": True, **training_manager.session_summary(training_manager.active_run_id)})

    @app.get("/api/training/sessions/{run_id}")
    def training_session(run_id: str) -> JSONResponse:
        if training_manager is None:
            return JSONResponse({"error": "training layer is unavailable"}, status_code=503)
        if run_id not in training_manager.sessions:
            return JSONResponse({"error": "training session not found"}, status_code=404)
        return JSONResponse(training_manager.session_summary(run_id))

    @app.get("/api/training/sessions/{run_id}/evidence")
    def training_session_evidence(run_id: str, limit: int = Query(default=1000, ge=1, le=20000)) -> JSONResponse:
        if training_manager is None:
            return JSONResponse({"error": "training layer is unavailable"}, status_code=503)
        if run_id not in training_manager.sessions:
            return JSONResponse({"error": "training session not found"}, status_code=404)
        return JSONResponse(training_manager.evidence(run_id, limit))

    @app.post("/api/training/sessions/{run_id}/markers")
    def training_session_marker(run_id: str, req: TrainingMarkerRequest, request: Request) -> JSONResponse:
        if training_manager is None:
            return JSONResponse({"error": "training layer is unavailable"}, status_code=503)
        if run_id != training_manager.active_run_id:
            return JSONResponse({"error": "training session is not active"}, status_code=409)
        identity = training_manager.auth.identity(request.headers.get("authorization")) or {"role": "instructor"}
        training_manager.record_action(identity["role"], req.action, req.detail)
        return JSONResponse({"recorded": True})

    @app.post("/api/training/sessions/{run_id}/finish")
    def training_finish_session(run_id: str, req: TrainingSessionFinishRequest) -> JSONResponse:
        if training_manager is None:
            return JSONResponse({"error": "training layer is unavailable"}, status_code=503)
        if run_id != training_manager.active_run_id:
            return JSONResponse({"error": "training session is not active"}, status_code=409)
        return JSONResponse(training_manager.finish_session(req.status))

    # ---- Phase 4: scenarios ---------------------------------------------

    @app.get("/api/scenarios")
    def list_scenarios() -> JSONResponse:
        return JSONResponse(
            [
                {
                    "scenario_id": s.scenario_id, "title": s.title, "description": s.description,
                    "event_count": len(s.events), "student_objectives": s.student_objectives,
                    "duration_seconds": s.duration_seconds,
                    "recommended_speed": s.recommended_speed,
                    "difficulty": s.difficulty,
                    "observation_points": s.observation_points,
                    "prerequisites": s.prerequisites,
                    "tags": s.tags,
                }
                for s in scenario_engine.list_scenarios()
            ]
        )

    @app.get("/api/scenarios/{scenario_id}")
    def get_scenario(scenario_id: str) -> JSONResponse:
        s = scenario_engine.scenarios.get(scenario_id)
        if s is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(s.model_dump())

    @app.post("/api/scenarios/{scenario_id}/start")
    async def start_scenario(scenario_id: str) -> JSONResponse:
        if training_manager is not None:
            active_run_id = training_manager.active_run_id
            if active_run_id is None:
                return JSONResponse(
                    {"error": "start a preflighted training session before starting a scenario"},
                    status_code=409,
                )
            active_session = training_manager.sessions[active_run_id]
            if active_session.scenario_id != scenario_id:
                return JSONResponse(
                    {"error": f"active training session is for '{active_session.scenario_id}'"},
                    status_code=409,
                )
        try:
            scenario_engine.start(scenario_id)
            await scenario_engine.drain_priority_writes()
        except KeyError as e:
            return JSONResponse({"error": str(e)}, status_code=404)
        if training_manager is not None:
            training_manager.record_action("instructor", "scenario-start", {"scenario_id": scenario_id})
            training_manager.consume_baseline()
        return JSONResponse(scenario_engine.status())

    @app.post("/api/scenarios/stop")
    async def stop_scenario() -> JSONResponse:
        scenario_engine.stop()
        await scenario_engine.drain_priority_writes()
        transport.registry.synchronize_reliability(fault_manager)
        return JSONResponse(scenario_engine.status())

    @app.post("/api/scenarios/reset")
    async def reset_scenario() -> JSONResponse:
        scenario_engine.reset()
        await scenario_engine.drain_priority_writes()
        transport.registry.synchronize_reliability(fault_manager)
        return JSONResponse(scenario_engine.status())

    @app.get("/api/scenarios/status/current")
    def scenario_status() -> JSONResponse:
        return JSONResponse(scenario_engine.status())

    @app.get("/api/cov/subscriptions")
    async def cov_subscriptions() -> JSONResponse:
        """
        Live COV subscription table for the dashboard -- lets an instructor
        SHOW the difference between WebCTRL's three refresh strategies:
        polling (no subscription appears here), unconfirmed COV, and
        confirmed COV. Reads bacpypes3's internal detection map directly.
        """
        bacnet_app = transport.app
        if bacnet_app is None:
            return JSONResponse([])

        # reverse map: (object type value, global instance) -> registered point
        by_identifier = {
            (rp.config.object_type.value, rp.global_instance): (key, rp)
            for key, rp in transport.registry.all_points().items()
        }
        loop_time = asyncio.get_running_loop().time()
        result = []
        for obj_id, detection in bacnet_app._cov_detections.items():
            obj_type, obj_instance = obj_id
            point_key, rp = by_identifier.get((str(obj_type), obj_instance), (None, None))
            for cov in detection.cov_subscriptions:
                remaining = None
                if cov.cancel_handle is not None:
                    remaining = max(0, round(cov.cancel_handle.when() - loop_time))
                result.append(
                    {
                        "object": f"{obj_type}:{obj_instance}",
                        "point": point_key,
                        "object_name": rp.config.object_name if rp else None,
                        "subscriber": str(cov.client_addr),
                        "process_id": cov.proc_id,
                        "mode": "confirmed" if cov.confirmed else "unconfirmed",
                        "lifetime_seconds": cov.lifetime,
                        "seconds_remaining": remaining,
                    }
                )
        return JSONResponse(result)

    @app.get("/api/logs/app")
    def logs_app(limit: int = Query(default=100, ge=1, le=500)) -> JSONResponse:
        return JSONResponse(list(recent_app_events)[-limit:])

    @app.get("/api/logs/bacnet")
    def logs_bacnet(limit: int = Query(default=100, ge=1, le=500)) -> JSONResponse:
        return JSONResponse(list(recent_bacnet_traffic)[-limit:])

    # ---- Phase 6a: LLM orchestration ------------------------------------

    @app.get("/api/llm/status")
    async def llm_status() -> JSONResponse:
        connected = await ollama_client.test_connection()
        models: list[str] = []
        error = None
        if connected:
            try:
                models = await ollama_client.list_models()
            except OllamaConnectionError as e:
                error = str(e)
        return JSONResponse(
            {
                "connected": connected,
                "host": ollama_client.host,
                "configured_model": ollama_client.model,
                "available_models": models,
                "error": error,
            }
        )

    @app.post("/api/llm/propose")
    async def llm_propose(req: LlmProposeRequest) -> JSONResponse:
        request_id = f"req-{uuid.uuid4().hex[:12]}"
        result = await orchestration_service.propose(req.instructor_request, request_id)
        if result.error:
            return JSONResponse({"error": result.error, "request_id": request_id}, status_code=502)
        proposal_token = None
        if result.validation.valid:
            proposal_token = secrets.token_urlsafe(24)
            app.state.pending_llm_proposals[proposal_token] = {
                "digest": _bundle_digest(result.bundle),
                "expires_at": time.time() + 600,
                "request_id": request_id,
            }
        return JSONResponse(
            {
                "bundle": result.bundle.model_dump(),
                "valid": result.validation.valid,
                "validation_errors": result.validation.errors,
                "proposal_token": proposal_token,
                "proposal_expires_in_seconds": 600 if proposal_token else None,
            }
        )

    @app.post("/api/llm/apply")
    async def llm_apply(req: LlmApplyRequest) -> JSONResponse:
        try:
            bundle = LlmActionBundle.model_validate(req.bundle)
        except Exception as e:  # noqa: BLE001 - malformed client payload, not a server error
            return JSONResponse({"error": f"malformed action bundle: {e}"}, status_code=400)
        proposal = app.state.pending_llm_proposals.get(req.proposal_token)
        if proposal is None:
            return JSONResponse(
                {"error": "proposal approval token is missing, invalid, or already used"},
                status_code=403,
            )
        if proposal["expires_at"] < time.time():
            app.state.pending_llm_proposals.pop(req.proposal_token, None)
            return JSONResponse({"error": "proposal approval token has expired"}, status_code=403)
        if not hmac.compare_digest(proposal["digest"], _bundle_digest(bundle)):
            return JSONResponse(
                {"error": "action bundle does not match the approved proposal"},
                status_code=403,
            )
        # Consume before execution so refreshes or local callers cannot replay it.
        app.state.pending_llm_proposals.pop(req.proposal_token, None)
        result = orchestration_service.apply(bundle)
        status_code = 200 if result.applied else 422
        return JSONResponse(
            {"applied": result.applied, "action_results": result.action_results, "error": result.error},
            status_code=status_code,
        )

    @app.get("/api/llm/audit")
    def llm_audit(limit: int = 50) -> JSONResponse:
        return JSONResponse(orchestration_service.audit_service.recent(limit))

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app
