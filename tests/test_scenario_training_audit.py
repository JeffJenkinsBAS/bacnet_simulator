import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config_models import EquipmentGroupConfig
from app.diagnostics import CommandCenterDiagnostics
from app.faults import FaultManager
from app.registry import PointRegistry
from app.scenario import Scenario, ScenarioEngine


ROOT = Path(__file__).resolve().parent.parent


def _catalog_registry() -> PointRegistry:
    groups = [
        EquipmentGroupConfig.model_validate(json.loads(path.read_text()))
        for path in sorted((ROOT / "config" / "devices").glob("*.json"))
    ]
    return PointRegistry(groups)


def test_shipped_scenarios_validate_against_the_live_point_catalog() -> None:
    engine = ScenarioEngine(
        FaultManager(), _catalog_registry(), lambda: 0.0, lambda: []
    )
    engine.load_all(ROOT / "config" / "scenarios")

    assert len(engine.scenarios) >= 10
    assert all(scenario.observation_points for scenario in engine.scenarios.values() if scenario.scenario_id != "simulator_comm_loss")
    assert {"hot_water_reheat_load_response", "chilled_water_load_response"} <= set(engine.scenarios)


def test_scenario_schema_rejects_unsorted_or_incomplete_events() -> None:
    with pytest.raises(ValueError, match="ordered"):
        Scenario.model_validate(
            {
                "scenario_id": "bad_timeline",
                "title": "bad",
                "events": [
                    {"time_seconds": 10, "action": "set_weather", "parameters": {"outside_air_temperature": 80}},
                    {"time_seconds": 5, "action": "set_weather", "parameters": {"outside_air_temperature": 70}},
                ],
            }
        )
    with pytest.raises(ValueError, match="requires equipment and alias"):
        Scenario.model_validate(
            {
                "scenario_id": "bad_write",
                "title": "bad",
                "events": [{"time_seconds": 0, "action": "set_value", "value": 1}],
            }
        )


def test_stop_restores_pre_lesson_weather_targets_and_completed_effects_remain_explicit() -> None:
    clock = {"seconds": 0.0}
    site = SimpleNamespace(
        equipment_id="ACI-SIM-SITE",
        target_oa_temp_f=72.0,
        target_oa_humidity_pct=48.0,
    )
    engine = ScenarioEngine(
        FaultManager(), _catalog_registry(), lambda: clock["seconds"], lambda: [site]
    )
    engine.register_scenario(
        Scenario.model_validate(
            {
                "scenario_id": "weather_restore",
                "title": "Weather restore",
                "initial_conditions": {"outside_air_temperature": 20, "outside_air_humidity": 30},
                "events": [
                    {"time_seconds": 0, "action": "set_weather", "parameters": {"outside_air_temperature": 10}}
                ],
            }
        )
    )

    engine.start("weather_restore")
    assert (site.target_oa_temp_f, site.target_oa_humidity_pct) == (20.0, 30.0)
    engine.tick(1.0)
    assert site.target_oa_temp_f == 10.0
    assert engine.status()["status"] == "completed"
    assert engine.status()["effects_active"] is True
    engine.stop()
    assert (site.target_oa_temp_f, site.target_oa_humidity_pct) == (72.0, 48.0)


class _Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class _Registry:
    def __init__(self):
        self.values = {
            "SITE.building_pressure": 0.05,
            "UNIT.command": 1.0,
            "UNIT.status": 0.0,
        }
        self.groups = []
        self._points = {
            key: SimpleNamespace(config=SimpleNamespace(normal_range=None))
            for key in self.values
        }

    def all_points(self):
        return self._points

    def _get(self, key):
        return self.values[key]


def test_command_proof_diagnostic_separates_simulated_start_allowance_from_wall_failure_confirmation() -> None:
    wall = _Clock()
    simulated = _Clock()
    layout = {
        "building": {
            "name": "test",
            "asset": "test",
            "pressure": {"group_id": "SITE", "alias": "building_pressure"},
        },
        "locations": [
            {
                "id": "unit",
                "label": "Unit",
                "group_id": "UNIT",
                "component_type": "chiller",
                "floor": "plant",
                "x": 50,
                "y": 50,
                "diagnostic": {
                    "type": "binary_command_status",
                    "command_alias": "command",
                    "status_alias": "status",
                    "expected_proof_seconds": 120,
                },
            }
        ],
    }
    diagnostics = CommandCenterDiagnostics(
        _Registry(), layout, clock=wall, simulation_clock=simulated
    )

    first = diagnostics.snapshot()["locations"][0]
    assert first["state"] == "starting"
    assert first["expected_proof_seconds"] == 120.0
    simulated.value = 119.0
    wall.value = 30.0
    assert diagnostics.snapshot()["locations"][0]["state"] == "starting"

    simulated.value = 120.0
    assert diagnostics.snapshot()["locations"][0]["state"] == "tracking"
    wall.value = 45.0
    assert diagnostics.snapshot()["locations"][0]["state"] == "failure"
