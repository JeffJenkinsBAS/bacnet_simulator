"""
Entry point. Run with:

    python -m app.main

Single-device architecture: every equipment group's objects live under ONE
BACnet device on UDP 47808. This module loads every equipment group config,
validates global object-instance uniqueness across all of them, builds ONE
PointRegistry containing every object, starts ONE BacnetTransport, and
wires each equipment model to a GroupView scoped to its own group.

Phase 4 adds a FaultManager (threaded through every GroupView and the
transport layer) and a ScenarioEngine (loaded from config/scenarios/*.json,
ticked once per simulation loop).

Phase 6a adds an OrchestrationService (Ollama client + action validation +
audit trail) wired to the SAME FaultManager/ScenarioEngine instances the
Instructor Panel already uses -- an LLM-proposed fault or scenario goes
through the identical code path as one an instructor triggers by hand. None
of this required any change to the equipment model files or the BACnet
transport layer -- see faults.py, scenario.py, and
app/services/orchestration_service.py docstrings for why.
"""
from __future__ import annotations

import asyncio
import glob
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from app.api import create_app
from app.config_models import EquipmentGroupConfig, NetworkConfig, SupervisoryDeviceConfig, validate_equipment_groups
from app.diagnostics import CommandCenterDiagnostics
from app.engine import SimulationEngine
from app.equipment.ahu import AhuModel, AhuParameters
from app.equipment.boiler import BoilerModel
from app.equipment.chiller import ChillerModel
from app.equipment.exhaust_fan import ExhaustFanModel
from app.equipment.managers import BoilerManagerModel, ChwPlantManagerModel
from app.equipment.site import SiteModel
from app.equipment.vav_single_duct import SingleDuctVavModel, VavParameters
from app.faults import FaultManager
from app.llm.ollama_client import OllamaClient
from app.logging_setup import configure_logging
from app.registry import PointRegistry
from app.scenario import ScenarioEngine
from app.services.audit_service import AuditService
from app.services.orchestration_service import OrchestrationService
from app.transport import BacnetTransport
from app.training import TrainingAuth, TrainingManager

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
logger = logging.getLogger("aci_sim.main")


def load_or_create_training_pin() -> str:
    """Use an environment override or a stable local-only generated PIN."""
    configured = os.getenv("ACI_SIM_INSTRUCTOR_PIN")
    if configured:
        return configured
    pin_path = Path(__file__).resolve().parent.parent / "logs" / "training-instructor-pin.txt"
    pin_path.parent.mkdir(parents=True, exist_ok=True)
    if pin_path.exists():
        return pin_path.read_text(encoding="utf-8").strip()
    import secrets

    pin = f"{secrets.randbelow(1_000_000):06d}"
    pin_path.write_text(pin + "\n", encoding="utf-8")
    logger.warning("Generated local training instructor PIN at %s", pin_path)
    return pin


def load_network_config() -> NetworkConfig:
    with open(CONFIG_DIR / "network.json") as f:
        return NetworkConfig.model_validate(json.load(f))


def load_supervisory_config() -> SupervisoryDeviceConfig:
    with open(CONFIG_DIR / "supervisory_device.json") as f:
        return SupervisoryDeviceConfig.model_validate(json.load(f))


def load_building_layout() -> dict:
    with open(CONFIG_DIR / "building_layout.json") as f:
        return json.load(f)


def load_all_equipment_groups() -> list[EquipmentGroupConfig]:
    groups: list[EquipmentGroupConfig] = []
    for path in sorted(glob.glob(str(CONFIG_DIR / "devices" / "*.json"))):
        with open(path) as f:
            groups.append(EquipmentGroupConfig.model_validate(json.load(f)))
    validate_equipment_groups(groups)
    logger.info("Loaded and validated %d equipment groups (%d total objects)", len(groups), sum(len(g.points) for g in groups))
    return groups


async def check_for_duplicate_instance(transport: BacnetTransport) -> None:
    """Best-effort duplicate device-instance detection -- see Phase 1 architecture, Network Safety."""
    if transport.app is None:
        return
    if transport.network_config.bind_address.startswith("127."):
        # On loopback the /24 broadcast target (127.0.0.255) is meaningless --
        # no other host can exist -- and on Windows the broadcast SEND poisons
        # the asyncio datagram transport: the socket stays bound but bacpypes3
        # never receives another packet ("deaf device", found live 2026-07-17
        # when every uvicorn-hosted instance ignored all BACnet traffic while
        # a standalone transport answered fine). Skip; the check still runs on
        # a real bench NIC.
        logger.info(
            "Skipping duplicate device-instance check on loopback bind %s "
            "(broadcast Who-Is is meaningless there and kills the UDP transport on Windows)",
            transport.network_config.bind_address,
        )
        return
    instance = transport.supervisory_config.device_instance
    try:
        responses = await transport.app.who_is(low_limit=instance, high_limit=instance, timeout=2.0)
    except Exception:  # noqa: BLE001 - never let the startup check crash the app
        logger.exception("Duplicate device-instance check failed to complete for instance %d", instance)
        return
    sources = {str(r.pduSource) for r in (responses or [])}
    if len(sources) > 1:
        logger.warning(
            "STARTUP WARNING: device instance %d answered by %d different sources (%s) -- "
            "looks like a duplicate BACnet device instance.",
            instance,
            len(sources),
            ", ".join(sources),
        )
    else:
        logger.info("Duplicate device-instance check passed for instance %d", instance)


def build_equipment(registry: PointRegistry, fault_manager: FaultManager) -> list:
    """
    Wires every equipment model to a GroupView of the shared registry
    (scoped to that model's own group_id, and now also fault-aware) plus
    whatever cross-group references it needs.
    """
    site_view = registry.view("ACI-SIM-SITE", fault_manager=fault_manager)
    site = SiteModel("ACI-SIM-SITE", site_view)

    plant_view = registry.view("ACI-SIM-CHW-PLANT", fault_manager=fault_manager)
    chillers = []
    for n in (1, 2, 3):
        chillers.append(
            ChillerModel(
                f"ACI-SIM-CHILLER-{n}", registry.view(f"ACI-SIM-CHILLER-{n}", fault_manager=fault_manager),
                site_registry=site_view, plant_registry=plant_view,
            )
        )
    boiler_mgr_view = registry.view("ACI-SIM-BOILER-MGR", fault_manager=fault_manager)
    boilers = []
    for n in (1, 2, 3):
        boilers.append(
            BoilerModel(
                f"ACI-SIM-BOILER-{n}", registry.view(f"ACI-SIM-BOILER-{n}", fault_manager=fault_manager),
                manager_registry=boiler_mgr_view, manager_enable_alias=f"enable_boiler{n}",
            )
        )
    # Managers tick after their units and expose typed physical loop state to
    # the downstream AHU and VAV models.
    chw_manager = ChwPlantManagerModel(
        "ACI-SIM-CHW-PLANT",
        plant_view,
        chillers,
        site_registry=site_view,
    )
    boiler_manager = BoilerManagerModel(
        "ACI-SIM-BOILER-MGR",
        boiler_mgr_view,
        boilers,
        site_registry=site_view,
    )

    ahu = AhuModel(
        "ACI-SIM-AHU-1",
        registry.view("ACI-SIM-AHU-1", fault_manager=fault_manager),
        site_registry=site_view,
        parameters=AhuParameters(),
        chw_plant_model=chw_manager,
        boiler_plant_model=boiler_manager,
    )
    # The plant supplies the AHU immediately; the AHU's completed coil load
    # feeds the parent header/chillers on the next one-second thermal tick.
    chw_manager.set_cooling_coils([ahu])
    exhaust_fan = ExhaustFanModel(
        "ACI-SIM-EF-1",
        registry.view("ACI-SIM-EF-1", fault_manager=fault_manager),
        site_registry=site_view,
        ahu_model=ahu,
    )

    vavs = []
    group_configs = {group.group_id: group for group in registry.groups}

    def vav_parameters(group_id: str) -> VavParameters:
        configured = group_configs[group_id].model_parameters
        try:
            return VavParameters(**configured)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{group_id} contains invalid VAV model_parameters: {exc}"
            ) from exc

    # VAV-1/VAV-2: real physical controllers, no simulated Zone Temp.
    for n in (1, 2):
        group_id = f"ACI-SIM-VAV-{n}"
        vavs.append(
            SingleDuctVavModel(
                group_id,
                registry.view(group_id, fault_manager=fault_manager),
                parameters=vav_parameters(group_id),
                has_physical_zone_sensor=True, ahu_model=ahu,
                boiler_plant_model=boiler_manager,
            )
        )

    # VAV-3..17: virtual zones, simulated Zone Temp included.
    for n in range(3, 18):
        group_id = f"ACI-SIM-VAV-{n}"
        vavs.append(
            SingleDuctVavModel(
                group_id,
                registry.view(group_id, fault_manager=fault_manager),
                parameters=vav_parameters(group_id),
                has_physical_zone_sensor=False, ahu_model=ahu,
                boiler_plant_model=boiler_manager,
            )
        )

    # AHU-1 reads the previous tick's effective terminal positions to model
    # the common-duct resistance seen by its static-pressure sensor. The VAVs
    # then consume the AHU's newly calculated pressure later in the same tick.
    ahu.set_vav_models(vavs)
    # Every hot-water coil contributes its water demand and air-side heat
    # transfer to the common distribution-loop pressure/energy balance.
    boiler_manager.set_heating_coils([ahu, *vavs])

    # Physical dependency/tick order: ambient -> plants -> loop headers ->
    # air handler -> pressure trim -> terminal units/zones.
    return [
        site,
        *chillers,
        *boilers,
        chw_manager,
        boiler_manager,
        ahu,
        exhaust_fan,
        *vavs,
    ]


def load_llm_config() -> dict:
    """
    Loaded once at startup, not hot-reloaded -- see config/llm/models.json's
    own _note for why (no Settings GUI to edit it from yet, Phase 6b).
    Missing/malformed file falls back to Ollama's own defaults rather than
    failing startup, since Phase 6a's LLM features are additive -- the core
    simulator must come up regardless of whether Ollama is configured or
    even running.
    """
    try:
        with open(CONFIG_DIR / "llm" / "models.json") as f:
            data = json.load(f)
        return {
            "host": data.get("ollama_host", "http://localhost:11434"),
            "model": data.get("default_model", "llama3.1"),
            "timeout_seconds": data.get("timeout_seconds", 60),
        }
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning("Could not load config/llm/models.json (%s) -- using Ollama defaults", e)
        return {"host": "http://localhost:11434", "model": "llama3.1", "timeout_seconds": 60}


def build_application() -> tuple[FastAPI, BacnetTransport, SimulationEngine, FaultManager, ScenarioEngine]:
    """
    Build everything that does NOT require a running asyncio event loop.
    bacpypes3's local objects need a running loop at construction time, so
    starting the transport (which builds the complete BACnet object catalog and binds
    the single UDP port) happens later, inside the FastAPI lifespan handler.
    """
    configure_logging()
    network_config = load_network_config()
    supervisory_config = load_supervisory_config()
    groups = load_all_equipment_groups()

    registry = PointRegistry(groups)
    fault_manager = FaultManager()
    transport = BacnetTransport(network_config, supervisory_config, registry, fault_manager=fault_manager)
    diagnostics = CommandCenterDiagnostics(registry, load_building_layout())
    engine = SimulationEngine(
        equipment=[],
        fault_manager=fault_manager,
        diagnostics=diagnostics,
    )
    diagnostics.set_equipment_provider(lambda: engine.equipment)
    diagnostics.set_simulation_clock(lambda: engine.simulated_seconds_elapsed)

    scenario_engine = ScenarioEngine(
        fault_manager, registry,
        get_sim_seconds=lambda: engine.simulated_seconds_elapsed,
        get_equipment=lambda: engine.equipment,
    )
    scenario_engine.load_all(CONFIG_DIR / "scenarios")
    engine.scenario_engine = scenario_engine

    llm_config = load_llm_config()
    ollama_client = OllamaClient(
        host=llm_config["host"], model=llm_config["model"], timeout_seconds=llm_config["timeout_seconds"]
    )
    audit_service = AuditService()
    orchestration_service = OrchestrationService(
        ollama_client, registry, fault_manager, scenario_engine, audit_service
    )

    equipment_factory = lambda: build_equipment(transport.registry, fault_manager)
    training_manager = TrainingManager(
        engine=engine,
        registry=registry,
        fault_manager=fault_manager,
        scenario_engine=scenario_engine,
        equipment_factory=equipment_factory,
        baseline_path=CONFIG_DIR / "training" / "baselines.json",
        outcomes_path=CONFIG_DIR / "training" / "outcomes.json",
        auth=TrainingAuth(load_or_create_training_pin(), required=True),
        get_last_command=lambda: transport.app.last_command_received if transport.app else None,
        evidence_dir=Path(__file__).resolve().parent.parent / "artifacts" / "training",
    )
    engine.training_manager = training_manager

    api_app = create_app(
        transport,
        engine,
        fault_manager,
        scenario_engine,
        orchestration_service,
        ollama_client,
        diagnostics=diagnostics,
        equipment_factory=equipment_factory,
        training_manager=training_manager,
    )
    return api_app, transport, engine, fault_manager, scenario_engine


def create_lifespan_app() -> FastAPI:
    api_app, transport, engine, fault_manager, scenario_engine = build_application()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("=" * 70)
        logger.info(
            "ACI BACnet Building Simulation Platform starting -- 1 supervisory device, %d equipment groups, %d scenarios",
            len(transport.registry.groups), len(scenario_engine.scenarios),
        )
        logger.info("=" * 70)

        transport_started = False
        duplicate_check_task: asyncio.Task | None = None
        try:
            transport.start()
            transport_started = True
            engine.equipment.extend(build_equipment(transport.registry, fault_manager))

            if transport.network_config.startup_duplicate_instance_check:
                duplicate_check_task = asyncio.create_task(check_for_duplicate_instance(transport))

            await engine.start()
            yield
        finally:
            if duplicate_check_task is not None and not duplicate_check_task.done():
                duplicate_check_task.cancel()
                await asyncio.gather(duplicate_check_task, return_exceptions=True)
            await engine.stop()
            if transport_started:
                transport.stop()
            logger.info("Shutdown complete")

    api_app.router.lifespan_context = lifespan
    return api_app


app = create_lifespan_app()


def main() -> None:
    dashboard_host = os.environ.get("ACI_DASHBOARD_HOST", "127.0.0.1")
    try:
        dashboard_port = int(os.environ.get("ACI_DASHBOARD_PORT", "8001"))
    except ValueError as exc:
        raise SystemExit("ACI_DASHBOARD_PORT must be an integer") from exc
    if not 1 <= dashboard_port <= 65535:
        raise SystemExit("ACI_DASHBOARD_PORT must be between 1 and 65535")
    uvicorn.run(app, host=dashboard_host, port=dashboard_port, log_level="warning")


if __name__ == "__main__":
    main()
