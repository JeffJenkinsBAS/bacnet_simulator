import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config_models import EquipmentGroupConfig, validate_equipment_groups
from app.diagnostics import CommandCenterDiagnostics
from app.registry import PointRegistry


CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class FakeRegistry:
    def __init__(self, values: dict[str, float]) -> None:
        self.values = values
        self.groups = []
        self._points = {
            key: SimpleNamespace(config=SimpleNamespace(normal_range=None))
            for key in values
        }
        self._points["SITE.building_pressure"].config.normal_range = SimpleNamespace(
            low=0.03,
            high=0.10,
        )

    def all_points(self) -> dict:
        return dict(self._points)

    def _get(self, key: str) -> float:
        return self.values[key]


def _layout(diagnostic: dict) -> dict:
    return {
        "schema_version": 1,
        "coordinate_system": "percent",
        "building": {
            "name": "Test Building",
            "asset": "test",
            "pressure": {"group_id": "SITE", "alias": "building_pressure"},
        },
        "locations": [
            {
                "id": "test-location",
                "label": "Test Location",
                "group_id": "TEST",
                "component_type": "test",
                "floor": 1,
                "x": 50,
                "y": 50,
                "diagnostic": diagnostic,
            }
        ],
    }


def test_binary_command_status_requires_15_real_seconds_before_failure() -> None:
    clock = FakeClock()
    registry = FakeRegistry(
        {
            "SITE.building_pressure": 0.05,
            "TEST.start_stop": 1.0,
            "TEST.run_status": 0.0,
        }
    )
    diagnostics = CommandCenterDiagnostics(
        registry,
        _layout(
            {
                "type": "binary_command_status",
                "command_alias": "start_stop",
                "status_alias": "run_status",
            }
        ),
        clock=clock,
    )

    first = diagnostics.snapshot()
    assert first["locations"][0]["state"] == "tracking"
    assert first["summary"] == {
        "running": 0,
        "failure": 0,
        "failures": 0,
        "tracking": 1,
        "inhibited": 0,
        "idle": 0,
    }

    clock.now = 14.9
    assert diagnostics.snapshot()["locations"][0]["state"] == "tracking"

    clock.now = 15.0
    failed = diagnostics.snapshot()["locations"][0]
    assert failed["state"] == "failure"
    assert failed["mismatch_seconds"] == 15.0
    assert failed["values"]["command"] is True
    assert failed["values"]["status"] is False

    registry.values["TEST.run_status"] = 1.0
    recovered = diagnostics.snapshot()
    assert recovered["locations"][0]["state"] == "running"
    assert recovered["locations"][0]["mismatch_seconds"] == 0.0

    registry.values["TEST.start_stop"] = 0.0
    registry.values["TEST.run_status"] = 0.0
    assert diagnostics.snapshot()["locations"][0]["state"] == "idle"


def test_ahu_simultaneous_heating_and_cooling_requires_15_real_seconds() -> None:
    clock = FakeClock()
    registry = FakeRegistry(
        {
            "SITE.building_pressure": 0.05,
            "TEST.start_stop": 1.0,
            "TEST.run_status": 1.0,
        }
    )
    layout = _layout(
        {
            "type": "binary_command_status",
            "command_alias": "start_stop",
            "status_alias": "run_status",
        }
    )
    layout["locations"][0]["component_type"] = "ahu"
    equipment_snapshot = {
        "simultaneous_heating_cooling": True,
        "cooling_valve_command_pct": 40.0,
        "heating_valve_command_pct": 40.0,
        "cooling_valve_effective_pct": 39.0,
        "heating_valve_effective_pct": 38.0,
        "supply_air_temp_f": 70.0,
        "supply_air_temp_setpoint_f": 55.0,
        "valve_overlap_pct": 40.0,
        "valve_changeover_active": False,
    }
    equipment = SimpleNamespace(
        equipment_id="TEST",
        operating_snapshot=lambda: equipment_snapshot,
    )
    diagnostics = CommandCenterDiagnostics(
        registry,
        layout,
        clock=clock,
        equipment_provider=lambda: [equipment],
    )

    tracking = diagnostics.snapshot()["locations"][0]
    assert tracking["state"] == "tracking"
    assert tracking["diagnostic_type"] == "simultaneous_heating_cooling"
    assert tracking["values"]["valve_overlap_pct"] == 40.0

    clock.now = 15.0
    failed = diagnostics.snapshot()["locations"][0]
    assert failed["state"] == "failure"
    assert failed["mismatch_seconds"] == 15.0
    assert "wasting energy" in failed["message"]
    assert "WebCTRL priority locks" in failed["message"]

    equipment_snapshot["simultaneous_heating_cooling"] = False
    recovered = diagnostics.snapshot()["locations"][0]
    assert recovered["state"] == "running"
    assert recovered["mismatch_seconds"] == 0.0


def test_vav_airflow_uses_inclusive_25_percent_band_and_real_time_delay() -> None:
    clock = FakeClock()
    registry = FakeRegistry(
        {
            "SITE.building_pressure": 0.05,
            "TEST.airflow_setpoint": 1000.0,
            "TEST.airflow": 749.0,
        }
    )
    diagnostics = CommandCenterDiagnostics(
        registry,
        _layout(
            {
                "type": "vav_airflow",
                "setpoint_alias": "airflow_setpoint",
                "airflow_alias": "airflow",
            }
        ),
        clock=clock,
    )

    assert diagnostics.snapshot()["locations"][0]["state"] == "tracking"
    clock.now = 15.0
    assert diagnostics.snapshot()["locations"][0]["state"] == "failure"

    registry.values["TEST.airflow"] = 750.0
    assert diagnostics.snapshot()["locations"][0]["state"] == "running"
    registry.values["TEST.airflow"] = 1250.0
    assert diagnostics.snapshot()["locations"][0]["state"] == "running"

    registry.values["TEST.airflow"] = 1250.1
    clock.now = 20.0
    assert diagnostics.snapshot()["locations"][0]["state"] == "tracking"

    registry.values["TEST.airflow_setpoint"] = 0.0
    assert diagnostics.snapshot()["locations"][0]["state"] == "idle"


def test_deployed_layout_and_generated_catalog_are_complete_and_valid() -> None:
    groups = [
        EquipmentGroupConfig.model_validate(json.loads(path.read_text()))
        for path in sorted((CONFIG_DIR / "devices").glob("*.json"))
    ]
    validate_equipment_groups(groups)
    assert len(groups) == 28
    assert sum(len(group.points) for group in groups) == 355

    layout = json.loads((CONFIG_DIR / "building_layout.json").read_text())
    CommandCenterDiagnostics(PointRegistry(groups), layout)
    locations = layout["locations"]
    assert len(locations) == 34
    counts = {
        component_type: sum(
            1 for location in locations if location["component_type"] == component_type
        )
        for component_type in {
            "chiller",
            "cooling_tower",
            "pump",
            "boiler",
            "ahu",
            "exhaust_fan",
            "vav",
        }
    }
    assert counts == {
        "chiller": 3,
        "cooling_tower": 3,
        "pump": 6,
        "boiler": 3,
        "ahu": 1,
        "exhaust_fan": 1,
        "vav": 17,
    }
    assert all(0 <= location["x"] <= 100 and 0 <= location["y"] <= 100 for location in locations)
    vav_locations = [location for location in locations if location["component_type"] == "vav"]
    assert len(vav_locations) == 17
    assert all(
        1 <= location["space"]["width"] <= 40
        and 1 <= location["space"]["height"] <= 40
        for location in vav_locations
    )


@pytest.mark.asyncio
async def test_full_catalog_builds_and_drives_every_layout_location() -> None:
    groups = [
        EquipmentGroupConfig.model_validate(json.loads(path.read_text()))
        for path in sorted((CONFIG_DIR / "devices").glob("*.json"))
    ]
    registry = PointRegistry(groups)
    assert len(registry.build_objects()) == 355
    diagnostics = CommandCenterDiagnostics(
        registry,
        json.loads((CONFIG_DIR / "building_layout.json").read_text()),
    )

    payload = diagnostics.snapshot()
    assert len(payload["locations"]) == 34
    assert payload["summary"] == {
        "running": 0,
        "failure": 0,
        "failures": 0,
        "tracking": 15,
        "inhibited": 0,
        "idle": 19,
    }
    assert payload["building"]["pressure"]["source"] == "ACI-SIM-SITE.building_pressure"


def test_command_center_endpoint_exposes_ui_contract() -> None:
    clock = FakeClock()
    registry = FakeRegistry(
        {
            "SITE.building_pressure": 0.05,
            "TEST.start_stop": 0.0,
            "TEST.run_status": 0.0,
        }
    )
    diagnostics = CommandCenterDiagnostics(
        registry,
        _layout(
            {
                "type": "binary_command_status",
                "command_alias": "start_stop",
                "status_alias": "run_status",
            }
        ),
        clock=clock,
    )
    app = create_app(
        transport=MagicMock(),
        engine=MagicMock(),
        fault_manager=MagicMock(),
        scenario_engine=MagicMock(),
        orchestration_service=MagicMock(),
        ollama_client=MagicMock(),
        diagnostics=diagnostics,
    )

    response = TestClient(app).get("/api/command-center")
    assert response.status_code == 200
    payload = response.json()
    assert set(("building", "failure_delay_seconds", "summary", "locations")) <= payload.keys()
    assert payload["failure_delay_seconds"] == 15.0
    assert payload["building"]["pressure"] == {
        "value": 0.05,
        "normal_low": 0.03,
        "normal_high": 0.1,
        "state": "normal",
        "source": "SITE.building_pressure",
    }
    assert set(
        (
            "id",
            "label",
            "group_id",
            "component_type",
            "floor",
            "x",
            "y",
            "state",
            "diagnostic_type",
            "mismatch_seconds",
            "threshold_seconds",
            "values",
            "message",
        )
    ) <= payload["locations"][0].keys()


def test_layout_rejects_unknown_source_points() -> None:
    registry = FakeRegistry(
        {
            "SITE.building_pressure": 0.05,
            "TEST.start_stop": 0.0,
            "TEST.run_status": 0.0,
        }
    )
    layout = _layout(
        {
            "type": "binary_command_status",
            "command_alias": "missing_command",
            "status_alias": "run_status",
        }
    )
    with pytest.raises(ValueError, match="unknown points"):
        CommandCenterDiagnostics(registry, layout)
