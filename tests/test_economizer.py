"""Differential-enthalpy economizer suitability and safety-limit regressions."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.config_models import EquipmentGroupConfig
from app.equipment.ahu import AhuModel, AhuParameters
from app.equipment.psychrometrics import (
    dew_point_f,
    moist_air_enthalpy_btu_per_lb,
)
from app.faults import FaultManager, FaultType
from app.registry import PointRegistry


CONFIG_DIR = Path(__file__).resolve().parent.parent / "config" / "devices"
AHU_ID = "ACI-SIM-AHU-1"
SITE_ID = "ACI-SIM-SITE"


def _group(filename: str) -> EquipmentGroupConfig:
    return EquipmentGroupConfig.model_validate(
        json.loads((CONFIG_DIR / filename).read_text(encoding="utf-8"))
    )


def _model(
    *,
    fault_manager: FaultManager | None = None,
    parameters: AhuParameters | None = None,
) -> tuple[PointRegistry, AhuModel]:
    # Earlier async integration tests may close pytest's main-thread loop.
    # bacpypes3 schedules object post-initialization work while the registry is
    # constructed, so restore a loop when this module runs later in the suite.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    registry = PointRegistry([_group("site.json"), _group("ahu_1.json")])
    registry.build_objects()
    ahu = AhuModel(
        AHU_ID,
        registry.view(AHU_ID, fault_manager=fault_manager),
        registry.view(SITE_ID, fault_manager=fault_manager),
        parameters=parameters,
    )
    return registry, ahu


def _prime_cooling_need(ahu: AhuModel) -> None:
    ahu.fan_running = True
    ahu._requested_conditioning = "cooling"
    ahu._sa_temp_setpoint_f = 55.0
    ahu._sa_temp = 68.0
    ahu._ra_temp = 72.0
    ahu._ra_humidity = 50.0
    ahu._ma_temp = 60.0


def test_psychrometric_helpers_support_enthalpy_and_dew_point_decisions() -> None:
    cool_dry = moist_air_enthalpy_btu_per_lb(60.0, 40.0)
    warm_humid = moist_air_enthalpy_btu_per_lb(78.0, 70.0)

    assert cool_dry < warm_humid
    assert dew_point_f(75.0, 50.0) == pytest.approx(55.1, abs=0.8)


def test_dual_enthalpy_enable_hold_and_disable_hysteresis() -> None:
    _, ahu = _model()
    _prime_cooling_need(ahu)

    effective = ahu._update_economizer(
        1.0,
        requested_pct=100.0,
        oa_temp_f=60.0,
        oa_humidity_pct=40.0,
        cooling_command_pct=50.0,
        safety_shutdown=False,
    )
    assert effective == 100.0
    assert ahu.economizer_snapshot()["free_cooling_available"] is True
    assert ahu.economizer_snapshot()["suitability_method"] == "dual-enthalpy"

    # Equal OA/RA enthalpy sits in the -1/+1 Btu/lb hold band.
    effective = ahu._update_economizer(
        1.0,
        requested_pct=100.0,
        oa_temp_f=72.0,
        oa_humidity_pct=50.0,
        cooling_command_pct=50.0,
        safety_shutdown=False,
    )
    assert effective == 100.0
    assert ahu.economizer_snapshot()["free_cooling_available"] is True

    effective = ahu._update_economizer(
        1.0,
        requested_pct=100.0,
        oa_temp_f=82.0,
        oa_humidity_pct=65.0,
        cooling_command_pct=50.0,
        safety_shutdown=False,
    )
    assert effective == 0.0
    assert ahu.economizer_snapshot()["free_cooling_available"] is False
    assert ahu.economizer_snapshot()["state"] == "unavailable-weather"


def test_high_dew_point_locks_out_economizer_even_below_ra_dry_bulb() -> None:
    _, ahu = _model()
    _prime_cooling_need(ahu)

    effective = ahu._update_economizer(
        1.0,
        requested_pct=100.0,
        oa_temp_f=70.0,
        oa_humidity_pct=85.0,
        cooling_command_pct=50.0,
        safety_shutdown=False,
    )

    assert ahu.economizer_snapshot()["oa_dew_point_f"] > 57.0
    assert ahu.economizer_snapshot()["free_cooling_available"] is False
    assert effective == 0.0


def test_unreliable_oa_humidity_falls_back_to_differential_dry_bulb() -> None:
    faults = FaultManager()
    faults.set_fault(
        "oa-rh-failed",
        FaultType.reliability_fail,
        SITE_ID,
        "oa_humidity",
        {"value": 50.0},
    )
    _, ahu = _model(fault_manager=faults)
    _prime_cooling_need(ahu)

    effective = ahu._update_economizer(
        1.0,
        requested_pct=75.0,
        oa_temp_f=55.0,
        oa_humidity_pct=50.0,
        cooling_command_pct=50.0,
        safety_shutdown=False,
    )
    snapshot = ahu.economizer_snapshot()

    assert effective == 75.0
    assert snapshot["suitability_method"] == "differential-dry-bulb"
    assert "humidity sensor unavailable" in snapshot["sensor_fallback_reason"]


def test_unreliable_return_air_sensor_falls_back_to_single_enthalpy() -> None:
    faults = FaultManager()
    faults.set_fault(
        "ra-temp-failed",
        FaultType.reliability_fail,
        AHU_ID,
        "ahu_ra_temp",
        {"value": 72.0},
    )
    _, ahu = _model(fault_manager=faults)
    _prime_cooling_need(ahu)

    effective = ahu._update_economizer(
        1.0,
        requested_pct=60.0,
        oa_temp_f=55.0,
        oa_humidity_pct=35.0,
        cooling_command_pct=50.0,
        safety_shutdown=False,
    )
    snapshot = ahu.economizer_snapshot()

    assert effective == 60.0
    assert snapshot["suitability_method"] == "single-enthalpy"
    assert "return-air enthalpy sensor unavailable" in (
        snapshot["sensor_fallback_reason"]
    )


def test_only_reliable_oat_uses_fixed_dry_bulb_fallback() -> None:
    faults = FaultManager()
    faults.set_fault(
        "oa-rh-failed",
        FaultType.reliability_fail,
        SITE_ID,
        "oa_humidity",
        {"value": 50.0},
    )
    faults.set_fault(
        "ra-temp-failed",
        FaultType.reliability_fail,
        AHU_ID,
        "ahu_ra_temp",
        {"value": 72.0},
    )
    _, ahu = _model(fault_manager=faults)
    _prime_cooling_need(ahu)

    effective = ahu._update_economizer(
        1.0,
        requested_pct=50.0,
        oa_temp_f=60.0,
        oa_humidity_pct=50.0,
        cooling_command_pct=50.0,
        safety_shutdown=False,
    )

    assert effective == 50.0
    assert ahu.economizer_snapshot()["suitability_method"] == "fixed-dry-bulb"


def test_unreliable_outdoor_air_temperature_disables_free_cooling() -> None:
    faults = FaultManager()
    faults.set_fault(
        "oa-temp-failed",
        FaultType.reliability_fail,
        SITE_ID,
        "oa_temp",
        {"value": 55.0},
    )
    _, ahu = _model(fault_manager=faults)
    _prime_cooling_need(ahu)

    effective = ahu._update_economizer(
        1.0,
        requested_pct=100.0,
        oa_temp_f=55.0,
        oa_humidity_pct=40.0,
        cooling_command_pct=50.0,
        safety_shutdown=False,
    )
    snapshot = ahu.economizer_snapshot()

    assert effective == 0.0
    assert snapshot["suitability_method"] == "unavailable"
    assert snapshot["state"] == "unavailable-sensor"
    assert "outdoor-air temperature sensor unavailable" in (
        snapshot["sensor_fallback_reason"]
    )


def test_unsuitable_weather_limits_webctrl_request_to_minimum_ventilation() -> None:
    parameters = AhuParameters(
        fan_start_time_constant_seconds=0.1,
        fan_speed_time_constant_seconds=0.1,
        duct_pressure_time_constant_seconds=0.1,
        duct_sensor_time_constant_seconds=0.1,
    )
    registry, ahu = _model(parameters=parameters)
    registry._set(f"{AHU_ID}.sa_fan_ss", 1.0)
    registry._set(f"{AHU_ID}.economizer", 100.0)
    registry._set(f"{AHU_ID}.cooling_valve", 50.0)
    registry._set(f"{SITE_ID}.oa_temp", 85.0)
    registry._set(f"{SITE_ID}.oa_humidity", 70.0)

    for _ in range(8):
        ahu.tick(1.0)
    snapshot = ahu.economizer_snapshot()

    assert snapshot["requested_pct"] == 100.0
    assert snapshot["effective_pct"] == 0.0
    assert snapshot["outside_air_fraction"] == pytest.approx(0.15)
    assert "economizer-commanded-while-unavailable" in snapshot["fdd_flags"]


def test_fan_off_and_hard_safety_force_damper_fully_closed() -> None:
    _, ahu = _model()
    _prime_cooling_need(ahu)
    ahu.fan_running = False
    assert ahu._update_economizer(
        1.0,
        requested_pct=100.0,
        oa_temp_f=55.0,
        oa_humidity_pct=40.0,
        cooling_command_pct=50.0,
        safety_shutdown=False,
    ) == 0.0
    assert ahu.economizer_snapshot()["state"] == "off"

    ahu.fan_running = True
    assert ahu._update_economizer(
        1.0,
        requested_pct=100.0,
        oa_temp_f=55.0,
        oa_humidity_pct=40.0,
        cooling_command_pct=50.0,
        safety_shutdown=True,
    ) == 0.0
    assert ahu.economizer_snapshot()["state"] == "safety-shutdown"


def test_mixed_air_low_limit_closes_and_releases_with_hysteresis() -> None:
    _, ahu = _model()
    _prime_cooling_need(ahu)
    ahu._ma_temp = 44.0

    assert ahu._update_economizer(
        1.0,
        requested_pct=100.0,
        oa_temp_f=35.0,
        oa_humidity_pct=40.0,
        cooling_command_pct=50.0,
        safety_shutdown=False,
    ) == 0.0
    assert ahu.economizer_snapshot()["mixed_air_low_limit_active"] is True

    ahu._ma_temp = 46.0
    assert ahu._update_economizer(
        1.0,
        requested_pct=100.0,
        oa_temp_f=35.0,
        oa_humidity_pct=40.0,
        cooling_command_pct=50.0,
        safety_shutdown=False,
    ) == 0.0

    ahu._ma_temp = 47.0
    assert ahu._update_economizer(
        1.0,
        requested_pct=100.0,
        oa_temp_f=35.0,
        oa_humidity_pct=40.0,
        cooling_command_pct=50.0,
        safety_shutdown=False,
    ) == 100.0
    assert ahu.economizer_snapshot()["mixed_air_low_limit_active"] is False


def test_stuck_oa_damper_fault_defeats_mixed_air_low_limit() -> None:
    faults = FaultManager()
    faults.set_fault(
        "oa-damper-stuck-open",
        FaultType.stuck_value,
        AHU_ID,
        "economizer_damper_feedback",
        {"value": 100.0},
    )
    _, ahu = _model(fault_manager=faults)
    _prime_cooling_need(ahu)
    ahu._ma_temp = 44.0

    effective = ahu._update_economizer(
        1.0,
        requested_pct=0.0,
        oa_temp_f=10.0,
        oa_humidity_pct=45.0,
        cooling_command_pct=0.0,
        safety_shutdown=False,
    )
    snapshot = ahu.economizer_snapshot()

    assert effective == 100.0
    assert snapshot["mixed_air_low_limit_active"] is True
    assert snapshot["state"] == "actuator-stuck"
    assert "physically stuck at 100%" in snapshot["limiting_reason"]
    assert "economizer-damper-actuator-stuck" in snapshot["fdd_flags"]


def test_stuck_oa_damper_drives_sustained_freeze_exposure() -> None:
    faults = FaultManager()
    faults.set_fault(
        "freezestat-bypassed",
        FaultType.safety_bypass,
        AHU_ID,
        "automatic_freezestat_trip",
    )
    faults.set_fault(
        "oa-damper-stuck-open",
        FaultType.stuck_value,
        AHU_ID,
        "economizer_damper_feedback",
        {"value": 100.0},
    )
    registry, ahu = _model(fault_manager=faults)
    registry._set(f"{AHU_ID}.sa_fan_ss", 1.0)
    registry._set(f"{AHU_ID}.economizer", 100.0)
    registry._set(f"{AHU_ID}.preheat_valve", 0.0)
    registry._set(f"{AHU_ID}.cooling_valve", 0.0)
    registry._set(f"{SITE_ID}.oa_temp", 10.0)
    registry._set(f"{SITE_ID}.oa_humidity", 45.0)

    for _ in range(22):
        ahu.tick(60.0)

    safety = ahu.safety_snapshot()
    assert safety["cooling_coil_entering_air_temp_f"] < 32.0
    assert safety["cooling_coil_freeze_condition"] is True
    assert safety["cooling_coil_rupture_flood"] is True


def test_integrated_cooling_requires_180_simulated_seconds_full_open() -> None:
    _, ahu = _model()
    _prime_cooling_need(ahu)

    ahu._update_economizer(
        179.0,
        requested_pct=100.0,
        oa_temp_f=55.0,
        oa_humidity_pct=40.0,
        cooling_command_pct=50.0,
        safety_shutdown=False,
    )
    assert ahu.economizer_snapshot()["integrated_cooling_allowed"] is False

    ahu._update_economizer(
        1.0,
        requested_pct=100.0,
        oa_temp_f=55.0,
        oa_humidity_pct=40.0,
        cooling_command_pct=50.0,
        safety_shutdown=False,
    )
    snapshot = ahu.economizer_snapshot()
    assert snapshot["integrated_cooling_allowed"] is True
    assert snapshot["state"] == "integrated-economizing"


def test_available_cooling_air_without_command_sets_fdd_flag() -> None:
    _, ahu = _model()
    _prime_cooling_need(ahu)
    ahu._update_economizer(
        1.0,
        requested_pct=0.0,
        oa_temp_f=55.0,
        oa_humidity_pct=40.0,
        cooling_command_pct=50.0,
        safety_shutdown=False,
    )

    assert (
        "economizer-not-commanded-when-available"
        in ahu.economizer_snapshot()["fdd_flags"]
    )
