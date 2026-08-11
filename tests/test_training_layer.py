from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from bacpypes3.primitivedata import Real
from fastapi.testclient import TestClient

from app.api import create_app
from app.config_models import EquipmentGroupConfig
from app.engine import SimulationEngine
from app.faults import FaultManager
from app.registry import PointRegistry
from app.scenario import Scenario, ScenarioEngine
from app.training import (
    PriorityReconciliationRequired,
    TrainingAuth,
    TrainingManager,
)


ROOT = Path(__file__).resolve().parent.parent


class FakeSite:
    equipment_id = "ACI-SIM-SITE"

    def __init__(self):
        self.target_oa_temp_f = 70.0
        self.target_oa_humidity_pct = 50.0
        self._oa_temp = 70.0
        self._oa_humidity = 50.0

    def tick(self, dt_seconds: float) -> None:
        self._oa_temp += (self.target_oa_temp_f - self._oa_temp) * min(1.0, dt_seconds / 30.0)
        self._oa_humidity += (self.target_oa_humidity_pct - self._oa_humidity) * min(1.0, dt_seconds / 30.0)


def _registry(*filenames: str) -> PointRegistry:
    groups = [
        EquipmentGroupConfig.model_validate(
            json.loads((ROOT / "config" / "devices" / filename).read_text())
        )
        for filename in filenames
    ]
    registry = PointRegistry(groups)
    registry.build_objects()
    return registry


def _manager(registry: PointRegistry, tmp_path: Path) -> TrainingManager:
    faults = FaultManager()
    engine = SimulationEngine([], fault_manager=faults)
    scenario = ScenarioEngine(
        faults,
        registry,
        get_sim_seconds=lambda: engine.simulated_seconds_elapsed,
        get_equipment=lambda: engine.equipment,
    )
    scenario.register_scenario(
        Scenario.model_validate(
            {
                "scenario_id": "training_test",
                "title": "Training test",
                "observation_points": ["ACI-SIM-SITE.oa_temp"],
                "events": [
                    {
                        "time_seconds": 5,
                        "action": "set_weather",
                        "parameters": {"outside_air_temperature": 80},
                    }
                ],
            }
        )
    )
    baselines = tmp_path / "baselines.json"
    baselines.write_text(
        json.dumps(
            {
                "baselines": [
                    {
                        "baseline_id": "neutral",
                        "version": "1.0.0",
                        "title": "Neutral",
                        "weather": {"outside_air_temperature": 70, "outside_air_humidity": 50},
                        "settle_seconds": 2,
                    }
                ]
            }
        )
    )
    outcomes = tmp_path / "outcomes.json"
    outcomes.write_text(
        json.dumps(
            {
                "scenarios": {
                    "training_test": [
                        {
                            "assertion_id": "oa_above_75",
                            "title": "OA rises",
                            "point": "ACI-SIM-SITE.oa_temp",
                            "operator": "gt",
                            "value": 75,
                            "start_seconds": 0,
                            "end_seconds": 20,
                            "for_seconds": 2,
                        }
                    ]
                }
            }
        )
    )
    manager = TrainingManager(
        engine=engine,
        registry=registry,
        fault_manager=faults,
        scenario_engine=scenario,
        equipment_factory=lambda: [FakeSite()],
        baseline_path=baselines,
        outcomes_path=outcomes,
        auth=TrainingAuth("246810", required=True),
        evidence_dir=tmp_path / "evidence",
    )
    engine.training_manager = manager
    return manager


@pytest.mark.asyncio
async def test_baseline_restore_requires_explicit_external_priority_decision(tmp_path: Path) -> None:
    registry = _registry("site.json", "ahu_1.json")
    manager = _manager(registry, tmp_path)
    setpoint = registry.all_points()["ACI-SIM-AHU-1.duct_static_pressure_setpoint"].bacnet_object
    await setpoint.write_property("presentValue", Real(1.5), priority=8)

    with pytest.raises(PriorityReconciliationRequired):
        await manager.restore_baseline("neutral", None)

    restored = await manager.restore_baseline("neutral", "retain")
    assert restored["settled"] is True
    report = manager.priority_report()
    assert report["external_count"] == 1
    assert report["external"][0]["priority"] == 8
    preflight = manager.preflight("training_test")
    assert preflight["can_start"] is True
    assert any(item["code"] == "retained-priorities" for item in preflight["warnings"])

    await setpoint.write_property("presentValue", Real(1.7), priority=8)
    changed = manager.preflight("training_test")
    assert changed["can_start"] is False
    assert any(item["code"] == "external-priorities" for item in changed["blockers"])

    await manager.restore_baseline("neutral", "release")
    assert manager.priority_report()["external_count"] == 0


@pytest.mark.asyncio
async def test_checkpoint_restore_rewinds_internal_physical_state(tmp_path: Path) -> None:
    manager = _manager(_registry("site.json"), tmp_path)
    await manager.restore_baseline("neutral", None)
    site = manager.engine.equipment[0]
    expected = site._oa_temp
    site._oa_temp = 123.0

    result = await manager.restore_checkpoint("neutral", None)

    assert result["from_checkpoint"] is True
    assert manager.engine.equipment[0]._oa_temp == pytest.approx(expected)
    assert manager.baseline_settled is True


@pytest.mark.asyncio
async def test_preflight_evidence_and_time_window_scoring(tmp_path: Path) -> None:
    manager = _manager(_registry("site.json"), tmp_path)
    await manager.restore_baseline("neutral", None)
    preflight = manager.preflight("training_test")
    assert preflight["can_start"] is True

    session = manager.start_session("training_test", "Team A", 1)
    assert session["score"] == 0.0
    site_point = manager.registry.all_points()["ACI-SIM-SITE.oa_temp"]
    site_point.bacnet_object.presentValue = 80.0
    manager.engine._advance_physics(3.0)
    result = manager.finish_session()

    assert result["score"] == 100.0
    assert result["sample_count"] == 3
    assert Path(result["evidence_path"]).exists()
    evidence = manager.evidence(result["run_id"])
    assert evidence["samples"]
    assert any(action["action"] == "session-start" for action in evidence["actions"])


def test_role_tokens_protect_mutations_but_keep_reads_public() -> None:
    auth = TrainingAuth("246810", required=True)
    manager = SimpleNamespace(
        auth=auth,
        current_baseline_id=None,
        current_baseline_version=None,
        baseline_settled=False,
        active_run_id=None,
        record_action=MagicMock(),
    )
    registry = MagicMock()
    registry.groups = []
    registry.all_points.return_value = {}
    transport = MagicMock()
    transport.registry = registry
    transport.app = None
    transport.supervisory_config = SimpleNamespace(device_name="test", device_instance=1)
    transport.network_config = SimpleNamespace(
        bind_address="127.0.0.1",
        udp_port=47808,
        private_lab_mode=True,
        respond_to_who_is=True,
        peer_allowlist=[],
    )
    engine = MagicMock()
    engine.equipment = []
    engine.start = AsyncMock()
    engine.status.return_value = {"running": False}
    scenario = MagicMock()
    scenario.active_priority_override_count = 0
    scenario.status.return_value = {"running": False}
    app = create_app(
        transport,
        engine,
        MagicMock(),
        scenario,
        MagicMock(),
        MagicMock(),
        training_manager=manager,
    )
    client = TestClient(app)

    assert client.get("/api/status").status_code == 200
    assert client.post("/api/simulation/start").status_code == 401

    student = client.post(
        "/api/training/auth/login",
        json={"role": "student", "label": "Student A"},
    ).json()["token"]
    assert client.post(
        "/api/simulation/start", headers={"Authorization": f"Bearer {student}"}
    ).status_code == 403

    assert client.post(
        "/api/training/auth/login", json={"role": "instructor", "pin": "bad"}
    ).status_code == 403
    instructor = client.post(
        "/api/training/auth/login",
        json={"role": "instructor", "pin": "246810", "label": "Instructor"},
    ).json()["token"]
    assert client.post(
        "/api/simulation/start", headers={"Authorization": f"Bearer {instructor}"}
    ).status_code == 200
    engine.start.assert_awaited_once()
