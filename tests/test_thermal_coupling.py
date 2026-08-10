"""Energy-conservation regressions for zone -> AHU -> CHW coupling."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from bacpypes3.basetypes import BinaryPV
from bacpypes3.primitivedata import Real

from app.config_models import EquipmentGroupConfig
from app.equipment.ahu import AhuModel, AhuParameters
from app.equipment.chiller import ChillerModel
from app.equipment.managers import ChwPlantManagerModel
from app.equipment.psychrometrics import humidity_ratio_from_rh
from app.equipment.site import SiteModel
from app.equipment.vav_single_duct import SingleDuctVavModel
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
        await obj.write_property(
            "presentValue",
            Real(float(value)),
            priority=8,
        )


def _chiller_stack() -> tuple[
    PointRegistry,
    SiteModel,
    ChillerModel,
    ChwPlantManagerModel,
]:
    registry = PointRegistry(
        [
            _group("site.json"),
            _group("chw_plant.json"),
            _group("chiller_1.json"),
        ]
    )
    registry.build_objects()
    site_view = registry.view("ACI-SIM-SITE")
    plant_view = registry.view("ACI-SIM-CHW-PLANT")
    site = SiteModel("ACI-SIM-SITE", site_view)
    chiller = ChillerModel(
        "ACI-SIM-CHILLER-1",
        registry.view("ACI-SIM-CHILLER-1"),
        site_registry=site_view,
        plant_registry=plant_view,
    )
    manager = ChwPlantManagerModel(
        "ACI-SIM-CHW-PLANT",
        plant_view,
        [chiller],
    )
    return registry, site, chiller, manager


@pytest.mark.asyncio
async def test_common_return_air_is_mass_weighted_from_child_spaces() -> None:
    registry = PointRegistry([_group("site.json"), _group("ahu_1.json")])
    registry.build_objects()
    ahu = AhuModel(
        "ACI-SIM-AHU-1",
        registry.view("ACI-SIM-AHU-1"),
        registry.view("ACI-SIM-SITE"),
        parameters=AhuParameters(return_air_time_constant_seconds=2.0),
    )
    common_ratio = humidity_ratio_from_rh(72.0, 45.0)
    hot_high_flow = SimpleNamespace(
        return_air_temp_f=80.0,
        return_air_humidity_ratio=common_ratio,
        return_airflow_cfm=1000.0,
        design_max_airflow_cfm=1000.0,
        damper_position_feedback_pct=100.0,
        params=SimpleNamespace(floor_area_sqft=1000.0),
    )
    cool_low_flow = SimpleNamespace(
        return_air_temp_f=60.0,
        return_air_humidity_ratio=common_ratio,
        return_airflow_cfm=100.0,
        design_max_airflow_cfm=100.0,
        damper_position_feedback_pct=100.0,
        params=SimpleNamespace(floor_area_sqft=100.0),
    )
    ahu.set_vav_models([hot_high_flow, cool_low_flow])
    await _write(registry, "ACI-SIM-AHU-1", "sa_fan_ss", True)

    for _ in range(30):
        ahu.tick(1.0)

    expected = (80.0 * 1000.0 + 60.0 * 100.0) / 1100.0
    assert ahu.operating_snapshot()["return_air_temp_f"] == pytest.approx(
        expected,
        abs=0.15,
    )
    assert ahu.total_supply_airflow_cfm == pytest.approx(1100.0)


@pytest.mark.asyncio
async def test_external_controller_vavs_still_have_internal_space_loads() -> None:
    registry = PointRegistry([_group("vav_1.json")])
    registry.build_objects()
    vav = SingleDuctVavModel(
        "ACI-SIM-VAV-1",
        registry.view("ACI-SIM-VAV-1"),
        has_physical_zone_sensor=True,
    )

    assert "zone_temp" not in registry.view("ACI-SIM-VAV-1").all_points()
    assert vav.zone_model is not None
    assert vav.return_air_temp_f == pytest.approx(vav.params.zone_cooling_setpoint_f)


@pytest.mark.asyncio
async def test_pump_on_chiller_off_warms_loop_and_creates_load_delta_t() -> None:
    registry, site, chiller, manager = _chiller_stack()
    manager.set_cooling_coils(
        [SimpleNamespace(cooling_coil_load_btuh=150_000.0)]
    )
    await _write(registry, "ACI-SIM-CHILLER-1", "chw_iso_valve", True)
    await _write(registry, "ACI-SIM-CHILLER-1", "chw_pump_ss", True)

    for _ in range(240):
        site.tick(1.0)
        chiller.tick(1.0)
        manager.tick(1.0)

    assert not chiller.proven
    assert manager.flow_gpm == pytest.approx(300.0, abs=0.1)
    assert manager.return_temp_f > manager.supply_temp_f + 0.7
    assert manager.return_temp_f - manager.supply_temp_f == pytest.approx(
        1.0,
        abs=0.15,
    )
    assert manager.supply_temp_f > 54.5


@pytest.mark.asyncio
async def test_running_chiller_with_no_coil_load_has_near_zero_delta_t() -> None:
    registry, site, chiller, manager = _chiller_stack()
    for alias in (
        "chiller_enable",
        "chiller_ss",
        "chw_iso_valve",
        "chw_pump_ss",
        "cw_pump_ss",
    ):
        await _write(registry, "ACI-SIM-CHILLER-1", alias, True)

    for _ in range(600):
        site.tick(1.0)
        chiller.tick(1.0)
        manager.tick(1.0)

    assert chiller.proven
    assert manager.supply_temp_f == pytest.approx(44.0, abs=0.3)
    assert manager.return_temp_f - manager.supply_temp_f == pytest.approx(
        0.0,
        abs=0.2,
    )


@pytest.mark.asyncio
async def test_air_and_water_sides_conserve_cooling_coil_energy() -> None:
    registry = PointRegistry(
        [
            _group("site.json"),
            _group("chw_plant.json"),
            _group("chiller_1.json"),
            _group("ahu_1.json"),
            _group("vav_3.json"),
        ]
    )
    registry.build_objects()
    site_view = registry.view("ACI-SIM-SITE")
    plant_view = registry.view("ACI-SIM-CHW-PLANT")
    site = SiteModel("ACI-SIM-SITE", site_view)
    chiller = ChillerModel(
        "ACI-SIM-CHILLER-1",
        registry.view("ACI-SIM-CHILLER-1"),
        site_registry=site_view,
        plant_registry=plant_view,
    )
    manager = ChwPlantManagerModel(
        "ACI-SIM-CHW-PLANT",
        plant_view,
        [chiller],
    )
    ahu = AhuModel(
        "ACI-SIM-AHU-1",
        registry.view("ACI-SIM-AHU-1"),
        site_view,
        chw_plant_model=manager,
    )
    vav = SingleDuctVavModel(
        "ACI-SIM-VAV-3",
        registry.view("ACI-SIM-VAV-3"),
        has_physical_zone_sensor=False,
        ahu_model=ahu,
    )
    ahu.set_vav_models([vav])
    manager.set_cooling_coils([ahu])

    for alias in (
        "chiller_enable",
        "chiller_ss",
        "chw_iso_valve",
        "chw_pump_ss",
        "cw_pump_ss",
        "ct_fan_ss",
    ):
        await _write(registry, "ACI-SIM-CHILLER-1", alias, True)
    await _write(registry, "ACI-SIM-AHU-1", "sa_fan_ss", True)
    await _write(registry, "ACI-SIM-AHU-1", "cooling_valve", 100.0)
    await _write(registry, "ACI-SIM-VAV-3", "damper_position_command", 100.0)
    await _write(registry, "ACI-SIM-VAV-3", "airflow_setpoint", 350.0)

    for _ in range(900):
        for model in (site, chiller, manager, ahu, vav):
            model.tick(1.0)

    assert ahu.cooling_coil_load_btuh > 5_000.0
    water_load_btuh = (
        500.0
        * ahu.cooling_coil_chw_flow_gpm
        * (ahu.cooling_coil_chwr_temp_f - manager.supply_temp_f)
    )
    assert water_load_btuh == pytest.approx(
        ahu.cooling_coil_load_btuh,
        rel=0.001,
        abs=10.0,
    )
    assert manager.return_temp_f > manager.supply_temp_f
