"""Cross-equipment physics and command-center airflow-state regressions."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from bacpypes3.basetypes import BinaryPV
from bacpypes3.primitivedata import Real

from app.config_models import EquipmentGroupConfig
from app.diagnostics import CommandCenterDiagnostics
from app.equipment.ahu import AhuModel, AhuParameters
from app.equipment.boiler import BoilerModel
from app.equipment.chiller import ChillerModel
from app.equipment.managers import BoilerManagerModel, ChwPlantManagerModel
from app.equipment.site import SiteModel
from app.equipment.vav_single_duct import SingleDuctVavModel, VavParameters
from app.registry import PointRegistry


CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
pytestmark = pytest.mark.asyncio


def _group(filename: str) -> EquipmentGroupConfig:
    return EquipmentGroupConfig.model_validate(
        json.loads((CONFIG_DIR / "devices" / filename).read_text())
    )


async def _write(
    registry: PointRegistry,
    group_id: str,
    alias: str,
    value: float | bool,
    *,
    priority: int = 8,
) -> None:
    obj = registry.all_points()[f"{group_id}.{alias}"].bacnet_object
    if isinstance(value, bool):
        await obj.write_property(
            "presentValue",
            BinaryPV("active" if value else "inactive"),
            priority=priority,
        )
    else:
        await obj.write_property("presentValue", Real(value), priority=priority)


class MutableChwPlant:
    cooling_capacity_fraction = 0.0
    supply_temp_f = 54.0


class MutableHotWaterPlant:
    heating_capacity_fraction = 0.0
    supply_temp_f = 100.0


async def test_ahu_exposes_one_commandable_supply_air_setpoint_av() -> None:
    group = _group("ahu_1.json")
    matches = [point for point in group.points if point.alias == "sa_temp_setpoint"]

    assert len(matches) == 1
    point = matches[0]
    assert point.object_type.value == "analog-value"
    assert point.object_instance == 1
    assert point.units == "degrees-fahrenheit"
    assert point.signal_direction.value == "webctrl_to_sim"
    assert point.writable is True
    assert point.commandable is True
    assert point.minimum == 45.0
    assert point.maximum == 95.0
    assert point.relinquish_default == 55.0

    registry = PointRegistry([group])
    registry.build_objects()
    registered = registry.all_points()["ACI-SIM-AHU-1.sa_temp_setpoint"]
    assert registered.global_instance == 9001


async def test_vav_airflow_requires_ahu_supply_fan_proof() -> None:
    registry = PointRegistry([_group("site.json"), _group("ahu_1.json"), _group("vav_3.json")])
    registry.build_objects()
    site = registry.view("ACI-SIM-SITE")
    ahu = AhuModel("ACI-SIM-AHU-1", registry.view("ACI-SIM-AHU-1"), site)
    vav = SingleDuctVavModel(
        "ACI-SIM-VAV-3",
        registry.view("ACI-SIM-VAV-3"),
        ahu_model=ahu,
        has_physical_zone_sensor=False,
    )
    await _write(registry, "ACI-SIM-VAV-3", "damper_position_command", 100.0)
    await _write(registry, "ACI-SIM-VAV-3", "airflow_setpoint", 350.0)

    for _ in range(30):
        ahu.tick(1.0)
        vav.tick(1.0)

    assert registry.view("ACI-SIM-VAV-3").get("airflow") < 5.0
    assert vav.operating_snapshot()["mode"] == "off"

    await _write(registry, "ACI-SIM-AHU-1", "sa_fan_ss", True)
    for _ in range(90):
        ahu.tick(1.0)
        vav.tick(1.0)

    assert registry.view("ACI-SIM-VAV-3").get("airflow") > 330.0
    assert vav.operating_snapshot()["active"] is True


async def test_ahu_cooling_valve_requires_usable_chilled_water() -> None:
    registry = PointRegistry([_group("site.json"), _group("ahu_1.json")])
    registry.build_objects()
    registry._set("ACI-SIM-SITE.oa_temp", 80.0)
    chw = MutableChwPlant()
    ahu = AhuModel(
        "ACI-SIM-AHU-1",
        registry.view("ACI-SIM-AHU-1"),
        registry.view("ACI-SIM-SITE"),
        parameters=AhuParameters(coil_time_constant_seconds=20.0),
        chw_plant_model=chw,
    )
    await _write(registry, "ACI-SIM-AHU-1", "sa_fan_ss", True)
    await _write(registry, "ACI-SIM-AHU-1", "cooling_valve", 100.0)

    for _ in range(180):
        ahu.tick(1.0)
    unavailable_sat = registry.view("ACI-SIM-AHU-1").get("ahu_sa_temp")
    assert unavailable_sat > 68.0
    assert ahu.mechanical_cooling_available is False

    chw.cooling_capacity_fraction = 1.0
    chw.supply_temp_f = 44.0
    for _ in range(180):
        ahu.tick(1.0)
    available_sat = registry.view("ACI-SIM-AHU-1").get("ahu_sa_temp")
    assert 52.0 <= available_sat <= 58.0
    assert available_sat < unavailable_sat - 10.0
    assert ahu.conditioning_source == "mechanical-cooling"


async def test_ahu_sat_setpoint_does_not_bypass_webctrl_valve_commands() -> None:
    registry = PointRegistry([_group("site.json"), _group("ahu_1.json")])
    registry.build_objects()
    registry._set("ACI-SIM-SITE.oa_temp", 70.0)
    hot_water = MutableHotWaterPlant()
    hot_water.heating_capacity_fraction = 1.0
    hot_water.supply_temp_f = 180.0
    ahu = AhuModel(
        "ACI-SIM-AHU-1",
        registry.view("ACI-SIM-AHU-1"),
        registry.view("ACI-SIM-SITE"),
        boiler_plant_model=hot_water,
    )
    await _write(registry, "ACI-SIM-AHU-1", "sa_fan_ss", True)
    await _write(registry, "ACI-SIM-AHU-1", "sa_temp_setpoint", 85.0)

    for _ in range(600):
        ahu.tick(1.0)
    no_heat = ahu.operating_snapshot()
    assert no_heat["requested_conditioning"] == "heating"
    assert no_heat["conditioning_source"] == "neutral"
    assert no_heat["supply_air_temp_f"] < 78.0

    await _write(registry, "ACI-SIM-AHU-1", "heating_valve", 50.0)
    early_sat = None
    for second in range(600):
        ahu.tick(1.0)
        if second == 44:
            early_sat = ahu.effective_sa_temp_f

    heated = ahu.operating_snapshot()
    assert early_sat is not None and early_sat < 80.0
    assert 84.5 <= heated["supply_air_temp_f"] <= 85.5
    assert heated["supply_air_temp_setpoint_f"] == 85.0
    assert 49.0 <= heated["heating_valve_effective_pct"] <= 51.0
    assert heated["conditioning_source"] == "hot-water-heating"


async def test_ahu_cold_outdoor_air_requires_more_heating_valve() -> None:
    registry = PointRegistry([_group("site.json"), _group("ahu_1.json")])
    registry.build_objects()
    registry._set("ACI-SIM-SITE.oa_temp", 40.0)
    hot_water = MutableHotWaterPlant()
    hot_water.heating_capacity_fraction = 1.0
    hot_water.supply_temp_f = 180.0
    ahu = AhuModel(
        "ACI-SIM-AHU-1",
        registry.view("ACI-SIM-AHU-1"),
        registry.view("ACI-SIM-SITE"),
        boiler_plant_model=hot_water,
    )
    await _write(registry, "ACI-SIM-AHU-1", "sa_fan_ss", True)
    await _write(registry, "ACI-SIM-AHU-1", "sa_temp_setpoint", 85.0)
    await _write(registry, "ACI-SIM-AHU-1", "heating_valve", 50.0)

    for _ in range(900):
        ahu.tick(1.0)
    half_open = ahu.operating_snapshot()
    assert half_open["outside_air_fraction"] == 0.15
    assert half_open["supply_air_temp_f"] < 82.0
    assert half_open["supply_air_temp_error_f"] > 3.0

    await _write(registry, "ACI-SIM-AHU-1", "heating_valve", 72.0)
    for _ in range(900):
        ahu.tick(1.0)
    cold_weather_design = ahu.operating_snapshot()
    assert 84.0 <= cold_weather_design["supply_air_temp_f"] <= 86.0
    assert 71.0 <= cold_weather_design["heating_valve_effective_pct"] <= 73.0


async def test_ahu_distinguishes_valve_changeover_from_persistent_overlap() -> None:
    registry = PointRegistry([_group("site.json"), _group("ahu_1.json")])
    registry.build_objects()
    ahu = AhuModel(
        "ACI-SIM-AHU-1",
        registry.view("ACI-SIM-AHU-1"),
        registry.view("ACI-SIM-SITE"),
    )

    # Two valves driven open together is not a crossover and is immediately
    # exposed to the wall-clock command-center diagnostic.
    await _write(registry, "ACI-SIM-AHU-1", "cooling_valve", 40.0)
    await _write(registry, "ACI-SIM-AHU-1", "heating_valve", 40.0)
    ahu.tick(1.0)
    simultaneous = ahu.operating_snapshot()
    assert simultaneous["simultaneous_heating_cooling"] is True
    assert simultaneous["valve_overlap_pct"] == 40.0
    assert simultaneous["valve_changeover_active"] is False

    # Establish cooling operation, then cross-ramp toward heating. Material
    # overlap is allowed for one actuator-stroke interval.
    await _write(registry, "ACI-SIM-AHU-1", "heating_valve", 0.0)
    await _write(registry, "ACI-SIM-AHU-1", "cooling_valve", 80.0)
    for _ in range(120):
        ahu.tick(1.0)
    await _write(registry, "ACI-SIM-AHU-1", "cooling_valve", 40.0)
    await _write(registry, "ACI-SIM-AHU-1", "heating_valve", 40.0)
    for _ in range(20):
        ahu.tick(1.0)
    crossover = ahu.operating_snapshot()
    assert crossover["simultaneous_heating_cooling"] is False
    assert crossover["valve_changeover_active"] is True
    assert crossover["valve_changeover_remaining_seconds"] == 41.0

    # If both commands stop moving and remain open after the travel window,
    # the condition becomes a persistent energy-waste issue.
    for _ in range(42):
        ahu.tick(1.0)
    stuck = ahu.operating_snapshot()
    assert stuck["simultaneous_heating_cooling"] is True
    assert stuck["valve_changeover_active"] is False

    # A clean handoff remains legitimate while the outgoing physical actuator
    # finishes closing, because its commanded position is already zero.
    await _write(registry, "ACI-SIM-AHU-1", "cooling_valve", 0.0)
    await _write(registry, "ACI-SIM-AHU-1", "heating_valve", 50.0)
    ahu.tick(1.0)
    clean_handoff = ahu.operating_snapshot()
    assert clean_handoff["simultaneous_heating_cooling"] is False
    assert clean_handoff["valve_changeover_active"] is True
    assert clean_handoff["cooling_valve_effective_pct"] > 10.0


async def test_vav_reheat_requires_hot_water_distribution() -> None:
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
    hot_water = MutableHotWaterPlant()
    vav = SingleDuctVavModel(
        "ACI-SIM-VAV-1",
        registry.view("ACI-SIM-VAV-1"),
        parameters=VavParameters(thermal_time_constant_seconds=5.0),
        ahu_model=ahu,
        boiler_plant_model=hot_water,
    )
    await _write(registry, "ACI-SIM-VAV-1", "damper_position_command", 25.0)
    await _write(registry, "ACI-SIM-VAV-1", "hw_valve_command", 100.0)

    for _ in range(120):
        vav.tick(1.0)
    assert registry.view("ACI-SIM-VAV-1").get("discharge_temp") < 58.0
    assert vav.operating_snapshot()["mode"] == "ventilation"

    ahu.cooling_delivery_available = True
    ahu.conditioning_source = "mechanical-cooling"
    assert vav.operating_snapshot()["mode"] == "cooling"
    assert vav.operating_snapshot()["conditioning_source"] == "mechanical-cooling"
    ahu.cooling_delivery_available = False
    ahu.conditioning_source = "neutral"
    assert vav.operating_snapshot()["mode"] == "ventilation"

    hot_water.heating_capacity_fraction = 1.0
    hot_water.supply_temp_f = 180.0
    for _ in range(180):
        vav.tick(1.0)
    discharge = registry.view("ACI-SIM-VAV-1").get("discharge_temp")
    assert 88.0 <= discharge <= 95.5
    assert vav.operating_snapshot()["mode"] == "heating"


async def test_command_center_marks_vav_upstream_inhibited() -> None:
    class Registry:
        values = {
            "SITE.building_pressure": 0.05,
            "VAV.airflow_setpoint": 800.0,
            "VAV.airflow": 0.0,
        }
        groups = []

        def all_points(self):
            return {
                key: SimpleNamespace(config=SimpleNamespace(normal_range=None))
                for key in self.values
            }

        def _get(self, key):
            return self.values[key]

    class Vav:
        equipment_id = "VAV"

        @staticmethod
        def operating_snapshot():
            return {
                "active": False,
                "mode": "off",
                "conditioning_source": "neutral",
                "dependencies": {"ahu_proven": False},
            }

    layout = {
        "building": {
            "name": "Test",
            "asset": "test",
            "pressure": {"group_id": "SITE", "alias": "building_pressure"},
        },
        "locations": [
            {
                "id": "vav",
                "label": "VAV",
                "group_id": "VAV",
                "component_type": "vav",
                "floor": "1",
                "x": 50,
                "y": 50,
                "space": {"width": 10, "height": 8},
                "diagnostic": {
                    "type": "vav_airflow",
                    "setpoint_alias": "airflow_setpoint",
                    "airflow_alias": "airflow",
                },
            }
        ],
    }
    diagnostics = CommandCenterDiagnostics(
        Registry(),
        layout,
        equipment_provider=lambda: [Vav()],
    )
    payload = diagnostics.snapshot()
    assert payload["locations"][0]["state"] == "inhibited"
    assert payload["locations"][0]["air_delivery"]["mode"] == "off"
    assert payload["summary"]["inhibited"] == 1


async def test_real_plant_to_ahu_to_vav_chain_delivers_cooling_and_reheat() -> None:
    registry = PointRegistry(
        [
            _group("site.json"),
            _group("chw_plant.json"),
            _group("chiller_1.json"),
            _group("boiler_mgr.json"),
            _group("boiler_1.json"),
            _group("ahu_1.json"),
            _group("vav_3.json"),
        ]
    )
    registry.build_objects()
    site_view = registry.view("ACI-SIM-SITE")
    chw_view = registry.view("ACI-SIM-CHW-PLANT")
    boiler_mgr_view = registry.view("ACI-SIM-BOILER-MGR")
    site = SiteModel("ACI-SIM-SITE", site_view)
    chiller = ChillerModel(
        "ACI-SIM-CHILLER-1",
        registry.view("ACI-SIM-CHILLER-1"),
        site_registry=site_view,
        plant_registry=chw_view,
    )
    boiler = BoilerModel(
        "ACI-SIM-BOILER-1",
        registry.view("ACI-SIM-BOILER-1"),
        manager_registry=boiler_mgr_view,
        manager_enable_alias="enable_boiler1",
    )
    chw_manager = ChwPlantManagerModel("ACI-SIM-CHW-PLANT", chw_view, [chiller])
    boiler_manager = BoilerManagerModel(
        "ACI-SIM-BOILER-MGR",
        boiler_mgr_view,
        [boiler],
    )
    ahu = AhuModel(
        "ACI-SIM-AHU-1",
        registry.view("ACI-SIM-AHU-1"),
        site_view,
        chw_plant_model=chw_manager,
        boiler_plant_model=boiler_manager,
    )
    vav = SingleDuctVavModel(
        "ACI-SIM-VAV-3",
        registry.view("ACI-SIM-VAV-3"),
        has_physical_zone_sensor=False,
        ahu_model=ahu,
        boiler_plant_model=boiler_manager,
    )
    equipment = [site, chiller, boiler, chw_manager, boiler_manager, ahu, vav]

    for alias in (
        "chiller_enable",
        "chiller_ss",
        "chw_iso_valve",
        "chw_pump_ss",
        "cw_pump_ss",
        "ct_fan_ss",
    ):
        await _write(registry, "ACI-SIM-CHILLER-1", alias, True)
    await _write(registry, "ACI-SIM-CHILLER-1", "chws_stpt_reset", 44.0)
    await _write(registry, "ACI-SIM-AHU-1", "sa_fan_ss", True)
    await _write(registry, "ACI-SIM-AHU-1", "cooling_valve", 100.0)
    await _write(registry, "ACI-SIM-VAV-3", "damper_position_command", 100.0)
    await _write(registry, "ACI-SIM-VAV-3", "airflow_setpoint", 350.0)

    for _ in range(600):
        for model in equipment:
            model.tick(1.0)

    cooling = vav.operating_snapshot()
    assert chw_manager.cooling_available is True
    assert 52.0 <= ahu.effective_sa_temp_f <= 58.0
    assert cooling["mode"] == "cooling"
    assert cooling["airflow_cfm"] > 330.0
    assert cooling["discharge_temp_f"] < cooling["zone_temp_f"] - 2.0

    await _write(registry, "ACI-SIM-AHU-1", "cooling_valve", 0.0)
    await _write(registry, "ACI-SIM-BOILER-1", "boiler_ss", True)
    await _write(registry, "ACI-SIM-BOILER-1", "circ_pump_ss", True)
    await _write(registry, "ACI-SIM-BOILER-1", "hw_pump_ss", True)
    await _write(registry, "ACI-SIM-BOILER-1", "hws_stpt_reset", 180.0)
    await _write(registry, "ACI-SIM-VAV-3", "hw_valve_command", 100.0)
    await _write(registry, "ACI-SIM-VAV-3", "airflow_setpoint", 300.0)
    await _write(registry, "ACI-SIM-VAV-3", "damper_position_command", 25.0)

    for _ in range(900):
        for model in equipment:
            model.tick(1.0)

    heating = vav.operating_snapshot()
    assert boiler_manager.heating_available is True
    assert 88.0 <= heating["discharge_temp_f"] <= 95.0
    assert heating["mode"] == "heating"
    assert heating["airflow_cfm"] <= 310.0
    assert heating["discharge_temp_f"] > heating["zone_temp_f"] + 2.0
