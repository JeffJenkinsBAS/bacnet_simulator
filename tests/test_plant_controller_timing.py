"""Local chiller/boiler controller timing and BACnet telemetry regressions."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from bacpypes3.basetypes import BinaryPV

from app.config_models import EquipmentGroupConfig
from app.equipment.boiler import BoilerModel, BoilerParameters
from app.equipment.chiller import ChillerModel, ChillerParameters
from app.main import build_application
from app.registry import PointRegistry


CONFIG_DIR = Path(__file__).resolve().parent.parent / "config" / "devices"


def _group(filename: str) -> EquipmentGroupConfig:
    return EquipmentGroupConfig.model_validate(
        json.loads((CONFIG_DIR / filename).read_text())
    )


async def _write(registry: PointRegistry, group: str, alias: str, value: bool) -> None:
    point = registry.all_points()[f"{group}.{alias}"].bacnet_object
    await point.write_property(
        "presentValue",
        BinaryPV("active" if value else "inactive"),
        priority=8,
    )


async def _ready_chiller(parameters: ChillerParameters):
    registry = PointRegistry(
        [_group("site.json"), _group("chw_plant.json"), _group("chiller_1.json")]
    )
    registry.build_objects()
    chiller = ChillerModel(
        "ACI-SIM-CHILLER-1",
        registry.view("ACI-SIM-CHILLER-1"),
        registry.view("ACI-SIM-SITE"),
        registry.view("ACI-SIM-CHW-PLANT"),
        parameters=parameters,
    )
    chiller.set_evaporator_conditions(return_temp_f=58.0, flow_gpm=300.0)
    for alias in ("chiller_ss", "chw_iso_valve", "chw_pump_ss", "cw_pump_ss"):
        await _write(registry, "ACI-SIM-CHILLER-1", alias, True)
    return registry, chiller


@pytest.mark.asyncio
async def test_chiller_minimum_run_then_anti_recycle_and_second_start() -> None:
    registry, chiller = await _ready_chiller(
        ChillerParameters(
            start_delay_seconds=2.0,
            minimum_run_seconds=8.0,
            minimum_off_seconds=6.0,
            pump_start_delay_seconds=1.0,
            isolation_valve_time_constant_seconds=1.0,
        )
    )
    unit = registry.view("ACI-SIM-CHILLER-1")

    for _ in range(6):
        chiller.tick(1.0)
    assert chiller.proven
    assert unit.get("operating_state") == 3.0
    assert unit.get("start_count") == 1.0

    await _write(registry, "ACI-SIM-CHILLER-1", "chiller_ss", False)
    chiller.tick(1.0)
    assert chiller.proven
    assert unit.get("minimum_run_hold_active") == 1.0
    assert unit.get("operating_state") == 4.0

    for _ in range(10):
        chiller.tick(1.0)
        if not chiller.proven:
            break
    assert not chiller.proven

    await _write(registry, "ACI-SIM-CHILLER-1", "chiller_ss", True)
    chiller.tick(1.0)
    assert not chiller.proven
    assert unit.get("anti_recycle_active") == 1.0
    assert unit.get("operating_state") == 1.0
    assert 0.0 < unit.get("minimum_off_remaining") <= 6.0

    for _ in range(12):
        chiller.tick(1.0)
        if chiller.proven:
            break
    assert chiller.proven
    assert unit.get("start_count") == 2.0


@pytest.mark.asyncio
async def test_chiller_safety_interlock_overrides_minimum_run_hold() -> None:
    registry, chiller = await _ready_chiller(
        ChillerParameters(
            start_delay_seconds=2.0,
            minimum_run_seconds=100.0,
            minimum_off_seconds=6.0,
            pump_start_delay_seconds=1.0,
            isolation_valve_time_constant_seconds=1.0,
        )
    )
    unit = registry.view("ACI-SIM-CHILLER-1")
    for _ in range(6):
        chiller.tick(1.0)
    await _write(registry, "ACI-SIM-CHILLER-1", "chiller_ss", False)
    chiller.tick(1.0)
    assert unit.get("minimum_run_hold_active") == 1.0

    await _write(registry, "ACI-SIM-CHW-PLANT", "emerg_shutdown_trip", True)
    chiller.tick(1.0)
    assert not chiller.proven
    assert unit.get("minimum_run_hold_active") == 0.0
    assert unit.get("operating_state") == 5.0


@pytest.mark.asyncio
async def test_boiler_minimum_run_then_anti_cycle_and_permissive_telemetry() -> None:
    registry = PointRegistry([_group("boiler_1.json")])
    registry.build_objects()
    boiler = BoilerModel(
        "ACI-SIM-BOILER-1",
        registry.view("ACI-SIM-BOILER-1"),
        parameters=BoilerParameters(
            purge_seconds=2.0,
            ignition_seconds=1.0,
            minimum_run_seconds=8.0,
            minimum_off_seconds=6.0,
            pump_start_delay_seconds=1.0,
        ),
    )
    unit = registry.view("ACI-SIM-BOILER-1")
    await _write(registry, "ACI-SIM-BOILER-1", "circ_pump_ss", True)
    await _write(registry, "ACI-SIM-BOILER-1", "boiler_ss", True)
    for _ in range(6):
        boiler.tick(1.0)
    assert boiler.proven
    assert unit.get("start_permissive") == 1.0
    assert unit.get("start_count") == 1.0

    await _write(registry, "ACI-SIM-BOILER-1", "boiler_ss", False)
    boiler.tick(1.0)
    assert boiler.proven
    assert unit.get("minimum_run_hold_active") == 1.0

    for _ in range(10):
        boiler.tick(1.0)
        if not boiler.proven:
            break
    assert not boiler.proven

    await _write(registry, "ACI-SIM-BOILER-1", "boiler_ss", True)
    boiler.tick(1.0)
    assert unit.get("anti_recycle_active") == 1.0
    assert unit.get("operating_state") == 1.0

    await _write(registry, "ACI-SIM-BOILER-1", "circ_pump_ss", False)
    for _ in range(3):
        boiler.tick(1.0)
    assert unit.get("start_permissive") == 0.0
    assert unit.get("operating_state") == 6.0


def test_timing_parameters_reject_negative_values() -> None:
    with pytest.raises(ValueError, match="minimum_off_seconds"):
        ChillerParameters(minimum_off_seconds=-1.0)
    with pytest.raises(ValueError, match="minimum_run_seconds"):
        BoilerParameters(minimum_run_seconds=-1.0)


@pytest.mark.asyncio
async def test_application_factory_wires_unit_model_parameters_from_config() -> None:
    api, transport, _engine, _faults, _scenarios = build_application()
    transport.registry.build_objects()
    equipment = {item.equipment_id: item for item in api.state.equipment_factory()}

    chiller = equipment["ACI-SIM-CHILLER-1"]
    boiler = equipment["ACI-SIM-BOILER-1"]
    assert chiller.params.start_delay_seconds == 45.0
    assert chiller.params.minimum_run_seconds == 180.0
    assert chiller.params.minimum_off_seconds == 300.0
    assert boiler.params.purge_seconds == 30.0
    assert boiler.params.ignition_seconds == 5.0
    assert boiler.params.minimum_run_seconds == 120.0
    assert boiler.params.minimum_off_seconds == 60.0
