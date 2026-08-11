"""Hydronic plant physics, sequencing, and physical-fault regressions."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from bacpypes3.basetypes import BinaryPV
from bacpypes3.primitivedata import Real

from app.config_models import EquipmentGroupConfig
from app.equipment.boiler import BoilerModel, BoilerParameters
from app.equipment.chiller import ChillerModel, ChillerParameters
from app.equipment.managers import (
    BoilerManagerModel,
    BoilerPlantParameters,
    ChwPlantManagerModel,
    ChwPlantParameters,
)
from app.faults import FaultManager, FaultType
from app.registry import PointRegistry


CONFIG_DIR = Path(__file__).resolve().parent.parent / "config" / "devices"


def _group(filename: str) -> EquipmentGroupConfig:
    return EquipmentGroupConfig.model_validate(
        json.loads((CONFIG_DIR / filename).read_text())
    )


async def _write(registry: PointRegistry, group: str, alias: str, value) -> None:
    obj = registry.all_points()[f"{group}.{alias}"].bacnet_object
    encoded = (
        BinaryPV("active" if value else "inactive")
        if isinstance(value, bool)
        else Real(float(value))
    )
    await obj.write_property("presentValue", encoded, priority=8)


def _chiller_stack(
    *,
    fault_manager: FaultManager | None = None,
    parameters: ChillerParameters | None = None,
):
    registry = PointRegistry(
        [_group("site.json"), _group("chw_plant.json"), _group("chiller_1.json")]
    )
    registry.build_objects()
    site = registry.view("ACI-SIM-SITE", fault_manager=fault_manager)
    plant = registry.view("ACI-SIM-CHW-PLANT", fault_manager=fault_manager)
    unit = registry.view("ACI-SIM-CHILLER-1", fault_manager=fault_manager)
    chiller = ChillerModel(
        "ACI-SIM-CHILLER-1",
        unit,
        site_registry=site,
        plant_registry=plant,
        parameters=parameters,
    )
    manager = ChwPlantManagerModel(
        "ACI-SIM-CHW-PLANT",
        plant,
        [chiller],
        parameters=ChwPlantParameters(flow_time_constant_seconds=3.0),
    )
    return registry, chiller, manager


async def _command_chiller(registry: PointRegistry, *, tower: bool = True) -> None:
    for alias in ("chiller_ss", "chw_iso_valve", "chw_pump_ss", "cw_pump_ss"):
        await _write(registry, "ACI-SIM-CHILLER-1", alias, True)
    await _write(registry, "ACI-SIM-CHILLER-1", "ct_fan_ss", tower)


@pytest.mark.asyncio
async def test_chw_flow_ramps_and_bypass_diverts_flow_around_evaporator() -> None:
    registry, chiller, manager = _chiller_stack(
        parameters=ChillerParameters(start_delay_seconds=10.0)
    )
    await _command_chiller(registry)
    await _write(registry, "ACI-SIM-CHILLER-1", "byp_vlv_output", 100.0)

    for _ in range(5):
        chiller.tick(1.0)
        manager.tick(1.0)
    assert 0.0 < manager.flow_gpm < 300.0

    for _ in range(90):
        chiller.tick(1.0)
        manager.tick(1.0)
    assert manager.flow_gpm == pytest.approx(300.0, abs=0.1)
    assert chiller._evaporator_flow_gpm < 1.0
    assert not chiller.proven

    await _write(registry, "ACI-SIM-CHILLER-1", "byp_vlv_output", 0.0)
    for _ in range(90):
        chiller.tick(1.0)
        manager.tick(1.0)
    assert chiller._evaporator_flow_gpm > 290.0
    assert chiller.proven


@pytest.mark.asyncio
async def test_tower_vfd_speed_and_cw_pump_change_condenser_response() -> None:
    registry, chiller, _ = _chiller_stack(
        parameters=ChillerParameters(
            start_delay_seconds=3.0,
            tower_time_constant_seconds=3.0,
        )
    )
    await _command_chiller(registry)
    await _write(registry, "ACI-SIM-CHILLER-1", "ct_vfd_output", 100.0)
    # Unit-only operation uses the documented standalone part-load fallback.
    for _ in range(120):
        chiller.tick(1.0)
    full_speed_cws = chiller.registry.get("cws_temp")
    full_speed_delta = chiller.registry.get("cwr_temp") - full_speed_cws

    await _write(registry, "ACI-SIM-CHILLER-1", "ct_vfd_output", 0.0)
    for _ in range(120):
        chiller.tick(1.0)
    minimum_speed_cws = chiller.registry.get("cws_temp")
    assert minimum_speed_cws > full_speed_cws + 5.0
    assert full_speed_delta > 4.5  # compressor work is included in rejected heat

    await _write(registry, "ACI-SIM-CHILLER-1", "cw_pump_ss", False)
    await _write(registry, "ACI-SIM-CHILLER-1", "ct_vfd_output", 100.0)
    for _ in range(120):
        chiller.tick(1.0)
    assert chiller.registry.get("cws_temp") == pytest.approx(70.0, abs=0.2)
    assert chiller.registry.get("cwr_temp") == pytest.approx(70.0, abs=0.2)


@pytest.mark.asyncio
async def test_compressor_capacity_ramps_instead_of_instant_full_load() -> None:
    registry, chiller, _ = _chiller_stack(
        parameters=ChillerParameters(
            start_delay_seconds=3.0,
            compressor_loading_time_constant_seconds=30.0,
        )
    )
    chiller.set_evaporator_conditions(return_temp_f=60.0, flow_gpm=300.0)
    await _command_chiller(registry)
    for _ in range(8):
        chiller.tick(1.0)
    early_output = chiller.evaporator_heat_removed_btuh
    assert chiller.proven
    assert 0.0 < early_output < 500_000.0

    for _ in range(120):
        chiller.tick(1.0)
    assert chiller.evaporator_heat_removed_btuh > 1_400_000.0
    assert chiller.evaporator_heat_removed_btuh > early_output * 3.0


@pytest.mark.asyncio
async def test_high_head_latches_until_cooled_and_manager_reset() -> None:
    registry, chiller, _ = _chiller_stack(
        parameters=ChillerParameters(
            start_delay_seconds=3.0,
            high_head_trip_f=84.0,
            high_head_reset_f=75.0,
            tower_time_constant_seconds=3.0,
        )
    )
    await _command_chiller(registry, tower=False)
    for _ in range(120):
        chiller.tick(1.0)
    assert chiller.safety_lockout
    assert not chiller.proven

    await _write(registry, "ACI-SIM-CHILLER-1", "ct_fan_ss", True)
    await _write(registry, "ACI-SIM-CHILLER-1", "ct_vfd_output", 100.0)
    for _ in range(120):
        chiller.tick(1.0)
    assert chiller.registry.get("cwr_temp") < 75.0
    assert chiller.safety_lockout

    await _write(registry, "ACI-SIM-CHILLER-1", "manager_reset", True)
    chiller.tick(1.0)
    assert not chiller.safety_lockout
    for _ in range(5):
        chiller.tick(1.0)
    assert chiller.proven


@pytest.mark.asyncio
async def test_forced_false_unit_proof_inhibits_physical_capacity() -> None:
    faults = FaultManager()
    registry, chiller, manager = _chiller_stack(
        fault_manager=faults,
        parameters=ChillerParameters(start_delay_seconds=3.0),
    )
    manager.set_cooling_coils([type("Load", (), {"cooling_coil_load_btuh": 200_000.0})()])
    await _command_chiller(registry)
    faults.set_fault(
        "failed-compressor",
        FaultType.forced_status,
        "ACI-SIM-CHILLER-1",
        "chiller_status",
        {"value": False},
    )
    for _ in range(90):
        chiller.tick(1.0)
        manager.tick(1.0)
    assert not chiller.proven
    assert chiller.evaporator_heat_removed_btuh == 0.0
    assert manager.operating_snapshot()["refrigeration_btuh"] == 0.0

    faults.clear_fault("failed-compressor")
    for _ in range(90):
        chiller.tick(1.0)
        manager.tick(1.0)
    assert chiller.proven
    assert chiller.evaporator_heat_removed_btuh > 0.0


@pytest.mark.asyncio
async def test_boiler_primary_flow_is_decoupled_from_secondary_distribution() -> None:
    registry = PointRegistry([_group("boiler_mgr.json"), _group("boiler_1.json")])
    registry.build_objects()
    boiler = BoilerModel(
        "ACI-SIM-BOILER-1",
        registry.view("ACI-SIM-BOILER-1"),
        parameters=BoilerParameters(purge_seconds=3.0, ignition_seconds=2.0),
    )
    manager = BoilerManagerModel(
        "ACI-SIM-BOILER-MGR", registry.view("ACI-SIM-BOILER-MGR"), [boiler]
    )
    await _write(registry, "ACI-SIM-BOILER-1", "circ_pump_ss", True)
    await _write(registry, "ACI-SIM-BOILER-1", "boiler_ss", True)
    for _ in range(90):
        boiler.tick(1.0)
        manager.tick(1.0)

    assert manager.flow_gpm == pytest.approx(0.0, abs=0.01)
    assert boiler.flow_gpm == pytest.approx(60.0)
    assert boiler.proven
    assert boiler.heat_output_btuh > 0.0


@pytest.mark.asyncio
async def test_hydraulic_separator_bounds_supply_and_mixes_primary_return() -> None:
    class SeparatorBoiler:
        proven = True
        hw_pump_running = True
        circ_pump_running = True
        heat_output_btuh = 0.0
        hws_temp_f = 150.0

        def set_hydronic_conditions(self, return_temp_f: float, flow_gpm: float) -> None:
            self.return_temp_f = return_temp_f
            self.flow_gpm = flow_gpm

    class LowFlowLoad:
        hot_water_coil_load_btuh = 20_000.0
        return_air_temp_f = 70.0

        @staticmethod
        def hot_water_flow_at_pressure(dp_psi: float) -> float:
            return 2.0 * (max(0.0, dp_psi) / 4.0) ** 0.5

    registry = PointRegistry([_group("boiler_mgr.json")])
    registry.build_objects()
    boiler = SeparatorBoiler()
    manager = BoilerManagerModel(
        "ACI-SIM-BOILER-MGR",
        registry.view("ACI-SIM-BOILER-MGR"),
        [boiler],
        parameters=BoilerPlantParameters(
            header_time_constant_seconds=2.0,
            hydraulic_time_constant_seconds=1.0,
        ),
    )
    manager.set_heating_coils([LowFlowLoad()])

    for _ in range(300):
        manager.tick(1.0)

    assert manager.flow_gpm < boiler.flow_gpm / 5.0
    assert manager.supply_temp_f <= boiler.hws_temp_f + 0.25
    assert manager.supply_temp_f > manager.return_temp_f
    snapshot = manager.operating_snapshot()
    measured_secondary_heat = (
        500.0
        * manager.flow_gpm
        * (manager.supply_temp_f - manager.return_temp_f)
    )
    modeled_secondary_sink = (
        snapshot["heating_load_btuh"] + snapshot["ambient_loss_btuh"]
    )
    assert measured_secondary_heat == pytest.approx(
        modeled_secondary_sink, rel=0.001, abs=5.0
    )
    assert snapshot["primary_to_secondary_heat_btuh"] == pytest.approx(
        measured_secondary_heat, rel=0.001, abs=5.0
    )
    # Most 150 F primary supply recirculates through the separator, so the
    # boiler entering water is much warmer than the low-flow secondary return.
    expected_primary_return = (
        manager.flow_gpm * manager.return_temp_f
        + (boiler.flow_gpm - manager.flow_gpm) * boiler.hws_temp_f
    ) / boiler.flow_gpm
    expected_primary_return += snapshot["pump_heat_btuh"] / (
        500.0 * boiler.flow_gpm
    )
    assert boiler.return_temp_f == pytest.approx(expected_primary_return, abs=0.05)
    assert boiler.return_temp_f > manager.return_temp_f


@pytest.mark.asyncio
async def test_failed_boiler_proof_and_stuck_local_start_cannot_heat_loop() -> None:
    faults = FaultManager()
    registry = PointRegistry([_group("boiler_mgr.json"), _group("boiler_1.json")])
    registry.build_objects()
    mgr_view = registry.view("ACI-SIM-BOILER-MGR", fault_manager=faults)
    unit_view = registry.view("ACI-SIM-BOILER-1", fault_manager=faults)
    boiler = BoilerModel(
        "ACI-SIM-BOILER-1",
        unit_view,
        parameters=BoilerParameters(purge_seconds=3.0, ignition_seconds=2.0),
        manager_registry=mgr_view,
        manager_enable_alias="enable_boiler1",
    )
    manager = BoilerManagerModel("ACI-SIM-BOILER-MGR", mgr_view, [boiler])
    await _write(registry, "ACI-SIM-BOILER-MGR", "enable_boiler1", True)
    await _write(registry, "ACI-SIM-BOILER-1", "boiler_ss", True)
    await _write(registry, "ACI-SIM-BOILER-1", "circ_pump_ss", True)
    faults.set_fault(
        "ignition-chain-open",
        FaultType.stuck_value,
        "ACI-SIM-BOILER-1",
        "boiler_ss",
        {"value": False},
    )
    for _ in range(60):
        boiler.tick(1.0)
        manager.tick(1.0)
    assert not boiler.proven
    assert boiler.heat_output_btuh == 0.0

    faults.clear_fault("ignition-chain-open")
    for _ in range(30):
        boiler.tick(1.0)
        manager.tick(1.0)
    assert boiler.proven
    assert boiler.heat_output_btuh > 0.0

    faults.set_fault(
        "failed-proof",
        FaultType.forced_status,
        "ACI-SIM-BOILER-1",
        "boiler_ok",
        {"value": False},
    )
    boiler.tick(1.0)
    manager.tick(1.0)
    assert not boiler.proven
    assert boiler.heat_output_btuh == 0.0
    assert manager.operating_snapshot()["boiler_heat_btuh"] == 0.0
