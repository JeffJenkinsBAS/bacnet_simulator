"""
Tests for Phase 4: the FaultManager mechanics applied through GroupView,
and validation that every shipped scenario JSON file actually parses and
only references fault types that exist.
"""
import glob
import json
from pathlib import Path

import pytest

from app.config_models import EquipmentGroupConfig
from app.equipment.vav_single_duct import SingleDuctVavModel, VavParameters
from app.faults import FaultManager, FaultType
from app.registry import PointRegistry
from app.scenario import Scenario

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
GROUP_ID = "ACI-SIM-VAV-1"

def _load_group_config() -> EquipmentGroupConfig:
    with open(CONFIG_DIR / "devices" / "vav_1.json") as f:
        return EquipmentGroupConfig.model_validate(json.load(f))


def _build(fault_manager: FaultManager):
    registry = PointRegistry([_load_group_config()])
    registry.build_objects()
    view = registry.view(GROUP_ID, fault_manager=fault_manager)
    vav = SingleDuctVavModel(
        equipment_id=GROUP_ID, registry=view,
        parameters=VavParameters(max_airflow_cfm=1000.0, design_static_pressure_inwc=1.0,
                                  damper_time_constant_seconds=5.0, max_reheat_rise_f=30.0,
                                  thermal_time_constant_seconds=10.0),
    )
    vav.available_static_pressure_inwc = 2.0
    return registry, view, vav


async def test_frozen_value_fault_holds_output_at_activation_value():
    fm = FaultManager()
    registry, view, vav = _build(fm)

    for _ in range(60):
        vav.tick(1.0)
    value_before_freeze = view.get("airflow")

    fm.set_fault("f1", FaultType.frozen_value, GROUP_ID, "airflow", {})

    # Command a big change and keep ticking -- the real internal state moves, but the published value must not.
    from bacpypes3.primitivedata import Real
    obj = registry.all_points()[f"{GROUP_ID}.damper_position_command"].bacnet_object
    await obj.write_property("presentValue", Real(0.0), priority=8)
    for _ in range(60):
        vav.tick(1.0)

    assert view.get("airflow") == pytest.approx(value_before_freeze), "frozen_value must hold the output constant"


async def test_offset_fault_shifts_output_by_fixed_amount():
    fm = FaultManager()
    registry, view, vav = _build(fm)
    for _ in range(60):
        vav.tick(1.0)
    unfaulted = view.get("discharge_temp")

    fm.set_fault("f2", FaultType.offset, GROUP_ID, "discharge_temp", {"offset": 5.0})
    vav.tick(1.0)

    assert view.get("discharge_temp") == pytest.approx(unfaulted + 5.0, abs=0.2)


async def test_drift_fault_accumulates_over_ticks():
    fm = FaultManager()
    registry, view, vav = _build(fm)
    fm.set_fault("f3", FaultType.drift, GROUP_ID, "discharge_temp", {"rate_per_second": 1.0})

    baseline = view.get("discharge_temp")
    for _ in range(10):
        fm.tick(1.0)  # normally called by SimulationEngine once per loop
        vav.tick(1.0)

    # after ~10s at 1.0/s drift, the published value should be roughly 10 higher than it would've been
    assert view.get("discharge_temp") > baseline + 5.0


async def test_stuck_value_fault_on_input_freezes_what_equipment_model_sees():
    fm = FaultManager()
    registry, view, vav = _build(fm)

    from bacpypes3.primitivedata import Real
    obj = registry.all_points()[f"{GROUP_ID}.damper_position_command"].bacnet_object
    await obj.write_property("presentValue", Real(50.0), priority=8)
    for _ in range(60):
        vav.tick(1.0)
    stuck_airflow = view.get("airflow")

    fm.set_fault("f4", FaultType.stuck_value, GROUP_ID, "damper_position_command", {})
    vav.tick(1.0)  # let the fault capture the CURRENT (50.0) commanded value before anything else changes
    stuck_feedback = view.get("damper_position_feedback")

    # WebCTRL writes a big new command -- the real object updates, but the equipment model must not react.
    await obj.write_property("presentValue", Real(100.0), priority=8)
    for _ in range(60):
        vav.tick(1.0)

    real_object_value = await_get(obj)
    assert real_object_value == pytest.approx(100.0), "the real BACnet object must still reflect the new write"
    assert view.get("airflow") == pytest.approx(stuck_airflow, abs=20.0), (
        "the equipment model's airflow must not have responded to the new command while stuck_value is active"
    )
    assert view.get("damper_position_feedback") == pytest.approx(stuck_feedback), (
        "AV:85 feedback must hold the captured effective position while the damper command is stuck"
    )


def await_get(obj):
    pv = obj.presentValue
    return float(pv)


async def test_forced_status_output_overrides_computed_boolean():
    fm = FaultManager()
    registry = PointRegistry([_load_group_config()])
    registry.build_objects()
    view = registry.view(GROUP_ID, fault_manager=fm)

    fm.set_fault("f5", FaultType.forced_status, GROUP_ID, "airflow", {"value": False})
    view.set("airflow", 500.0)  # equipment model tries to publish a real value
    assert view.get("airflow") == 0.0, "forced_status must override the real computed value"


async def test_reversed_actuator_inverts_commanded_percentage():
    fm = FaultManager()
    registry, view, vav = _build(fm)
    fm.set_fault("f6", FaultType.reversed_actuator, GROUP_ID, "damper_position_command", {})

    from bacpypes3.primitivedata import Real
    obj = registry.all_points()[f"{GROUP_ID}.damper_position_command"].bacnet_object
    await obj.write_property("presentValue", Real(20.0), priority=8)

    assert view.get_commanded("damper_position_command") == pytest.approx(80.0)
    vav.tick(1.0)
    assert view.get("damper_position_feedback") == pytest.approx(80.0)


def test_clear_fault_and_clear_all():
    fm = FaultManager()
    fm.set_fault("a", FaultType.offset, "G", "x", {"offset": 1.0})
    fm.set_fault("b", FaultType.offset, "G", "y", {"offset": 1.0})
    assert len(fm.list_faults()) == 2

    assert fm.clear_fault("a") is True
    assert fm.clear_fault("does-not-exist") is False
    assert len(fm.list_faults()) == 1

    fm.clear_all()
    assert len(fm.list_faults()) == 0


def test_every_shipped_scenario_is_valid_and_only_uses_real_fault_types():
    scenario_dir = Path(__file__).resolve().parent.parent / "config" / "scenarios"
    paths = sorted(glob.glob(str(scenario_dir / "*.json")))
    assert len(paths) >= 1, "expected at least one shipped scenario"

    known_fault_types = {t.value for t in FaultType}
    known_actions = {"set_fault", "clear_fault", "set_value", "release_value", "set_weather"}

    for path in paths:
        with open(path) as f:
            scenario = Scenario.model_validate(json.load(f))
        for event in scenario.events:
            assert event.action in known_actions, f"{path}: unknown action '{event.action}'"
            if event.action in ("set_fault", "clear_fault"):
                assert event.fault in known_fault_types, f"{path}: unknown fault type '{event.fault}'"
