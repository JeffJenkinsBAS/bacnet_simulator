"""VAV sizing, BACnet compatibility, and virtual-zone physics regressions."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.config_models import EquipmentGroupConfig, validate_equipment_groups
from app.equipment.psychrometrics import humidity_ratio_from_rh
from app.equipment.vav_single_duct import SingleDuctVavModel
from app.equipment.vav_single_duct import VavParameters
from app.equipment.zone import ZoneModel, ZoneParameters
from app.faults import FaultManager, FaultType
from app.registry import PointRegistry

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config" / "devices"
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# Fingerprint captured from the verified 28-group / 321-object WebCTRL build
# before the eight AHU command-center safety/sensor objects were appended.
EXISTING_IDENTIFIER_SHA256 = (
    "aebaf10e2b5e0a3cd044bdc2ef136cdb7ef70ce54065a4d158e3662645e6c0cb"
)
NEW_AHU_POINT_INSTANCES = {
    "ahu_ma_humidity": ("analog-input", 5),
    "ahu_sa_humidity": ("analog-input", 6),
    "cooling_coil_entering_air_temp": ("analog-input", 7),
    "automatic_high_static_trip": ("binary-input", 44),
    "duct_structural_failure": ("binary-input", 45),
    "automatic_freezestat_trip": ("binary-input", 46),
    "cooling_coil_freeze_condition": ("binary-input", 47),
    "cooling_coil_rupture_flood": ("binary-input", 48),
}
NEW_VAV_POINT_INSTANCES = {
    "heating_min_airflow": 81,
    "heating_max_airflow": 82,
    "cooling_min_airflow": 83,
    "cooling_max_airflow": 84,
    "damper_position_feedback": 85,
}


def _groups() -> list[EquipmentGroupConfig]:
    groups = [
        EquipmentGroupConfig.model_validate(json.loads(path.read_text()))
        for path in sorted(CONFIG_DIR.glob("*.json"))
    ]
    validate_equipment_groups(groups)
    return groups


def test_catalog_preserves_321_identifiers_and_adds_only_eight_ahu_points() -> None:
    groups = _groups()
    rows: list[tuple[str, str, str, int]] = []
    existing_rows: list[tuple[str, str, str, int]] = []
    new_rows: list[tuple[str, str, str, int]] = []

    for group in groups:
        for point in group.points:
            row = (
                group.group_id,
                point.alias,
                point.object_type.value,
                group.instance_offset + point.object_instance,
            )
            rows.append(row)
            if (
                group.group_id == "ACI-SIM-AHU-1"
                and point.alias in NEW_AHU_POINT_INSTANCES
            ):
                new_rows.append(row)
            else:
                existing_rows.append(row)

    assert len(groups) == 28
    assert len(rows) == 329
    assert len(existing_rows) == 321
    serialized = "\n".join(
        "|".join(map(str, row))
        for row in sorted(existing_rows, key=lambda item: json.dumps(item))
    )
    assert hashlib.sha256(serialized.encode()).hexdigest() == EXISTING_IDENTIFIER_SHA256

    expected_new_rows = [
        (
            "ACI-SIM-AHU-1",
            alias,
            object_type,
            9000 + local_instance,
        )
        for alias, (object_type, local_instance) in NEW_AHU_POINT_INSTANCES.items()
    ]
    assert sorted(new_rows) == sorted(expected_new_rows)

    humidity_rows = [
        row
        for row in rows
        if row[1] == "zone_humidity"
    ]
    assert sorted(humidity_rows, key=lambda row: row[3]) == [
        (f"ACI-SIM-VAV-{number}", "zone_humidity", "analog-input", number * 1000 + 10003)
        for number in range(3, 16)
    ]
    for number in (1, 2, 16, 17):
        group = next(g for g in groups if g.group_id == f"ACI-SIM-VAV-{number}")
        assert "zone_humidity" not in {point.alias for point in group.points}


def test_virtual_zone_profiles_are_varied_and_sizing_is_plausible() -> None:
    groups = {
        group.group_id: group
        for group in _groups()
        if group.group_id.startswith("ACI-SIM-VAV-")
    }
    virtual = [groups[f"ACI-SIM-VAV-{number}"] for number in range(3, 18)]
    areas = [float(group.model_parameters["floor_area_sqft"]) for group in virtual]
    maximums = [float(group.model_parameters["max_airflow_cfm"]) for group in virtual]
    minimums = [
        float(group.model_parameters["occupied_minimum_airflow_cfm"])
        for group in virtual
    ]
    temperatures = [
        next(point.initial_value for point in group.points if point.alias == "zone_temp")
        for group in virtual
    ]

    assert len(set(areas)) == len(areas)
    assert len(set(maximums)) >= 12
    assert len(set(temperatures)) == len(temperatures)
    assert min(maximums) == 400.0
    assert max(maximums) == 2120.0
    assert min(areas) == 600.0
    assert max(areas) == 2400.0
    assert all(0.24 <= minimum / maximum <= 0.36 for minimum, maximum in zip(minimums, maximums))
    assert all(0.60 <= maximum / area <= 1.15 for maximum, area in zip(maximums, areas))

    small = groups["ACI-SIM-VAV-3"].model_parameters
    large = groups["ACI-SIM-VAV-11"].model_parameters
    assert float(small["floor_area_sqft"]) < float(large["floor_area_sqft"])
    assert float(small["max_airflow_cfm"]) < float(large["max_airflow_cfm"])
    for group in groups.values():
        design_maximum = float(group.model_parameters["max_airflow_cfm"])
        by_alias = {point.alias: point for point in group.points}
        assert by_alias["airflow_setpoint"].maximum >= design_maximum
        assert by_alias["airflow"].maximum >= design_maximum
        for alias, local_instance in NEW_VAV_POINT_INSTANCES.items():
            point = by_alias[alias]
            assert point.object_type.value == "analog-value"
            assert point.object_instance == local_instance
            assert point.writable is False
            assert point.commandable is False

        occupied_minimum = float(
            group.model_parameters["occupied_minimum_airflow_cfm"]
        )
        heating_maximum = float(
            group.model_parameters["heating_maximum_airflow_cfm"]
        )
        assert by_alias["heating_min_airflow"].initial_value == occupied_minimum
        assert by_alias["heating_max_airflow"].initial_value == heating_maximum
        assert by_alias["cooling_min_airflow"].initial_value == occupied_minimum
        assert by_alias["cooling_max_airflow"].initial_value == design_maximum


def test_vav_parameter_semantics_reject_impossible_profiles() -> None:
    with pytest.raises(ValueError, match="design_static_pressure_inwc"):
        VavParameters(design_static_pressure_inwc=0.0)
    with pytest.raises(ValueError, match="airflow sizing"):
        VavParameters(
            max_airflow_cfm=400.0,
            occupied_minimum_airflow_cfm=300.0,
            heating_maximum_airflow_cfm=200.0,
        )
    with pytest.raises(ValueError, match="closed_damper_leakage_cfm"):
        VavParameters(closed_damper_leakage_cfm=3.1)


def test_no_supply_air_does_not_create_fictitious_temperature_response() -> None:
    params = ZoneParameters(
        floor_area_sqft=1000.0,
        thermal_capacitance_btuper_f=10000.0,
        envelope_ua_btuh_per_f=0.0,
        peak_solar_gain_btuh=0.0,
        internal_sensible_gain_btuh_per_sqft=0.0,
        occupants_per_1000_sqft=0.0,
        infiltration_ach_fan_on=0.0,
        infiltration_ach_fan_off=0.0,
        adjacent_mixing_cfm=0.0,
    )
    zone = ZoneModel(params, initial_temp_f=72.0, initial_humidity_pct=45.0)
    new_temp, _ = zone.tick(
        3600.0,
        zone_temp_f=72.0,
        outdoor_temp_f=95.0,
        outdoor_humidity_pct=60.0,
        supply_airflow_cfm=2120.0,
        discharge_temp_f=55.0,
        supply_humidity_ratio=humidity_ratio_from_rh(55.0, 90.0),
        ahu_supply_proven=False,
    )
    assert new_temp == pytest.approx(72.0)


def test_zone_heat_balance_scales_with_actual_airflow_and_thermal_mass() -> None:
    common = dict(
        floor_area_sqft=1000.0,
        thermal_capacitance_btuper_f=12000.0,
        envelope_ua_btuh_per_f=0.0,
        peak_solar_gain_btuh=0.0,
        internal_sensible_gain_btuh_per_sqft=0.0,
        occupants_per_1000_sqft=0.0,
        infiltration_ach_fan_on=0.0,
        infiltration_ach_fan_off=0.0,
        adjacent_mixing_cfm=0.0,
    )
    supply_ratio = humidity_ratio_from_rh(55.0, 90.0)
    results = {}
    for airflow in (400.0, 2120.0):
        zone = ZoneModel(ZoneParameters(**common), initial_temp_f=74.0)
        temp, _ = zone.tick(
            600.0,
            zone_temp_f=74.0,
            outdoor_temp_f=74.0,
            outdoor_humidity_pct=45.0,
            supply_airflow_cfm=airflow,
            discharge_temp_f=55.0,
            supply_humidity_ratio=supply_ratio,
            ahu_supply_proven=True,
        )
        results[airflow] = temp

    assert 0.05 < 74.0 - results[400.0] < 0.3
    assert 74.0 - results[2120.0] > 4.0 * (74.0 - results[400.0])
    assert 74.0 - results[2120.0] < 1.0


def test_humidity_responds_slowly_in_absolute_moisture_space() -> None:
    params = ZoneParameters(
        floor_area_sqft=600.0,
        thermal_capacitance_btuper_f=5400.0,
        envelope_ua_btuh_per_f=0.0,
        peak_solar_gain_btuh=0.0,
        internal_sensible_gain_btuh_per_sqft=0.0,
        occupants_per_1000_sqft=0.0,
        humidity_capacitance_multiplier=12.0,
        infiltration_ach_fan_on=0.0,
        adjacent_mixing_cfm=0.0,
    )
    zone = ZoneModel(params, initial_temp_f=72.0, initial_humidity_pct=55.0)
    _, rh = zone.tick(
        3600.0,
        zone_temp_f=72.0,
        outdoor_temp_f=72.0,
        outdoor_humidity_pct=55.0,
        supply_airflow_cfm=400.0,
        discharge_temp_f=72.0,
        supply_humidity_ratio=humidity_ratio_from_rh(72.0, 35.0),
        ahu_supply_proven=True,
    )
    assert 48.0 < rh < 55.0


def test_command_center_temperature_source_is_ascii_safe() -> None:
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    command_center = (STATIC_DIR / "command-center.html").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert app_js.isascii()
    assert command_center.isascii()
    assert "\\u00B0F" in app_js
    assert "&deg;F" in command_center
    assert "°F" not in app_js
    assert '"Segoe UI Variable Text"' in styles
    assert "font-size: 17px;" in styles
    assert "Laptop readability pass" in styles
    for label in (
        "Cooling minimum",
        "Cooling maximum",
        "Heating minimum",
        "Heating maximum",
        "Damper command",
        "Damper feedback",
    ):
        assert label in app_js
    for alias in (
        "heating_min_airflow",
        "heating_max_airflow",
        "cooling_min_airflow",
        "cooling_max_airflow",
        "damper_position_feedback",
    ):
        assert alias in app_js


@pytest.mark.asyncio
async def test_zone_sensor_fault_does_not_corrupt_physical_temperature_state() -> None:
    group = next(
        group
        for group in _groups()
        if group.group_id == "ACI-SIM-VAV-3"
    )
    faults = FaultManager()
    registry = PointRegistry([group])
    registry.build_objects()
    view = registry.view(group.group_id, fault_manager=faults)
    vav = SingleDuctVavModel(
        equipment_id=group.group_id,
        registry=view,
        has_physical_zone_sensor=False,
    )
    physical_before = vav.zone_model.temperature_f

    faults.set_fault(
        "zone-offset",
        FaultType.offset,
        group.group_id,
        "zone_temp",
        {"offset": 20.0},
    )
    vav.tick(60.0)
    indicated_during_fault = view.get("zone_temp")
    physical_during_fault = vav.zone_model.temperature_f
    assert indicated_during_fault > physical_during_fault + 19.0

    faults.clear_fault("zone-offset")
    vav.tick(60.0)
    indicated_after_clear = view.get("zone_temp")
    physical_after_clear = vav.zone_model.temperature_f
    assert indicated_after_clear == pytest.approx(physical_after_clear)
    assert abs(physical_after_clear - physical_before) < 1.0
