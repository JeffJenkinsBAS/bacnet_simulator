"""
Regression tests for a real bug found during Phase 3 live verification: a
delayed boolean state (fan/pump "running" proof) was being collapsed to a
bool every tick before being fed back into the first-order approach()
calculation, which reset the ramp to zero every tick and meant the state
could never actually cross the 0.5 threshold -- fans and pumps that should
prove on after their start delay never did. Fixed by keeping the ramp as a
persistent float fraction and only deriving the bool from it, never feeding
the bool back in. These tests exist so that pattern can't silently return.

Each test builds its own tiny, isolated PointRegistry (one or more small
equipment groups, group_id "TEST*") rather than loading the real config
files -- keeps these tests focused purely on the proof-delay math.
"""
import pytest

from app.config_models import EquipmentGroupConfig
from app.registry import PointRegistry
from app.equipment.ahu import AhuModel, AhuParameters
from app.equipment.boiler import BoilerModel, BoilerParameters
from app.equipment.chiller import ChillerModel, ChillerParameters
from app.equipment.exhaust_fan import ExhaustFanModel, ExhaustFanParameters
from bacpypes3.basetypes import BinaryPV
from bacpypes3.primitivedata import Real

pytestmark = pytest.mark.asyncio


def _group(group_id: str, offset: int, points: list[dict]) -> EquipmentGroupConfig:
    return EquipmentGroupConfig.model_validate({"group_id": group_id, "instance_offset": offset, "points": points})


def _bo(alias, inst):
    return {"alias": alias, "object_type": "binary-output", "object_instance": inst, "object_name": alias,
            "units": "no-units", "signal_direction": "webctrl_to_sim", "writable": True, "commandable": True}


def _bv(alias, inst):
    return {"alias": alias, "object_type": "binary-value", "object_instance": inst, "object_name": alias,
            "units": "no-units", "signal_direction": "webctrl_to_sim", "writable": True, "commandable": True}


def _ai(alias, inst, initial=55.0):
    return {"alias": alias, "object_type": "analog-input", "object_instance": inst, "object_name": alias,
            "units": "degrees-fahrenheit", "signal_direction": "sim_to_webctrl", "initial_value": initial}


def _ao(alias, inst):
    return {"alias": alias, "object_type": "analog-output", "object_instance": inst, "object_name": alias,
            "units": "percent", "signal_direction": "webctrl_to_sim", "writable": True, "commandable": True}


def _bi(alias, inst):
    return {"alias": alias, "object_type": "binary-input", "object_instance": inst, "object_name": alias,
            "units": "no-units", "signal_direction": "sim_to_webctrl"}


def _av_oa(alias, inst):
    return {"alias": alias, "object_type": "analog-value", "object_instance": inst, "object_name": alias,
            "units": "degrees-fahrenheit", "signal_direction": "sim_to_webctrl", "initial_value": 70.0}


async def _write_bool(registry: PointRegistry, group_id: str, alias: str, value: bool, priority: int = 8) -> None:
    obj = registry.all_points()[f"{group_id}.{alias}"].bacnet_object
    await obj.write_property("presentValue", BinaryPV("active" if value else "inactive"), priority=priority)


async def _write_analog(
    registry: PointRegistry,
    group_id: str,
    alias: str,
    value: float,
    priority: int = 8,
) -> None:
    obj = registry.all_points()[f"{group_id}.{alias}"].bacnet_object
    await obj.write_property("presentValue", Real(value), priority=priority)


async def test_ahu_fan_actually_proves_after_start_delay():
    ahu_group = _group("TEST-AHU", 90000, [
        _ao("cooling_valve", 20), _ao("heating_valve", 21), _ao("preheat_valve", 22), _ao("economizer", 23),
        _bo("ra_fan_ss", 60), _bo("sa_fan_ss", 61),
        _bv("high_static_pressure_trip", 100), _bv("freezestat_trip", 101),
        _ai("ahu_ma_temp", 1), _ai("ahu_ra_temp", 2), _ai("ahu_ra_humidity", 3, initial=50.0), _ai("ahu_sa_temp", 4),
        _bi("ra_smoke_detector", 40), _bi("sa_smoke_detector", 41),
        _bi("sa_fan_status", 42), _bi("ra_fan_status", 43),
    ])
    site_group = _group("TEST-SITE", 91000, [_av_oa("oa_temp", 80)])

    registry = PointRegistry([ahu_group, site_group])
    registry.build_objects()

    ahu = AhuModel("ACI-SIM-AHU-1", registry.view("TEST-AHU"), registry.view("TEST-SITE"),
                   parameters=AhuParameters(fan_start_time_constant_seconds=3.0))
    await _write_bool(registry, "TEST-AHU", "sa_fan_ss", True)

    for _ in range(30):  # 10x the time constant -- must have proved by now
        ahu.tick(1.0)

    assert ahu.fan_running is True, "fan should have proved on after 30s with a 3s start time constant"
    assert registry.view("TEST-AHU").get("sa_fan_status") == 1.0
    assert registry.view("TEST-AHU").get("ra_fan_status") == 0.0


async def test_chiller_pumps_and_tower_fan_actually_prove():
    chiller_group = _group("TEST-CHILLER", 92000, [
        _bo("chiller_enable", 60), _bo("chiller_ss", 61), _bi("chiller_status", 40),
        _ao("byp_vlv_output", 20), _bo("chw_iso_valve", 62), _bi("chw_iso_vlv_sts", 41),
        _bo("chw_pump_ss", 63), _bi("chw_pump_status", 42),
        _ai("chwr_temp", 1), _ao("chws_stpt_reset", 21), _ai("chws_temp", 2),
        _bo("ct_fan_ss", 64), _bi("ct_fan_status", 43), _bi("ct_vfd_fault", 44), _ao("ct_vfd_output", 22),
        _bo("cw_pump_ss", 65), _bi("cw_pump_status", 45),
        _ai("cwr_temp", 3), _ai("cws_basin_temp", 4), _ai("cws_temp", 5), _bo("manager_reset", 66),
    ])
    site_group = _group("TEST-SITE2", 93000, [_av_oa("oa_temp", 80)])
    plant_group = _group("TEST-PLANT", 94000, [_bv("emerg_shutdown_trip", 100), _bv("refrig_shutdown_trip", 101)])

    registry = PointRegistry([chiller_group, site_group, plant_group])
    registry.build_objects()

    chiller = ChillerModel("ACI-SIM-CHILLER-1", registry.view("TEST-CHILLER"), registry.view("TEST-SITE2"),
                            registry.view("TEST-PLANT"),
                            parameters=ChillerParameters(start_delay_seconds=10.0, pump_start_delay_seconds=3.0,
                                                          tower_fan_start_delay_seconds=3.0))
    await _write_bool(registry, "TEST-CHILLER", "chiller_enable", True)
    await _write_bool(registry, "TEST-CHILLER", "chiller_ss", True)
    await _write_bool(registry, "TEST-CHILLER", "chw_iso_valve", True)
    await _write_bool(registry, "TEST-CHILLER", "chw_pump_ss", True)
    await _write_bool(registry, "TEST-CHILLER", "cw_pump_ss", True)
    await _write_bool(registry, "TEST-CHILLER", "ct_fan_ss", True)

    for _ in range(60):
        chiller.tick(1.0)

    assert chiller._proven is True, "chiller should have proved on after 60s with a 10s start delay"
    assert chiller._chw_pump_running is True, "CHW pump should have proved on"
    assert chiller._cw_pump_running is True, "CW pump should have proved on"
    assert chiller._tower_fan_running is True, "cooling tower fan should have proved on"


async def test_boiler_pumps_actually_prove():
    boiler_group = _group("TEST-BOILER", 95000, [
        _bi("boiler_ok", 40), _bo("boiler_ss", 60), _bo("circ_pump_ss", 61), _bo("hw_pump_ss", 62), _ao("hws_stpt_reset", 20),
    ])
    registry = PointRegistry([boiler_group])
    registry.build_objects()

    boiler = BoilerModel("ACI-SIM-BOILER-1", registry.view("TEST-BOILER"), parameters=BoilerParameters(pump_start_delay_seconds=3.0))
    await _write_bool(registry, "TEST-BOILER", "circ_pump_ss", True)
    await _write_bool(registry, "TEST-BOILER", "hw_pump_ss", True)

    for _ in range(30):
        boiler.tick(1.0)

    assert boiler._circ_pump_running is True
    assert boiler._hw_pump_running is True


async def test_exhaust_fan_actually_proves():
    ef_group = _group("TEST-EF", 96000, [_ao("exh_air_damper", 20), _bo("exh_fan_ss", 60), _bi("fan_status", 40)])
    registry = PointRegistry([ef_group])
    registry.build_objects()

    fan = ExhaustFanModel("ACI-SIM-EF-1", registry.view("TEST-EF"), parameters=ExhaustFanParameters(proof_delay_seconds=4.0))
    await _write_bool(registry, "TEST-EF", "exh_fan_ss", True)

    for _ in range(20):
        fan.tick(1.0)

    assert fan._running is True
    assert registry.view("TEST-EF").get("fan_status") == 1.0


async def test_exhaust_vfd_trims_building_pressure_from_ahu_supply():
    ef_group = _group(
        "TEST-EF-VFD",
        97000,
        [
            _ao("exh_air_damper", 20),
            _ao("vfd_speed_command", 21),
            _bo("exh_fan_ss", 60),
            _bi("fan_status", 40),
        ],
    )
    site_group = _group(
        "TEST-PRESSURE",
        98000,
        [
            {
                "alias": "building_pressure",
                "object_type": "analog-input",
                "object_instance": 82,
                "object_name": "building_pressure",
                "units": "inches-of-water",
                "signal_direction": "sim_to_webctrl",
                "initial_value": 0.0,
                "normal_range": {"low": 0.03, "high": 0.10},
            }
        ],
    )
    registry = PointRegistry([ef_group, site_group])
    registry.build_objects()

    class RunningAhu:
        fan_running = True

    fan = ExhaustFanModel(
        "ACI-SIM-EF-1",
        registry.view("TEST-EF-VFD"),
        parameters=ExhaustFanParameters(
            proof_delay_seconds=2.0,
            pressure_time_constant_seconds=2.0,
        ),
        site_registry=registry.view("TEST-PRESSURE"),
        ahu_model=RunningAhu(),
    )
    await _write_bool(registry, "TEST-EF-VFD", "exh_fan_ss", True)
    await _write_analog(registry, "TEST-EF-VFD", "exh_air_damper", 100.0)
    await _write_analog(registry, "TEST-EF-VFD", "vfd_speed_command", 50.0)

    for _ in range(20):
        fan.tick(1.0)

    pressure_at_half_speed = registry.view("TEST-PRESSURE").get("building_pressure")
    assert 0.035 <= pressure_at_half_speed <= 0.055
    assert registry.view("TEST-EF-VFD").get("fan_status") == 1.0

    await _write_analog(registry, "TEST-EF-VFD", "vfd_speed_command", 100.0)
    for _ in range(20):
        fan.tick(1.0)

    assert registry.view("TEST-PRESSURE").get("building_pressure") < pressure_at_half_speed
