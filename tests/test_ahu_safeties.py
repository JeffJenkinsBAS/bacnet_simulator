"""AHU safety-state, catastrophic training physics, and point contracts."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config_models import EquipmentGroupConfig
from app.equipment.ahu import AhuModel, AhuParameters
from app.faults import FaultManager, FaultType
from app.registry import PointRegistry


CONFIG_DIR = Path(__file__).resolve().parent.parent / "config" / "devices"
AHU_ID = "ACI-SIM-AHU-1"


class ClosedTerminal:
    design_max_airflow_cfm = 1000.0
    damper_position_feedback_pct = 0.0


def _group(filename: str) -> EquipmentGroupConfig:
    return EquipmentGroupConfig.model_validate(
        json.loads((CONFIG_DIR / filename).read_text(encoding="utf-8"))
    )


def _model(
    *,
    parameters: AhuParameters | None = None,
    fault_manager: FaultManager | None = None,
) -> tuple[PointRegistry, AhuModel]:
    registry = PointRegistry([_group("site.json"), _group("ahu_1.json")])
    registry.build_objects()
    ahu = AhuModel(
        AHU_ID,
        registry.view(AHU_ID, fault_manager=fault_manager),
        registry.view("ACI-SIM-SITE", fault_manager=fault_manager),
        parameters=parameters,
    )
    ahu.set_vav_models([ClosedTerminal()])
    return registry, ahu


def _set_command(registry: PointRegistry, alias: str, value: float) -> None:
    registry._set(f"{AHU_ID}.{alias}", value)


def test_eight_command_center_points_append_without_renumbering() -> None:
    group = _group("ahu_1.json")
    by_alias = {point.alias: point for point in group.points}
    expected = {
        "ahu_ma_humidity": ("analog-input", 5, 9005),
        "ahu_sa_humidity": ("analog-input", 6, 9006),
        "cooling_coil_entering_air_temp": ("analog-input", 7, 9007),
        "automatic_high_static_trip": ("binary-input", 44, 9044),
        "duct_structural_failure": ("binary-input", 45, 9045),
        "automatic_freezestat_trip": ("binary-input", 46, 9046),
        "cooling_coil_freeze_condition": ("binary-input", 47, 9047),
        "cooling_coil_rupture_flood": ("binary-input", 48, 9048),
    }

    for alias, (object_type, local_instance, global_instance) in expected.items():
        point = by_alias[alias]
        assert point.object_type.value == object_type
        assert point.object_instance == local_instance
        assert group.instance_offset + local_instance == global_instance
        assert point.writable is False
        assert point.commandable is False

    assert by_alias["duct_static_pressure"].object_instance == 3
    assert by_alias["duct_static_pressure"].maximum == 10.0
    assert by_alias["high_static_pressure_trip"].object_instance == 100
    assert by_alias["freezestat_trip"].object_instance == 101


def test_safety_bypass_is_restricted_to_the_two_automatic_safeties() -> None:
    faults = FaultManager()
    faults.set_fault(
        "high-bypass",
        FaultType.safety_bypass,
        AHU_ID,
        "automatic_high_static_trip",
    )
    faults.set_fault(
        "freeze-bypass",
        FaultType.safety_bypass,
        AHU_ID,
        "automatic_freezestat_trip",
    )
    with pytest.raises(ValueError, match="safety_bypass"):
        faults.set_fault(
            "invalid-bypass",
            FaultType.safety_bypass,
            AHU_ID,
            "duct_static_pressure",
        )


def test_automatic_high_static_trip_latches_before_duct_failure() -> None:
    parameters = AhuParameters(
        fan_start_time_constant_seconds=0.1,
        fan_speed_time_constant_seconds=0.1,
        duct_pressure_time_constant_seconds=0.1,
        duct_sensor_time_constant_seconds=0.1,
        duct_static_pid_kp=0.0,
        duct_static_pid_ki=0.0,
        duct_static_pid_kd=0.0,
        duct_static_pid_bias_pct=100.0,
        duct_static_pid_output_slew_pct_per_second=100.0,
    )
    registry, ahu = _model(parameters=parameters)
    _set_command(registry, "sa_fan_ss", 1.0)

    for _ in range(10):
        ahu.tick(1.0)
        if ahu.safety_snapshot()["automatic_high_static_trip"]:
            break

    safety = ahu.safety_snapshot()
    assert safety["automatic_high_static_trip"] is True
    assert safety["duct_structural_failure"] is False
    assert safety["high_static_trip_active"] is True
    assert ahu.duct_static_snapshot()["fan_status"] is False
    assert ahu.duct_static_snapshot()["fan_speed_pct"] == 0.0

    for _ in range(5):
        ahu.tick(1.0)
    assert ahu.safety_snapshot()["automatic_high_static_trip"] is True


def test_bypassed_high_static_safety_allows_latched_duct_failure() -> None:
    faults = FaultManager()
    faults.set_fault(
        "high-bypass",
        FaultType.safety_bypass,
        AHU_ID,
        "automatic_high_static_trip",
    )
    parameters = AhuParameters(
        fan_start_time_constant_seconds=0.1,
        fan_speed_time_constant_seconds=0.1,
        duct_pressure_time_constant_seconds=0.1,
        duct_sensor_time_constant_seconds=0.1,
        duct_static_pid_kp=0.0,
        duct_static_pid_ki=0.0,
        duct_static_pid_kd=0.0,
        duct_static_pid_bias_pct=100.0,
        duct_static_pid_output_slew_pct_per_second=100.0,
    )
    registry, ahu = _model(parameters=parameters, fault_manager=faults)
    _set_command(registry, "sa_fan_ss", 1.0)

    for _ in range(10):
        ahu.tick(1.0)
        if ahu.safety_snapshot()["duct_structural_failure"]:
            break

    safety = ahu.safety_snapshot()
    assert safety["high_static_safety_bypassed"] is True
    assert safety["automatic_high_static_trip"] is False
    assert safety["duct_structural_failure"] is True
    assert ahu.duct_static_snapshot()["fan_command"] is True
    assert ahu.duct_static_snapshot()["fan_status"] is True
    assert ahu.duct_static_snapshot()["physical_inwc"] == 0.0

    faults.clear_all()
    ahu.tick(1.0)
    assert ahu.safety_snapshot()["duct_structural_failure"] is True


def test_manual_high_static_trip_is_not_defeated_by_automatic_bypass() -> None:
    faults = FaultManager()
    faults.set_fault(
        "high-bypass",
        FaultType.safety_bypass,
        AHU_ID,
        "automatic_high_static_trip",
    )
    registry, ahu = _model(fault_manager=faults)
    _set_command(registry, "sa_fan_ss", 1.0)
    _set_command(registry, "high_static_pressure_trip", 1.0)
    ahu.tick(5.0)

    safety = ahu.safety_snapshot()
    assert safety["manual_high_static_trip"] is True
    assert safety["high_static_trip_active"] is True
    assert ahu.duct_static_snapshot()["fan_status"] is False


def test_automatic_freezestat_trips_at_ten_simulated_seconds() -> None:
    _, ahu = _model()
    ahu._cooling_coil_entering_air_temp = 34.0

    ahu._update_freeze_safety(9.0, automatic_bypassed=False)
    assert ahu.safety_snapshot()["automatic_freezestat_trip"] is False
    ahu._update_freeze_safety(1.0, automatic_bypassed=False)
    assert ahu.safety_snapshot()["automatic_freezestat_trip"] is True


@pytest.mark.parametrize(
    ("flow_proven", "pre_failure_seconds", "final_seconds"),
    [
        (False, 1199.0, 1.0),
        (True, 3599.0, 1.0),
    ],
)
def test_bypassed_freezestat_uses_twenty_or_sixty_minute_failure_path(
    flow_proven: bool,
    pre_failure_seconds: float,
    final_seconds: float,
) -> None:
    _, ahu = _model()
    ahu._cooling_coil_entering_air_temp = 31.0
    ahu._cooling_valve_fraction = 1.0 if flow_proven else 0.0

    ahu._update_freeze_safety(
        pre_failure_seconds,
        automatic_bypassed=True,
    )
    safety = ahu.safety_snapshot()
    assert safety["cooling_coil_freeze_condition"] is True
    assert safety["cooling_coil_rupture_flood"] is False

    ahu._update_freeze_safety(final_seconds, automatic_bypassed=True)
    assert ahu.safety_snapshot()["cooling_coil_rupture_flood"] is True

    ahu._cooling_coil_entering_air_temp = 45.0
    ahu._update_freeze_safety(120.0, automatic_bypassed=False)
    assert ahu.safety_snapshot()["cooling_coil_rupture_flood"] is True


def test_freeze_hazard_progress_resets_before_damage_when_air_warms() -> None:
    _, ahu = _model()
    ahu._cooling_coil_entering_air_temp = 31.0
    ahu._update_freeze_safety(600.0, automatic_bypassed=True)
    assert ahu.safety_snapshot()["freeze_hazard_progress_pct"] == pytest.approx(
        50.0
    )

    ahu._cooling_coil_entering_air_temp = 40.0
    ahu._update_freeze_safety(1.0, automatic_bypassed=True)
    assert ahu.safety_snapshot()["freeze_hazard_progress_pct"] == 0.0
    assert ahu.safety_snapshot()["cooling_coil_rupture_flood"] is False


def test_mixed_air_and_coil_entering_sensors_are_separate() -> None:
    registry, ahu = _model()
    ahu._ma_temp = 42.0
    ahu._cooling_coil_entering_air_temp = 55.0
    ahu._publish_safety_points()
    view = registry.view(AHU_ID)

    assert view.get("ahu_ma_temp") != view.get("cooling_coil_entering_air_temp")
    assert view.get("cooling_coil_entering_air_temp") == pytest.approx(55.0)
    assert 0.0 <= view.get("ahu_ma_humidity") <= 100.0
    assert 0.0 <= view.get("ahu_sa_humidity") <= 100.0


def test_command_center_snapshot_exposes_sensors_actuators_and_safeties() -> None:
    _, ahu = _model()
    ahu._duct_static_history.extend({"sample": index} for index in range(12))
    snapshot = ahu.ahu_command_center_snapshot()

    assert "sensors" in snapshot
    assert "actuators" in snapshot
    assert "safety" in snapshot
    assert snapshot["safety"]["high_static_trip_threshold_inwc"] == 4.0
    assert snapshot["safety"]["duct_failure_limit_inwc"] == 5.0
    assert snapshot["safety"]["freezestat_trip_temp_f"] == 35.0
    assert snapshot["safety"]["cooling_coil_freeze_temp_f"] == 32.0
    assert len(ahu.ahu_command_center_snapshot(history_limit=5)["history"]) == 5
