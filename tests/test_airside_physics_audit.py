"""Regression tests for audited airside mass, energy, and actuator behavior."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from bacpypes3.basetypes import BinaryPV
from bacpypes3.primitivedata import Real

from app.config_models import EquipmentGroupConfig
from app.equipment.ahu import AhuModel, AhuParameters
from app.equipment.exhaust_fan import ExhaustFanModel, ExhaustFanParameters
from app.equipment.psychrometrics import (
    dry_bulb_from_enthalpy_and_humidity_ratio,
    humidity_ratio_from_rh,
    moist_air_enthalpy_from_humidity_ratio,
)
from app.equipment.zone import ZoneModel, ZoneParameters
from app.registry import PointRegistry


CONFIG_DIR = Path(__file__).resolve().parent.parent / "config" / "devices"


def _group(filename: str) -> EquipmentGroupConfig:
    return EquipmentGroupConfig.model_validate(
        json.loads((CONFIG_DIR / filename).read_text())
    )


async def _write(
    registry: PointRegistry,
    group_id: str,
    alias: str,
    value: float | bool,
) -> None:
    obj = registry.all_points()[f"{group_id}.{alias}"].bacnet_object
    if isinstance(value, bool):
        await obj.write_property(
            "presentValue",
            BinaryPV("active" if value else "inactive"),
            priority=8,
        )
    else:
        await obj.write_property("presentValue", Real(float(value)), priority=8)


@pytest.mark.asyncio
async def test_mixed_air_conserves_moist_air_enthalpy() -> None:
    registry = PointRegistry([_group("site.json"), _group("ahu_1.json")])
    registry.build_objects()
    site = registry.view("ACI-SIM-SITE")
    site.set("oa_temp", 95.0)
    site.set("oa_humidity", 80.0)
    ahu = AhuModel(
        "ACI-SIM-AHU-1",
        registry.view("ACI-SIM-AHU-1"),
        site,
        parameters=AhuParameters(
            minimum_outdoor_air_fraction=0.5,
            economizer_time_constant_seconds=1.0,
            return_air_time_constant_seconds=1.0,
            fan_heat_f=0.0,
        ),
    )
    return_ratio = humidity_ratio_from_rh(65.0, 20.0)
    terminal = SimpleNamespace(
        return_air_temp_f=65.0,
        return_air_humidity_ratio=return_ratio,
        return_airflow_cfm=1000.0,
        design_max_airflow_cfm=1000.0,
        damper_position_feedback_pct=100.0,
        params=SimpleNamespace(floor_area_sqft=1000.0),
    )
    ahu.set_vav_models([terminal])
    await _write(registry, "ACI-SIM-AHU-1", "sa_fan_ss", True)

    for _ in range(120):
        ahu.tick(1.0)

    fraction = ahu.outside_air_fraction
    outdoor_ratio = humidity_ratio_from_rh(95.0, 80.0)
    mixed_ratio = fraction * outdoor_ratio + (1.0 - fraction) * return_ratio
    mixed_enthalpy = (
        fraction * moist_air_enthalpy_from_humidity_ratio(95.0, outdoor_ratio)
        + (1.0 - fraction)
        * moist_air_enthalpy_from_humidity_ratio(65.0, return_ratio)
    )
    expected_temp = dry_bulb_from_enthalpy_and_humidity_ratio(
        mixed_enthalpy,
        mixed_ratio,
    )
    arithmetic_temp = fraction * 95.0 + (1.0 - fraction) * 65.0

    assert ahu._ma_temp == pytest.approx(expected_temp, abs=0.15)
    assert abs(ahu._ma_temp - arithmetic_temp) > 0.05


def test_negative_building_pressure_increases_zone_infiltration_load() -> None:
    parameters = ZoneParameters(
        floor_area_sqft=1000.0,
        ceiling_height_ft=10.0,
        thermal_capacitance_btuper_f=10000.0,
        envelope_ua_btuh_per_f=0.0,
        peak_solar_gain_btuh=0.0,
        internal_sensible_gain_btuh_per_sqft=0.0,
        occupants_per_1000_sqft=0.0,
        infiltration_ach_fan_off=0.2,
        adjacent_mixing_cfm=0.0,
    )
    neutral = ZoneModel(parameters, initial_temp_f=72.0)
    negative = ZoneModel(parameters, initial_temp_f=72.0)
    common = dict(
        zone_temp_f=72.0,
        outdoor_temp_f=95.0,
        outdoor_humidity_pct=60.0,
        supply_airflow_cfm=0.0,
        discharge_temp_f=55.0,
        supply_humidity_ratio=humidity_ratio_from_rh(55.0, 90.0),
        ahu_supply_proven=False,
    )

    neutral_temp, _ = neutral.tick(3600.0, building_pressure_inwc=0.0, **common)
    negative_temp, _ = negative.tick(
        3600.0,
        building_pressure_inwc=-0.05,
        **common,
    )

    assert negative.last_snapshot["infiltration_cfm"] > neutral.last_snapshot[
        "infiltration_cfm"
    ]
    assert negative_temp > neutral_temp


@pytest.mark.asyncio
async def test_ahu_hot_water_valves_use_equal_percentage_characteristic() -> None:
    registry = PointRegistry([_group("site.json"), _group("ahu_1.json")])
    registry.build_objects()
    ahu = AhuModel(
        "ACI-SIM-AHU-1",
        registry.view("ACI-SIM-AHU-1"),
        registry.view("ACI-SIM-SITE"),
    )
    ahu._heating_valve_fraction = 0.5

    flow = ahu.hot_water_flow_at_pressure(ahu.params.hot_water_design_dp_psi)

    assert 3.0 < flow < 4.0
    assert flow < 0.5 * ahu.params.heating_coil_design_flow_gpm


@pytest.mark.asyncio
async def test_exhaust_flow_and_envelope_pressure_follow_air_mass_balance() -> None:
    registry = PointRegistry([_group("site.json"), _group("ef_1.json")])
    registry.build_objects()

    class AirHandler:
        fan_running = True
        outside_airflow_cfm = 1500.0

    fan = ExhaustFanModel(
        "ACI-SIM-EF-1",
        registry.view("ACI-SIM-EF-1"),
        site_registry=registry.view("ACI-SIM-SITE"),
        ahu_model=AirHandler(),
        parameters=ExhaustFanParameters(
            proof_delay_seconds=1.0,
            fan_speed_time_constant_seconds=1.0,
            damper_time_constant_seconds=1.0,
            pressure_time_constant_seconds=1.0,
            maximum_exhaust_airflow_cfm=3000.0,
        ),
    )
    await _write(registry, "ACI-SIM-EF-1", "exh_fan_ss", True)
    await _write(registry, "ACI-SIM-EF-1", "exh_air_damper", 100.0)
    await _write(registry, "ACI-SIM-EF-1", "vfd_speed_command", 25.0)
    for _ in range(20):
        fan.tick(1.0)
    positive_pressure = registry.view("ACI-SIM-SITE").get("building_pressure")

    await _write(registry, "ACI-SIM-EF-1", "vfd_speed_command", 75.0)
    for _ in range(20):
        fan.tick(1.0)
    negative_pressure = registry.view("ACI-SIM-SITE").get("building_pressure")

    assert positive_pressure > 0.0
    assert negative_pressure < 0.0
    assert fan.exhaust_airflow_cfm > AirHandler.outside_airflow_cfm


@pytest.mark.asyncio
async def test_return_fan_has_independent_proof_timing() -> None:
    registry = PointRegistry([_group("site.json"), _group("ahu_1.json")])
    registry.build_objects()
    ahu = AhuModel(
        "ACI-SIM-AHU-1",
        registry.view("ACI-SIM-AHU-1"),
        registry.view("ACI-SIM-SITE"),
        parameters=AhuParameters(
            fan_start_time_constant_seconds=1.0,
            fan_proof_delay_seconds=1.0,
            return_fan_start_time_constant_seconds=5.0,
            return_fan_proof_delay_seconds=3.0,
        ),
    )
    await _write(registry, "ACI-SIM-AHU-1", "sa_fan_ss", True)
    await _write(registry, "ACI-SIM-AHU-1", "ra_fan_ss", True)

    ahu.tick(1.0)
    view = registry.view("ACI-SIM-AHU-1")
    assert view.get("sa_fan_status") == 1.0
    assert view.get("ra_fan_status") == 0.0

    for _ in range(3):
        ahu.tick(1.0)
    assert view.get("ra_fan_status") == 1.0
