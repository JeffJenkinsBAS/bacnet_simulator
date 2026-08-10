"""Cold-restart and duct-static training API contracts."""
from __future__ import annotations

import json
import socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from bacpypes3.primitivedata import Real
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.api import create_app
from app.config_models import EquipmentGroupConfig, NetworkConfig, SupervisoryDeviceConfig
from app.faults import FaultManager, FaultType
from app.registry import PointRegistry
from app.scenario import ScenarioEngine
from app.transport import BacnetTransport


CONFIG_DIR = Path(__file__).resolve().parent.parent / "config" / "devices"


class FakeAhu:
    equipment_id = "ACI-SIM-AHU-1"

    def __init__(self) -> None:
        self.tuning = {
            "kp": 30.0,
            "ki": 0.25,
            "kd": 0.0,
            "interval_seconds": 1.0,
        }
        self.reset_calls = 0

    def duct_static_snapshot(self) -> dict:
        return {
            "available": True,
            "fan_command": False,
            "fan_status": False,
            "pid_active": False,
            "setpoint_inwc": 1.0,
            "actual_inwc": 0.0,
            "fan_speed_pct": 0.0,
            "tuning": dict(self.tuning),
            "history": [],
        }

    def ahu_command_center_snapshot(self, history_limit: int = 180) -> dict:
        return {
            **self.duct_static_snapshot(),
            "history_limit": history_limit,
            "sensors": {},
            "actuators": {},
            "safety": {},
        }

    def configure_duct_static_pid(self, **tuning) -> None:
        self.tuning = dict(tuning)

    def reset_duct_static_pid(self, *, clear_history: bool) -> None:
        assert clear_history is False
        self.reset_calls += 1

    def restore_duct_static_pid_defaults(self) -> None:
        self.tuning = {
            "kp": 30.0,
            "ki": 0.25,
            "kd": 0.0,
            "interval_seconds": 1.0,
        }


def _client(
    *,
    equipment_factory=None,
    equipment: list | None = None,
) -> tuple[TestClient, MagicMock, MagicMock, MagicMock]:
    registry = MagicMock()
    registry.groups = [SimpleNamespace(), SimpleNamespace()]
    registry.all_points.return_value = {
        f"TEST.point-{number}": SimpleNamespace()
        for number in range(329)
    }
    registry.reset_runtime_state = AsyncMock()
    transport = MagicMock()
    transport.registry = registry

    engine = MagicMock()
    engine.equipment = list(equipment or [])
    engine.diagnostics = None
    engine.stop = AsyncMock()
    engine.start = AsyncMock()
    engine.status.return_value = {
        "running": True,
        "speed_multiplier": 1.0,
        "simulated_seconds_elapsed": 0.0,
        "tick_count": 0,
        "equipment_count": len(engine.equipment),
    }

    scenario = MagicMock()
    scenario.drain_priority_writes = AsyncMock()
    scenario.active_priority_override_count = 0

    app = create_app(
        transport=transport,
        engine=engine,
        fault_manager=MagicMock(),
        scenario_engine=scenario,
        orchestration_service=MagicMock(),
        ollama_client=MagicMock(),
        equipment_factory=equipment_factory,
    )
    return TestClient(app), transport, engine, scenario


def test_restart_requires_a_configured_equipment_factory() -> None:
    client, transport, engine, scenario = _client()

    response = client.post("/api/simulation/restart")

    assert response.status_code == 503
    transport.stop.assert_not_called()
    engine.stop.assert_not_awaited()
    scenario.reset.assert_not_called()


def test_restart_rebuilds_bacnet_objects_and_starts_from_zero() -> None:
    old_equipment = SimpleNamespace(equipment_id="OLD")
    replacement = SimpleNamespace(equipment_id="NEW")
    factory = MagicMock(return_value=[replacement])
    client, transport, engine, scenario = _client(
        equipment_factory=factory,
        equipment=[old_equipment],
    )

    response = client.post("/api/simulation/restart")

    assert response.status_code == 200
    assert response.json()["restarted"] is True
    assert response.json()["bacnet_rebound"] is False
    assert response.json()["bacnet_session_preserved"] is True
    assert response.json()["cov_subscriptions_preserved"] is True
    assert response.json()["webctrl_commands_preserved"] is True
    assert response.json()["i_am_announced"] is True
    assert response.json()["faults_cleared"] is True
    assert response.json()["priority_overrides_cleared"] is True
    assert response.json()["fleet"] == {
        "group_count": 2,
        "total_point_count": 329,
    }
    engine.stop.assert_awaited_once()
    scenario.reset.assert_called_once()
    scenario.drain_priority_writes.assert_awaited_once()
    transport.stop.assert_not_called()
    transport.start.assert_not_called()
    transport.registry.reset_runtime_state.assert_awaited_once()
    factory.assert_called_once()
    assert engine.equipment == [replacement]
    assert engine.speed_multiplier == 1.0
    engine.reset_clock.assert_called_once()
    engine.start.assert_awaited_once()


def test_restart_restores_previous_operating_process_after_factory_failure() -> None:
    previous = SimpleNamespace(equipment_id="PREVIOUS")
    factory = MagicMock(side_effect=RuntimeError("factory failed"))
    client, transport, engine, _ = _client(
        equipment_factory=factory,
        equipment=[previous],
    )
    engine.speed_multiplier = 5.0

    response = client.post("/api/simulation/restart")

    assert response.status_code == 500
    assert response.json()["recovery"] == {
        "attempted": True,
        "engine_restored": True,
        "bacnet_session_preserved": True,
    }
    assert response.json()["recovery_error"] is None
    assert engine.equipment == [previous]
    assert engine.speed_multiplier == 5.0
    transport.start.assert_not_called()
    transport.stop.assert_not_called()
    assert engine.stop.await_count == 1
    engine.start.assert_awaited_once()


def test_diagnostics_failure_does_not_abort_a_successful_restart() -> None:
    replacement = SimpleNamespace(equipment_id="NEW")
    client, _, engine, _ = _client(
        equipment_factory=MagicMock(return_value=[replacement]),
    )
    engine.diagnostics = MagicMock()
    engine.diagnostics.tick.side_effect = RuntimeError("diagnostics failed")

    response = client.post("/api/simulation/restart")

    assert response.status_code == 200
    assert response.json()["restarted"] is True
    engine.start.assert_awaited_once()


def test_stop_all_reconciles_bacnet_reliability_after_fault_clear() -> None:
    client, transport, engine, scenario = _client()

    response = client.post("/api/simulation/stop-all")

    assert response.status_code == 200
    engine.stop.assert_awaited_once()
    scenario.reset.assert_called_once()
    scenario.drain_priority_writes.assert_awaited_once()
    transport.registry.synchronize_reliability.assert_called_once()


def test_duct_static_pid_endpoints_apply_reset_and_restore_tuning() -> None:
    ahu = FakeAhu()
    client, _, _, _ = _client(equipment=[ahu])

    status = client.get("/api/ahu/duct-static")
    assert status.status_code == 200
    assert status.json()["available"] is True

    command_center = client.get("/api/ahu/command-center?history_limit=37")
    assert command_center.status_code == 200
    assert command_center.json()["history_limit"] == 37

    configured = client.put(
        "/api/ahu/duct-static/pid",
        json={
            "kp": 45.0,
            "ki": 0.4,
            "kd": 2.0,
            "interval_seconds": 2.5,
        },
    )
    assert configured.status_code == 200
    assert configured.json()["tuning"] == {
        "kp": 45.0,
        "ki": 0.4,
        "kd": 2.0,
        "interval_seconds": 2.5,
    }

    invalid = client.put(
        "/api/ahu/duct-static/pid",
        json={
            "kp": 101.0,
            "ki": 0.4,
            "kd": 2.0,
            "interval_seconds": 2.5,
        },
    )
    assert invalid.status_code == 422

    reset = client.post("/api/ahu/duct-static/pid/reset")
    assert reset.status_code == 200
    assert ahu.reset_calls == 1

    defaults = client.post("/api/ahu/duct-static/pid/defaults")
    assert defaults.status_code == 200
    assert defaults.json()["tuning"]["kp"] == 30.0
    assert defaults.json()["tuning"]["ki"] == 0.25


@pytest.mark.asyncio
async def test_restart_route_preserves_transport_and_webctrl_priority_commands() -> None:
    groups = [
        EquipmentGroupConfig.model_validate(
            json.loads((CONFIG_DIR / filename).read_text())
        )
        for filename in ("site.json", "ahu_1.json")
    ]
    registry = PointRegistry(groups)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    faults = FaultManager()
    transport = BacnetTransport(
        NetworkConfig(
            bind_address="127.0.0.1",
            subnet_bits=24,
            udp_port=port,
            respond_to_who_is=True,
            write_source_allowlist=[],
        ),
        SupervisoryDeviceConfig(
            device_instance=242998,
            device_name="ACI-SIM-RESTART-TEST",
        ),
        registry,
        fault_manager=faults,
    )
    engine = MagicMock()
    engine.equipment = []
    engine.diagnostics = None
    engine.speed_multiplier = 1.0
    engine.stop = AsyncMock()
    engine.start = AsyncMock()
    engine.status.return_value = {
        "running": True,
        "speed_multiplier": 1.0,
        "simulated_seconds_elapsed": 0.0,
        "tick_count": 0,
        "equipment_count": 1,
    }
    scenario = ScenarioEngine(
        faults,
        registry,
        get_sim_seconds=lambda: 0.0,
        get_equipment=lambda: engine.equipment,
    )
    replacement = SimpleNamespace(equipment_id="REPLACEMENT")
    transport.start()
    first_app = transport.app
    app = create_app(
        transport=transport,
        engine=engine,
        fault_manager=faults,
        scenario_engine=scenario,
        orchestration_service=MagicMock(),
        ollama_client=MagicMock(),
        equipment_factory=lambda: [replacement],
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        first = await client.post("/api/simulation/restart")
        assert first.status_code == 200
        assert first.json()["fleet"]["total_point_count"] == sum(
            len(group.points) for group in groups
        )
        assert transport.app is first_app

        setpoint = registry.all_points()[
            "ACI-SIM-AHU-1.duct_static_pressure_setpoint"
        ].bacnet_object
        await setpoint.write_property(
            "presentValue",
            Real(1.5),
            priority=8,
        )
        faults.set_fault(
            "restart-test",
            FaultType.reliability_fail,
            "ACI-SIM-AHU-1",
            "duct_static_pressure",
            {"value": 4.5},
        )
        registry.view("ACI-SIM-AHU-1", fault_manager=faults).set(
            "duct_static_pressure",
            1.0,
        )

        second = await client.post("/api/simulation/restart")
        assert second.status_code == 200
        assert transport.app is first_app
        assert not faults.list_faults()
        assert registry.view("ACI-SIM-AHU-1").get_commanded(
            "duct_static_pressure_setpoint"
        ) == pytest.approx(1.5)
        assert (
            setpoint.priorityArray[7].dict_contents()
            != {"null": ()}
        ), "Restart must not release WebCTRL's priority-8 command"
        rebuilt_pressure = registry.all_points()[
            "ACI-SIM-AHU-1.duct_static_pressure"
        ].bacnet_object
        assert str(rebuilt_pressure.reliability) == "no-fault-detected"
        assert list(rebuilt_pressure.statusFlags) == [0, 0, 0, 0]

    transport.stop()
