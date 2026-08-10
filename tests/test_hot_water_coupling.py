"""Hot-water loop hydraulics and first-law energy regressions."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from bacpypes3.basetypes import BinaryPV
from bacpypes3.primitivedata import Real

from app.config_models import EquipmentGroupConfig
from app.equipment.boiler import BoilerModel
from app.equipment.managers import BoilerManagerModel, BoilerPlantParameters
from app.equipment.vav_single_duct import SingleDuctVavModel, VavParameters
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
        else Real(value)
    )
    await obj.write_property("presentValue", encoded, priority=8)


class _FakeBoiler:
    proven = False
    hw_pump_running = True
    circ_pump_running = True
    heat_output_btuh = 0.0

    def set_hydronic_conditions(self, return_temp_f: float, flow_gpm: float) -> None:
        self.return_temp_f = return_temp_f
        self.flow_gpm = flow_gpm


class _FakeCoil:
    def __init__(self) -> None:
        self.valve_fraction = 1.0
        self.hot_water_coil_load_btuh = 50_000.0
        self.return_air_temp_f = 72.0

    def hot_water_flow_at_pressure(self, dp_psi: float) -> float:
        return 20.0 * self.valve_fraction * (max(0.0, dp_psi) / 4.0) ** 0.5


@pytest.mark.asyncio
async def test_loop_flow_pressure_return_temperature_and_pump_heat_follow_load() -> None:
    registry = PointRegistry([_group("boiler_mgr.json")])
    registry.build_objects()
    boiler = _FakeBoiler()
    coil = _FakeCoil()
    manager = BoilerManagerModel(
        "ACI-SIM-BOILER-MGR",
        registry.view("ACI-SIM-BOILER-MGR"),
        [boiler],
        parameters=BoilerPlantParameters(header_time_constant_seconds=1.0),
    )
    manager.set_heating_coils([coil])

    for _ in range(60):
        manager.tick(1.0)
    loaded = manager.operating_snapshot()
    assert loaded["flow_gpm"] > 20.0
    assert 0.0 < loaded["differential_pressure_psi"] < 12.0
    assert loaded["supply_temp_f"] > loaded["return_temp_f"]
    assert loaded["heating_load_btuh"] == 50_000.0
    assert loaded["pump_heat_btuh"] == 9_000.0
    assert boiler.flow_gpm > 20.0

    coil.valve_fraction = 0.0
    coil.hot_water_coil_load_btuh = 0.0
    for _ in range(60):
        manager.tick(1.0)
    unloaded = manager.operating_snapshot()
    assert unloaded["flow_gpm"] < loaded["flow_gpm"]
    assert unloaded["differential_pressure_psi"] > loaded["differential_pressure_psi"]


@pytest.mark.asyncio
async def test_boiler_start_proof_modulation_and_readbacks_follow_return_load() -> None:
    registry = PointRegistry([_group("boiler_1.json")])
    registry.build_objects()
    boiler = BoilerModel("ACI-SIM-BOILER-1", registry.view("ACI-SIM-BOILER-1"))
    boiler.set_hydronic_conditions(return_temp_f=140.0, flow_gpm=30.0)
    await _write(registry, "ACI-SIM-BOILER-1", "circ_pump_ss", True)
    await _write(registry, "ACI-SIM-BOILER-1", "hw_pump_ss", True)
    await _write(registry, "ACI-SIM-BOILER-1", "boiler_ss", True)

    for _ in range(180):
        boiler.tick(1.0)

    view = registry.view("ACI-SIM-BOILER-1")
    assert boiler.proven
    assert view.get("boiler_ok") == 1.0
    assert view.get("circ_pump_status") == 1.0
    assert view.get("hw_pump_status") == 1.0
    assert view.get("boiler_flow") == pytest.approx(30.0)
    assert view.get("firing_rate") > 90.0
    assert 175.0 <= view.get("hws_temp") <= 185.0
    assert boiler.heat_output_btuh > 500_000.0


@pytest.mark.asyncio
async def test_vav_reheat_conserves_water_and_air_side_energy_at_steady_state() -> None:
    registry = PointRegistry([_group("vav_1.json")])
    registry.build_objects()
    ahu = SimpleNamespace(
        available_static_pressure_inwc=1.2,
        effective_sa_temp_f=55.0,
        supply_air_available=True,
        cooling_delivery_available=False,
        conditioning_source="neutral",
        _ra_temp=72.0,
    )
    plant = SimpleNamespace(
        heating_capacity_fraction=1.0,
        supply_temp_f=180.0,
        differential_pressure_psi=4.0,
    )
    vav = SingleDuctVavModel(
        "ACI-SIM-VAV-1",
        registry.view("ACI-SIM-VAV-1"),
        parameters=VavParameters(
            damper_time_constant_seconds=2.0,
            thermal_time_constant_seconds=2.0,
            hot_water_valve_time_constant_seconds=2.0,
        ),
        ahu_model=ahu,
        boiler_plant_model=plant,
    )
    await _write(registry, "ACI-SIM-VAV-1", "damper_position_command", 25.0)
    await _write(registry, "ACI-SIM-VAV-1", "airflow_setpoint", 300.0)
    await _write(registry, "ACI-SIM-VAV-1", "hw_valve_command", 100.0)
    for _ in range(300):
        vav.tick(1.0)

    snapshot = vav.operating_snapshot()
    water_btuh = (
        500.0
        * snapshot["hot_water_flow_gpm"]
        * (plant.supply_temp_f - snapshot["hot_water_return_temp_f"])
    )
    assert snapshot["hot_water_flow_gpm"] > 0.0
    assert snapshot["hot_water_return_temp_f"] < plant.supply_temp_f
    assert water_btuh == pytest.approx(
        snapshot["hot_water_coil_load_btuh"], rel=0.01, abs=25.0
    )
    assert snapshot["discharge_temp_f"] > 80.0
