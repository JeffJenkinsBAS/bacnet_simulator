"""
Unit tests for the VAV equipment model's mechanical behavior. These do not
touch the network at all -- they build a PointRegistry from the real
ACI-SIM-VAV-1 group config file (one group, same as production, just not
merged with the other 15) and drive the model's tick() function.

Everything here runs inside a single asyncio event loop per test:
bacpypes3's local objects schedule internal async setup at construction
time, so both building the registry and issuing priority-array writes have
to happen with a loop already running rather than via bare asyncio.run()
calls scattered across fixture setup.
"""
import json
from pathlib import Path

import pytest
from bacpypes3.primitivedata import Real

from app.config_models import EquipmentGroupConfig
from app.registry import PointRegistry, GroupView
from app.equipment.vav_single_duct import SingleDuctVavModel, VavParameters

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
GROUP_ID = "ACI-SIM-VAV-1"

pytestmark = pytest.mark.asyncio


def _load_group_config() -> EquipmentGroupConfig:
    with open(CONFIG_DIR / "devices" / "vav_1.json") as f:
        return EquipmentGroupConfig.model_validate(json.load(f))


def _build_vav(**param_overrides) -> tuple[PointRegistry, GroupView, SingleDuctVavModel]:
    registry = PointRegistry([_load_group_config()])
    registry.build_objects()
    view = registry.view(GROUP_ID)
    params = VavParameters(
        max_airflow_cfm=param_overrides.pop("max_airflow_cfm", 1000.0),
        design_static_pressure_inwc=param_overrides.pop("design_static_pressure_inwc", 1.0),
        damper_time_constant_seconds=param_overrides.pop("damper_time_constant_seconds", 5.0),
        max_reheat_rise_f=param_overrides.pop("max_reheat_rise_f", 30.0),
        thermal_time_constant_seconds=param_overrides.pop("thermal_time_constant_seconds", 10.0),
        **param_overrides,
    )
    vav = SingleDuctVavModel(equipment_id=GROUP_ID, registry=view, parameters=params)
    return registry, view, vav


async def _write_commandable(registry: PointRegistry, alias: str, value: float, priority: int = 8) -> None:
    """
    Simulate a real WebCTRL BACnet write by going through the object's own
    write_property() -- the same priority-array-aware code path a real
    WritePropertyRequest hits in transport.py, not a shortcut.
    """
    obj = registry.all_points()[f"{GROUP_ID}.{alias}"].bacnet_object
    await obj.write_property("presentValue", Real(value), priority=priority)


async def test_airflow_increases_with_damper_position():
    registry, view, vav = _build_vav()
    vav.available_static_pressure_inwc = 2.0  # plenty of static, not the limiting factor
    await _write_commandable(registry, "damper_position_command", 100.0)

    for _ in range(60):  # run long enough for the first-order lag to settle
        vav.tick(1.0)

    airflow = view.get("airflow")
    assert airflow > 900.0, f"expected airflow to approach max (1000 cfm), got {airflow}"


async def test_airflow_is_capped_by_low_static_pressure():
    registry, view, vav = _build_vav()
    vav.available_static_pressure_inwc = 0.25  # only 25% of design static available
    await _write_commandable(registry, "damper_position_command", 100.0)

    for _ in range(60):
        vav.tick(1.0)

    airflow = view.get("airflow")
    assert 450.0 < airflow < 550.0, (
        f"quarter design pressure should produce about half design airflow by the fan-law square root, got {airflow}"
    )


async def test_zero_damper_command_yields_only_closed_blade_leakage():
    registry, view, vav = _build_vav()
    vav.available_static_pressure_inwc = 2.0
    vav._airflow_cfm = 750.0
    await _write_commandable(registry, "damper_position_command", 0.0)
    await _write_commandable(registry, "airflow_setpoint", 800.0)

    vav.tick(1.0)

    assert 0.0 <= view.get("airflow") <= 3.0
    assert view.get("airflow") == pytest.approx(
        vav.params.closed_damper_leakage_cfm
    )


async def test_unproven_ahu_forces_exact_zero_airflow():
    registry, view, vav = _build_vav()
    vav.available_static_pressure_inwc = 0.0
    vav._airflow_cfm = 750.0
    await _write_commandable(registry, "damper_position_command", 100.0)
    await _write_commandable(registry, "airflow_setpoint", 800.0)

    vav.tick(1.0)

    assert view.get("airflow") == 0.0


async def test_airflow_setpoint_caps_available_damper_capacity():
    registry, view, vav = _build_vav()
    vav.available_static_pressure_inwc = 2.0
    await _write_commandable(registry, "damper_position_command", 100.0)
    await _write_commandable(registry, "airflow_setpoint", 400.0)

    for _ in range(60):
        vav.tick(1.0)

    assert 390.0 <= view.get("airflow") <= 410.0


async def test_read_only_airflow_limits_and_damper_feedback_are_published():
    registry, view, vav = _build_vav(
        max_airflow_cfm=1000.0,
        occupied_minimum_airflow_cfm=300.0,
        heating_maximum_airflow_cfm=500.0,
    )
    vav.available_static_pressure_inwc = 2.0
    await _write_commandable(registry, "damper_position_command", 42.0)

    vav.tick(1.0)

    assert view.get("heating_min_airflow") == 300.0
    assert view.get("heating_max_airflow") == 500.0
    assert view.get("cooling_min_airflow") == 300.0
    assert view.get("cooling_max_airflow") == 1000.0
    assert view.get("damper_position_feedback") == 42.0

    expected_instances = {
        "heating_min_airflow": 81,
        "heating_max_airflow": 82,
        "cooling_min_airflow": 83,
        "cooling_max_airflow": 84,
        "damper_position_feedback": 85,
    }
    for alias, local_instance in expected_instances.items():
        point = view.point_config(alias)
        assert point.object_type.value == "analog-value"
        assert point.object_instance == local_instance
        assert point.writable is False
        assert point.commandable is False


async def test_discharge_temp_rises_with_reheat_valve():
    registry, view, vav = _build_vav()
    vav.ahu_supply_air_temp_f = 55.0
    vav.available_static_pressure_inwc = 2.0
    await _write_commandable(registry, "damper_position_command", 50.0)
    await _write_commandable(registry, "hw_valve_command", 100.0)

    for _ in range(120):
        vav.tick(1.0)

    discharge_temp = view.get("discharge_temp")
    assert discharge_temp > 55.0, "reheat valve fully open should raise discharge temp above SA temp"


async def test_discharge_temp_tracks_ahu_supply_air_temp_when_valve_closed():
    registry, view, vav = _build_vav()
    vav.ahu_supply_air_temp_f = 58.0
    vav.available_static_pressure_inwc = 2.0
    await _write_commandable(registry, "damper_position_command", 80.0)
    await _write_commandable(registry, "hw_valve_command", 0.0)

    for _ in range(120):
        vav.tick(1.0)

    discharge_temp = view.get("discharge_temp")
    assert abs(discharge_temp - 58.0) < 1.0, (
        f"with reheat valve closed, discharge temp should settle near AHU SA temp (58F), got {discharge_temp}"
    )


async def test_lower_airflow_produces_more_reheat_rise_for_same_valve_position():
    """Mechanical behavior check: less air dilutes the reheat coil's heat pickup more."""
    registry, view, vav = _build_vav()
    vav.ahu_supply_air_temp_f = 55.0

    # High airflow case
    vav.available_static_pressure_inwc = 2.0
    await _write_commandable(registry, "damper_position_command", 100.0)
    await _write_commandable(registry, "hw_valve_command", 50.0)
    for _ in range(150):
        vav.tick(1.0)
    high_flow_discharge_temp = view.get("discharge_temp")

    # Reset internal state and try low airflow with the same valve position
    vav._airflow_cfm = 0.0
    vav._discharge_temp_f = 55.0
    await _write_commandable(registry, "damper_position_command", 15.0)
    await _write_commandable(registry, "hw_valve_command", 50.0)
    for _ in range(150):
        vav.tick(1.0)
    low_flow_discharge_temp = view.get("discharge_temp")

    assert low_flow_discharge_temp > high_flow_discharge_temp, (
        "lower airflow with the same reheat valve position should produce a higher discharge "
        "temperature (less air to absorb the same heat output)"
    )


async def test_higher_priority_write_wins_over_lower_priority_write():
    """A lower-priority (higher number) write must not override a higher-priority command."""
    registry, view, vav = _build_vav()
    vav.available_static_pressure_inwc = 2.0

    await _write_commandable(registry, "damper_position_command", 100.0, priority=6)
    await _write_commandable(registry, "damper_position_command", 20.0, priority=12)

    for _ in range(60):
        vav.tick(1.0)

    # Priority 6 (higher priority / lower number) must win over priority 12.
    airflow = view.get("airflow")
    assert airflow > 900.0, (
        f"priority 6 command (100%) should win over priority 12 command (20%), got airflow={airflow}"
    )
