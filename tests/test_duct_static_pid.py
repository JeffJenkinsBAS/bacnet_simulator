"""AHU duct-static plant, BACnet points, and PID-training regressions."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from bacpypes3.basetypes import BinaryPV
from bacpypes3.primitivedata import Real

from app.config_models import EquipmentGroupConfig
from app.equipment.ahu import AhuModel, AhuParameters
from app.registry import PointRegistry


CONFIG_DIR = Path(__file__).resolve().parent.parent / "config" / "devices"
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _group(filename: str) -> EquipmentGroupConfig:
    return EquipmentGroupConfig.model_validate(
        json.loads((CONFIG_DIR / filename).read_text())
    )


async def _write_binary(
    registry: PointRegistry,
    group_id: str,
    alias: str,
    active: bool,
) -> None:
    point = registry.all_points()[f"{group_id}.{alias}"].bacnet_object
    await point.write_property(
        "presentValue",
        BinaryPV("active" if active else "inactive"),
        priority=8,
    )


async def _write_analog(
    registry: PointRegistry,
    group_id: str,
    alias: str,
    value: float,
) -> None:
    point = registry.all_points()[f"{group_id}.{alias}"].bacnet_object
    await point.write_property(
        "presentValue",
        Real(value),
        priority=8,
    )


@dataclass
class MutableTerminal:
    design_max_airflow_cfm: float
    damper_position_feedback_pct: float


def _model(
    terminals: list[MutableTerminal] | None = None,
    *,
    parameters: AhuParameters | None = None,
) -> tuple[PointRegistry, AhuModel]:
    registry = PointRegistry([_group("site.json"), _group("ahu_1.json")])
    registry.build_objects()
    ahu = AhuModel(
        "ACI-SIM-AHU-1",
        registry.view("ACI-SIM-AHU-1"),
        registry.view("ACI-SIM-SITE"),
        parameters=parameters,
    )
    ahu.set_vav_models(terminals or [])
    return registry, ahu


def test_duct_static_points_are_unique_next_in_line_ahu_avs() -> None:
    group = _group("ahu_1.json")
    expected = {
        "duct_static_pressure_setpoint": {
            "instance": 2,
            "global": 9002,
            "writable": True,
            "commandable": True,
            "minimum": 0.25,
            "maximum": 2.0,
        },
        "duct_static_pressure": {
            "instance": 3,
            "global": 9003,
            "writable": False,
            "commandable": False,
            "minimum": 0.0,
            "maximum": 10.0,
        },
        "sa_fan_speed_feedback": {
            "instance": 4,
            "global": 9004,
            "writable": False,
            "commandable": False,
            "minimum": 0.0,
            "maximum": 100.0,
        },
    }
    by_alias = {point.alias: point for point in group.points}
    for alias, contract in expected.items():
        point = by_alias[alias]
        assert point.object_type.value == "analog-value"
        assert point.object_instance == contract["instance"]
        assert group.instance_offset + point.object_instance == contract["global"]
        assert point.writable is contract["writable"]
        assert point.commandable is contract["commandable"]
        assert point.minimum == contract["minimum"]
        assert point.maximum == contract["maximum"]
    assert by_alias["duct_static_pressure_setpoint"].units == "inches-of-water"
    assert by_alias["duct_static_pressure"].units == "inches-of-water"
    assert by_alias["sa_fan_speed_feedback"].units == "percent"


@pytest.mark.asyncio
async def test_duct_static_and_trend_are_exactly_zero_when_fan_is_not_proven() -> None:
    registry, ahu = _model(
        [MutableTerminal(1000.0, 75.0)],
    )
    ahu.tick(2.0)

    snapshot = ahu.duct_static_snapshot()
    assert snapshot["fan_command"] is False
    assert snapshot["fan_status"] is False
    assert snapshot["pid_active"] is False
    assert snapshot["actual_inwc"] == 0.0
    assert snapshot["fan_speed_pct"] == 0.0
    assert snapshot["history"][-1]["setpoint_inwc"] == 0.0
    assert snapshot["history"][-1]["actual_inwc"] == 0.0
    assert snapshot["history"][-1]["fan_speed_pct"] == 0.0
    view = registry.view("ACI-SIM-AHU-1")
    assert view.get("duct_static_pressure") == 0.0
    assert view.get("sa_fan_speed_feedback") == 0.0


@pytest.mark.asyncio
async def test_fan_status_off_immediately_forces_pressure_and_frequency_to_zero() -> None:
    registry, ahu = _model([MutableTerminal(1000.0, 70.0)])
    await _write_binary(registry, "ACI-SIM-AHU-1", "sa_fan_ss", True)
    for _ in range(120):
        ahu.tick(1.0)
    assert ahu.duct_static_snapshot()["fan_status"] is True

    await _write_binary(registry, "ACI-SIM-AHU-1", "sa_fan_ss", False)
    ahu.tick(1.0)

    snapshot = ahu.duct_static_snapshot()
    assert snapshot["fan_status"] is False
    assert snapshot["actual_inwc"] == 0.0
    assert snapshot["physical_inwc"] == 0.0
    assert snapshot["fan_speed_pct"] == 0.0
    assert snapshot["vfd_frequency_hz"] == 0.0
    assert registry.view("ACI-SIM-AHU-1").get("duct_static_pressure") == 0.0


@pytest.mark.asyncio
async def test_fixed_speed_plant_pressure_rises_as_terminals_close() -> None:
    terminal = MutableTerminal(1000.0, 90.0)
    parameters = AhuParameters(
        duct_static_pid_kp=0.0,
        duct_static_pid_ki=0.0,
        duct_static_pid_kd=0.0,
        duct_static_pid_bias_pct=60.0,
        fan_speed_time_constant_seconds=2.0,
        duct_pressure_time_constant_seconds=2.0,
        duct_sensor_time_constant_seconds=1.0,
    )
    registry, ahu = _model([terminal], parameters=parameters)
    await _write_binary(registry, "ACI-SIM-AHU-1", "sa_fan_ss", True)

    for _ in range(45):
        ahu.tick(1.0)
    open_pressure = ahu.duct_static_snapshot()["actual_inwc"]
    open_speed = ahu.duct_static_snapshot()["fan_speed_pct"]

    terminal.damper_position_feedback_pct = 10.0
    for _ in range(30):
        ahu.tick(1.0)
    closed_pressure = ahu.duct_static_snapshot()["actual_inwc"]
    closed_speed = ahu.duct_static_snapshot()["fan_speed_pct"]

    assert closed_pressure > open_pressure + 0.35
    assert closed_speed == pytest.approx(open_speed, abs=0.1)


@pytest.mark.asyncio
async def test_direct_pid_increases_fan_speed_when_vavs_open_and_pressure_falls() -> None:
    terminal = MutableTerminal(1000.0, 15.0)
    registry, ahu = _model([terminal])
    await _write_binary(registry, "ACI-SIM-AHU-1", "sa_fan_ss", True)

    for _ in range(240):
        ahu.tick(1.0)
    closed_speed = ahu.duct_static_snapshot()["fan_speed_pct"]

    terminal.damper_position_feedback_pct = 90.0
    for _ in range(240):
        ahu.tick(1.0)
    open_snapshot = ahu.duct_static_snapshot()

    assert open_snapshot["fan_speed_pct"] > closed_speed + 5.0
    assert open_snapshot["aggregate_vav_damper_pct"] == pytest.approx(90.0)
    assert abs(open_snapshot["error_inwc"]) < 0.2


@pytest.mark.asyncio
async def test_closing_vavs_causes_pressure_bump_before_pid_unloads_fan() -> None:
    terminal = MutableTerminal(1000.0, 90.0)
    registry, ahu = _model([terminal])
    await _write_binary(registry, "ACI-SIM-AHU-1", "sa_fan_ss", True)
    for _ in range(300):
        ahu.tick(1.0)

    baseline = ahu.duct_static_snapshot()
    terminal.damper_position_feedback_pct = 15.0
    transient = []
    for _ in range(30):
        ahu.tick(1.0)
        transient.append(ahu.duct_static_snapshot())
    for _ in range(270):
        ahu.tick(1.0)
    settled = ahu.duct_static_snapshot()

    assert max(sample["actual_inwc"] for sample in transient) > (
        baseline["actual_inwc"] + 0.08
    )
    assert settled["fan_speed_pct"] < baseline["fan_speed_pct"] - 5.0
    assert abs(settled["actual_inwc"] - settled["setpoint_inwc"]) < 0.15


@pytest.mark.asyncio
async def test_webctrl_static_setpoint_change_drives_pid_and_pressure() -> None:
    registry, ahu = _model([MutableTerminal(1000.0, 70.0)])
    await _write_binary(registry, "ACI-SIM-AHU-1", "sa_fan_ss", True)
    for _ in range(300):
        ahu.tick(1.0)
    baseline = ahu.duct_static_snapshot()

    await _write_analog(
        registry,
        "ACI-SIM-AHU-1",
        "duct_static_pressure_setpoint",
        1.5,
    )
    for _ in range(300):
        ahu.tick(1.0)
    raised = ahu.duct_static_snapshot()

    assert raised["setpoint_inwc"] == pytest.approx(1.5)
    assert raised["actual_inwc"] > baseline["actual_inwc"] + 0.35
    assert raised["fan_speed_pct"] > baseline["fan_speed_pct"] + 5.0
    assert abs(raised["actual_inwc"] - 1.5) < 0.15


@pytest.mark.asyncio
async def test_running_vfd_clamps_physical_output_to_twenty_hertz() -> None:
    registry, ahu = _model([MutableTerminal(1000.0, 0.0)])
    await _write_analog(
        registry,
        "ACI-SIM-AHU-1",
        "duct_static_pressure_setpoint",
        0.25,
    )
    await _write_binary(registry, "ACI-SIM-AHU-1", "sa_fan_ss", True)
    for _ in range(600):
        ahu.tick(1.0)

    snapshot = ahu.duct_static_snapshot()
    assert snapshot["fan_status"] is True
    assert snapshot["pid_output_pct"] < snapshot["vfd_minimum_speed_pct"]
    assert snapshot["vfd_requested_frequency_hz"] < 20.0
    assert snapshot["vfd_frequency_hz"] == pytest.approx(20.0, abs=0.05)
    assert snapshot["fan_speed_pct"] == pytest.approx(100.0 / 3.0, abs=0.1)


@pytest.mark.asyncio
async def test_terminal_demand_is_weighted_by_each_vav_design_airflow() -> None:
    small_open = MutableTerminal(400.0, 100.0)
    large_closed = MutableTerminal(2000.0, 0.0)
    _, ahu = _model([small_open, large_closed])

    conductance, weighted_damper = ahu._aggregate_terminal_conductance()

    assert weighted_damper == pytest.approx(16.667, abs=0.01)
    assert conductance < 0.25


@pytest.mark.asyncio
async def test_pid_tuning_contract_and_defaults_are_bounded() -> None:
    _, ahu = _model()
    ahu.configure_duct_static_pid(
        kp=42.0,
        ki=0.4,
        kd=3.0,
        interval_seconds=2.0,
    )
    tuning = ahu.duct_static_snapshot()["tuning"]
    assert tuning == {
        "kp": 42.0,
        "ki": 0.4,
        "kd": 3.0,
        "interval_seconds": 2.0,
        "defaults": {
            "kp": 30.0,
            "ki": 0.25,
            "kd": 0.0,
            "interval_seconds": 1.0,
        },
        "is_default": False,
        "units": {
            "kp": "% output / in. w.c.",
            "ki": "% output / (in. w.c. x s)",
            "kd": "% output x s / in. w.c.",
            "interval": "simulated seconds",
        },
        "limits": {
            "kp": [0.0, 100.0],
            "ki": [0.0, 1.0],
            "kd": [0.0, 20.0],
            "interval_seconds": [0.5, 10.0],
        },
    }

    for kwargs in (
        {"kp": -0.1, "ki": 0.1, "kd": 0.0, "interval_seconds": 1.0},
        {"kp": 1.0, "ki": 1.1, "kd": 0.0, "interval_seconds": 1.0},
        {"kp": 1.0, "ki": 0.1, "kd": 20.1, "interval_seconds": 1.0},
        {"kp": 1.0, "ki": 0.1, "kd": 0.0, "interval_seconds": 0.4},
    ):
        with pytest.raises(ValueError):
            ahu.configure_duct_static_pid(**kwargs)


@pytest.mark.asyncio
async def test_fan_proof_requires_minimum_vfd_feedback() -> None:
    registry, ahu = _model([MutableTerminal(1000.0, 60.0)])
    await _write_binary(registry, "ACI-SIM-AHU-1", "sa_fan_ss", True)

    for _ in range(20):
        ahu.tick(1.0)
        snapshot = ahu.duct_static_snapshot()
        if snapshot["fan_status"]:
            assert snapshot["fan_speed_pct"] >= 20.0
            assert snapshot["pid_active"] is True
            break
        assert snapshot["actual_inwc"] == 0.0
    else:
        pytest.fail("supply fan never established proof")


@pytest.mark.asyncio
@pytest.mark.parametrize("speed_multiplier", [1, 2, 5, 10, 20, 60])
async def test_pid_remains_stable_at_every_dashboard_time_rate(
    speed_multiplier: int,
) -> None:
    registry, ahu = _model([MutableTerminal(1000.0, 70.0)])
    await _write_binary(registry, "ACI-SIM-AHU-1", "sa_fan_ss", True)

    samples = []
    elapsed = 0
    while elapsed < 600:
        ahu.tick(float(speed_multiplier))
        elapsed += speed_multiplier
        if elapsed >= 480:
            samples.append(ahu.duct_static_snapshot()["actual_inwc"])

    assert samples
    assert max(samples) - min(samples) < 0.12
    assert abs(samples[-1] - 1.0) < 0.12


def test_pid_training_page_exposes_core_controls_and_restart_action() -> None:
    html = (STATIC_DIR / "command-center.html").read_text(encoding="utf-8")
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    for token in (
        'data-view="duct-static"',
        'id="restart-simulation"',
        'id="ahu-command-stage"',
        'id="ahu-component-inspector"',
        'id="ahu-motion-toggle"',
        'id="ahu-high-static-state"',
        'id="ahu-freeze-state"',
        'id="ahu-economizer-card"',
        'data-ahu-component="economizer"',
        "/static/assets/ahu-1-active-components.png",
        'id="pid-trend"',
        'id="pid-tuning-form"',
        'id="pid-kp"',
        'id="pid-ki"',
        'id="pid-kd"',
        'id="pid-interval"',
        "20 Hz minimum",
    ):
        assert token in html
    for route in (
        "/api/simulation/restart",
        "/api/ahu/command-center",
        "/api/ahu/duct-static",
        "/api/ahu/duct-static/pid",
        "/api/ahu/duct-static/pid/reset",
        "/api/ahu/duct-static/pid/defaults",
    ):
        assert route in app_js
    assert ".pid-layout" in styles
    assert ".pid-trend-panel" in styles
    assert ".ahu-command-stage" in styles
    assert "prefers-reduced-motion" in styles
    assert "economizerEffective" in app_js
    assert "requestedFrequency" in app_js
