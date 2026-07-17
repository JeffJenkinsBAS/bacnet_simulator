"""
FastAPI application: the Basic UI, extended in Phase 4 with fault
injection and scenario control endpoints for the Instructor Panel, and in
Phase 6a with LLM orchestration endpoints. Still deliberately simple:
polling-based REST endpoints, one static HTML dashboard, no auth (isolated
training bench only) -- see HANDOFF.md's open question about whether that
still holds once Phase 6d (dynamic equipment) is unlocked; not revisited
for 6a since 6a's action set doesn't touch the BACnet object model.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.engine import SimulationEngine
from app.faults import FaultManager, FaultType
from app.llm.action_schema import LlmActionBundle
from app.llm.ollama_client import OllamaClient, OllamaConnectionError
from app.logging_setup import recent_app_events, recent_bacnet_traffic
from app.scenario import ScenarioEngine
from app.services.orchestration_service import OrchestrationService
from app.transport import BacnetTransport

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class SetFaultRequest(BaseModel):
    fault_id: Optional[str] = None
    fault_type: str
    group_id: Optional[str] = None
    alias: Optional[str] = None
    parameters: dict[str, Any] = {}


class ForceValueRequest(BaseModel):
    group_id: str
    alias: str
    value: Any = None


class LlmProposeRequest(BaseModel):
    instructor_request: str


class LlmApplyRequest(BaseModel):
    bundle: dict[str, Any]  # the LlmActionBundle exactly as returned by /api/llm/propose


def create_app(
    transport: BacnetTransport, engine: SimulationEngine,
    fault_manager: FaultManager, scenario_engine: ScenarioEngine,
    orchestration_service: OrchestrationService, ollama_client: OllamaClient,
) -> FastAPI:
    app = FastAPI(title="ACI BACnet Building Simulation Platform")
    app.state.transport = transport
    app.state.engine = engine
    app.state.fault_manager = fault_manager
    app.state.scenario_engine = scenario_engine
    app.state.orchestration_service = orchestration_service
    app.state.ollama_client = ollama_client
    app.state.start_time = time.time()

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/status")
    def status() -> JSONResponse:
        bacnet_app = transport.app
        registry = transport.registry
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
                    "last_command_received": bacnet_app.last_command_received if bacnet_app else None,
                },
                "fleet": {
                    "group_count": len(groups_summary),
                    "total_point_count": len(registry.all_points()),
                },
                "groups": groups_summary,
                "active_fault_count": len(fault_manager.list_faults()),
                "scenario": scenario_engine.status(),
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
                    "interlock": cfg.interlock,
                    "present_value": registry._get(f"{rp.group_id}.{alias}"),
                    "normal_range": cfg.normal_range.model_dump() if cfg.normal_range else None,
                    "active_faults": [f.fault_type.value for f in faults_here],
                    "forced": any(f.fault_id.startswith("force:") for f in faults_here),
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

    # NOTE: every endpoint that starts the engine loop or triggers a BACnet
    # priority-array write (force/release, scenarios) MUST be `async def` so
    # it runs on the event loop. A sync def runs in FastAPI's threadpool,
    # where asyncio.create_task/ensure_future raise "no running event loop"
    # -- this bit us live: engine.start() 500'd and left a corrupted flag.

    @app.post("/api/simulation/start")
    async def start_simulation() -> JSONResponse:
        engine.start()
        return JSONResponse(engine.status())

    @app.post("/api/simulation/stop")
    async def stop_simulation() -> JSONResponse:
        engine.stop()
        return JSONResponse(engine.status())

    @app.post("/api/simulation/speed/{multiplier}")
    async def set_speed(multiplier: float) -> JSONResponse:
        engine.speed_multiplier = max(0.1, min(60.0, multiplier))
        return JSONResponse(engine.status())

    @app.post("/api/simulation/stop-all")
    async def stop_all_simulation() -> JSONResponse:
        """Prominent Stop-All-Simulation control: stops the engine AND clears every active fault/force/scenario."""
        engine.stop()
        scenario_engine.reset()
        return JSONResponse({"simulation": engine.status(), "faults_cleared": True})

    @app.post("/api/site/weather")
    def set_weather(oa_temp_f: float | None = None, oa_humidity_pct: float | None = None) -> JSONResponse:
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
    def set_fault(req: SetFaultRequest) -> JSONResponse:
        try:
            fault_type = FaultType(req.fault_type)
        except ValueError:
            return JSONResponse({"error": f"unknown fault_type '{req.fault_type}'"}, status_code=400)
        fault_id = req.fault_id or f"manual:{req.group_id}.{req.alias}.{req.fault_type}"
        instance = fault_manager.set_fault(fault_id, fault_type, req.group_id, req.alias, req.parameters)
        return JSONResponse({"fault_id": instance.fault_id, "fault_type": instance.fault_type.value})

    @app.post("/api/faults/clear")
    def clear_fault(fault_id: str) -> JSONResponse:
        cleared = fault_manager.clear_fault(fault_id)
        return JSONResponse({"cleared": cleared})

    @app.post("/api/faults/clear-all")
    def clear_all_faults() -> JSONResponse:
        fault_manager.clear_all()
        return JSONResponse({"cleared": True})

    @app.post("/api/force")
    async def force_value(req: ForceValueRequest) -> JSONResponse:
        scenario_engine._apply_force_or_release(req.group_id, req.alias, "set_value", req.value)
        return JSONResponse({"forced": True, "group_id": req.group_id, "alias": req.alias, "value": req.value})

    @app.post("/api/release")
    async def release_value(req: ForceValueRequest) -> JSONResponse:
        scenario_engine._apply_force_or_release(req.group_id, req.alias, "release_value", None)
        return JSONResponse({"released": True, "group_id": req.group_id, "alias": req.alias})

    # ---- Phase 4: scenarios ---------------------------------------------

    @app.get("/api/scenarios")
    def list_scenarios() -> JSONResponse:
        return JSONResponse(
            [
                {
                    "scenario_id": s.scenario_id, "title": s.title, "description": s.description,
                    "event_count": len(s.events), "student_objectives": s.student_objectives,
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
        try:
            scenario_engine.start(scenario_id)
        except KeyError as e:
            return JSONResponse({"error": str(e)}, status_code=404)
        return JSONResponse(scenario_engine.status())

    @app.post("/api/scenarios/stop")
    async def stop_scenario() -> JSONResponse:
        scenario_engine.stop()
        return JSONResponse(scenario_engine.status())

    @app.post("/api/scenarios/reset")
    async def reset_scenario() -> JSONResponse:
        scenario_engine.reset()
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
    def logs_app(limit: int = 100) -> JSONResponse:
        return JSONResponse(list(recent_app_events)[-limit:])

    @app.get("/api/logs/bacnet")
    def logs_bacnet(limit: int = 100) -> JSONResponse:
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
        return JSONResponse(
            {
                "bundle": result.bundle.model_dump(),
                "valid": result.validation.valid,
                "validation_errors": result.validation.errors,
            }
        )

    @app.post("/api/llm/apply")
    async def llm_apply(req: LlmApplyRequest) -> JSONResponse:
        try:
            bundle = LlmActionBundle.model_validate(req.bundle)
        except Exception as e:  # noqa: BLE001 - malformed client payload, not a server error
            return JSONResponse({"error": f"malformed action bundle: {e}"}, status_code=400)
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
