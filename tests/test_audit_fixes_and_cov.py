"""
Tests for the 2026-07 audit fixes (SIMULATION_AUDIT.md) and the COV
notification delivery verification that unblocks all three WebCTRL refresh
strategies: polling (<31 s), UnconfirmedCOV (>=31 s), and ConfirmedCOV
(>=1 min ending :01).

Covers:
  - VAV reheat discharge clamp (audit 3.1)
  - Chiller flow-proving interlock + tower wet-bulb physics (3.2/3.3)
  - Boiler circ-pump interlock + Boiler Manager enable wiring (3.4/2.1)
  - Manager aggregators: chillerN_ok / boilerN_ok / common header (2.1)
  - Remote shutdown plant command (2.1)
  - reliability_fail sets the BACnet Reliability property (2.3)
  - Scenario/instructor force on BINARY writable points (live-caught bug)
  - Engine start refuses to corrupt state off the event loop (live-caught bug)
  - Confirmed AND unconfirmed COV notification delivery over real BACnet/IP
"""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from bacpypes3.app import Application
from bacpypes3.apdu import AbortPDU
from bacpypes3.basetypes import Reliability
from bacpypes3.local.device import DeviceObject
from bacpypes3.local.networkport import NetworkPortObject
from bacpypes3.pdu import Address, IPv4Address
from bacpypes3.primitivedata import ObjectIdentifier, Real

from app.config_models import EquipmentGroupConfig, NetworkConfig, SupervisoryDeviceConfig
from app.engine import SimulationEngine
from app.equipment.boiler import BoilerModel, BoilerParameters
from app.equipment.chiller import ChillerModel, ChillerParameters
from app.equipment.managers import BoilerManagerModel, ChwPlantManagerModel
from app.equipment.site import SiteModel
from app.equipment.vav_single_duct import SingleDuctVavModel, VavParameters
from app.faults import FaultManager, FaultType
from app.registry import PointRegistry
from app.scenario import ScenarioEngine
from app.transport import BacnetTransport, NetworkGuardedApplication

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

# distinct range from test_bacnet_integration.py's 47850+ block
_next_port = [47880]


async def test_transport_stop_cancels_cov_lifetimes_before_closing_links() -> None:
    subscriptions = [SimpleNamespace(), SimpleNamespace()]
    detection = SimpleNamespace(cov_subscriptions=subscriptions)
    link_layer = MagicMock()
    app = MagicMock()
    app._cov_detections = {("analog-input", 1): detection}
    app.link_layers = {"test": link_layer}

    transport = BacnetTransport.__new__(BacnetTransport)
    transport.app = app
    transport.supervisory_config = SimpleNamespace(device_name="TEST")

    transport.stop()

    assert app.cancel_subscription.call_count == 2
    app.cancel_subscription.assert_any_call(subscriptions[0])
    app.cancel_subscription.assert_any_call(subscriptions[1])
    link_layer.close.assert_called_once()
    assert transport.app is None

pytestmark = pytest.mark.asyncio


def _allocate_ports() -> tuple[int, int]:
    server_port = _next_port[0]
    client_port = _next_port[0] + 1
    _next_port[0] += 2
    return server_port, client_port


def _group(filename: str) -> EquipmentGroupConfig:
    with open(CONFIG_DIR / "devices" / filename) as f:
        return EquipmentGroupConfig.model_validate(json.load(f))


async def _write_commanded(registry: PointRegistry, key: str, value) -> None:
    """Real priority-array write, same path a WebCTRL WriteProperty takes."""
    obj = registry.all_points()[key].bacnet_object
    if isinstance(value, bool):
        from bacpypes3.basetypes import BinaryPV

        await obj.write_property("presentValue", BinaryPV("active" if value else "inactive"), priority=8)
    else:
        await obj.write_property("presentValue", Real(float(value)), priority=8)


# ---------------------------------------------------------------------------
# 3.1 VAV reheat clamp
# ---------------------------------------------------------------------------

async def test_vav_discharge_temp_clamped_at_hot_water_temp():
    registry = PointRegistry([_group("vav_1.json")])
    registry.build_objects()
    view = registry.view("ACI-SIM-VAV-1")
    vav = SingleDuctVavModel(
        "ACI-SIM-VAV-1", view,
        parameters=VavParameters(hot_water_supply_temp_f=140.0, thermal_time_constant_seconds=5.0),
    )
    # Normal heating mode: occupied minimum flow, reheat wide open.
    await _write_commanded(registry, "ACI-SIM-VAV-1.damper_position_command", 25.0)
    await _write_commanded(registry, "ACI-SIM-VAV-1.hw_valve_command", 100.0)
    for _ in range(600):
        vav.tick(1.0)
    discharge = view.get("discharge_temp")
    assert discharge <= 95.0 + 0.5, (
        f"discharge temp {discharge:.0f}F exceeds the configured VAV DAT limit"
    )
    assert discharge > 85.0, "reheat at occupied minimum flow should approach the 90-95F heating range"


# ---------------------------------------------------------------------------
# 3.2 / 3.3 chiller interlocks and tower physics
# ---------------------------------------------------------------------------

def _build_chiller_stack(fm: FaultManager | None = None):
    registry = PointRegistry([_group("site.json"), _group("chw_plant.json"), _group("chiller_1.json")])
    registry.build_objects()
    site_view = registry.view("ACI-SIM-SITE", fault_manager=fm)
    plant_view = registry.view("ACI-SIM-CHW-PLANT", fault_manager=fm)
    chiller_view = registry.view("ACI-SIM-CHILLER-1", fault_manager=fm)
    site = SiteModel("ACI-SIM-SITE", site_view)
    chiller = ChillerModel(
        "ACI-SIM-CHILLER-1", chiller_view, site_registry=site_view, plant_registry=plant_view,
        parameters=ChillerParameters(start_delay_seconds=10.0, pump_start_delay_seconds=1.0),
    )
    return registry, site, chiller


async def _command_chiller_on(registry: PointRegistry, pumps: bool) -> None:
    await _write_commanded(registry, "ACI-SIM-CHILLER-1.chiller_enable", True)
    await _write_commanded(registry, "ACI-SIM-CHILLER-1.chiller_ss", True)
    if pumps:
        await _write_commanded(registry, "ACI-SIM-CHILLER-1.chw_iso_valve", True)
        await _write_commanded(registry, "ACI-SIM-CHILLER-1.chw_pump_ss", True)
        await _write_commanded(registry, "ACI-SIM-CHILLER-1.cw_pump_ss", True)


async def test_chiller_never_proves_without_water_flow():
    registry, site, chiller = _build_chiller_stack()
    await _command_chiller_on(registry, pumps=False)
    for _ in range(120):
        site.tick(1.0)
        chiller.tick(1.0)
    assert not chiller.proven, "chiller proved with both pumps off -- flow-proving interlock missing"


async def test_chiller_proves_with_flow_then_trips_on_pump_loss():
    registry, site, chiller = _build_chiller_stack()
    await _command_chiller_on(registry, pumps=True)
    for _ in range(60):
        site.tick(1.0)
        chiller.tick(1.0)
    assert chiller.proven, "chiller should prove with enable+ss+both pumps after the start delay"

    await _write_commanded(registry, "ACI-SIM-CHILLER-1.chw_pump_ss", False)
    for _ in range(30):
        site.tick(1.0)
        chiller.tick(1.0)
    assert not chiller.proven, "losing evaporator flow while running must drop proof"


async def test_tower_fan_off_makes_condenser_water_climb_not_cool():
    registry, site, chiller = _build_chiller_stack()
    await _command_chiller_on(registry, pumps=True)
    # tower fan deliberately OFF while the machine runs
    for _ in range(400):
        site.tick(1.0)
        chiller.tick(1.0)
    view = registry.view("ACI-SIM-CHILLER-1")
    oa = registry.view("ACI-SIM-SITE").get("oa_temp")
    cws_no_fan = view.get("cws_temp")
    assert cws_no_fan > oa + 10.0, (
        f"CWS {cws_no_fan:.1f}F with no tower fan should climb well above OA {oa:.1f}F (heat rejection has nowhere to go)"
    )

    # now start the tower fan: evaporative cooling pulls CWS back DOWN toward wet-bulb + approach
    await _write_commanded(registry, "ACI-SIM-CHILLER-1.ct_fan_ss", True)
    for _ in range(400):
        site.tick(1.0)
        chiller.tick(1.0)
    cws_fan_on = view.get("cws_temp")
    assert cws_fan_on < cws_no_fan - 5.0, "starting the tower fan must cool the condenser water"


# ---------------------------------------------------------------------------
# 3.4 / 2.1 boiler interlock + manager enable
# ---------------------------------------------------------------------------

def _build_boiler_stack():
    registry = PointRegistry([_group("boiler_mgr.json"), _group("boiler_1.json")])
    registry.build_objects()
    mgr_view = registry.view("ACI-SIM-BOILER-MGR")
    boiler = BoilerModel(
        "ACI-SIM-BOILER-1", registry.view("ACI-SIM-BOILER-1"),
        parameters=BoilerParameters(purge_seconds=5.0, ignition_seconds=5.0, pump_start_delay_seconds=1.0),
        manager_registry=mgr_view, manager_enable_alias="enable_boiler1",
    )
    return registry, mgr_view, boiler


async def test_boiler_never_proves_without_circ_pump():
    registry, _, boiler = _build_boiler_stack()
    await _write_commanded(registry, "ACI-SIM-BOILER-1.boiler_ss", True)
    for _ in range(60):
        boiler.tick(1.0)
    assert not boiler.proven, "boiler proved with the circ pump off -- low-water/flow interlock missing"


async def test_boiler_manager_enable_starts_the_boiler():
    registry, _, boiler = _build_boiler_stack()
    # ONLY the manager-level enable, plus the pump -- no unit-level boiler_ss
    await _write_commanded(registry, "ACI-SIM-BOILER-MGR.enable_boiler1", True)
    await _write_commanded(registry, "ACI-SIM-BOILER-1.circ_pump_ss", True)
    for _ in range(30):
        boiler.tick(1.0)
    assert boiler.proven, "the Boiler Manager's enable_boiler1 point must be able to start the boiler"


# ---------------------------------------------------------------------------
# 2.1 manager aggregators
# ---------------------------------------------------------------------------

async def test_plant_manager_mirrors_proof_and_moves_common_header():
    registry = PointRegistry([
        _group("site.json"), _group("chw_plant.json"),
        _group("chiller_1.json"), _group("chiller_2.json"), _group("chiller_3.json"),
    ])
    registry.build_objects()
    site_view = registry.view("ACI-SIM-SITE")
    plant_view = registry.view("ACI-SIM-CHW-PLANT")
    site = SiteModel("ACI-SIM-SITE", site_view)
    chillers = [
        ChillerModel(
            f"ACI-SIM-CHILLER-{n}", registry.view(f"ACI-SIM-CHILLER-{n}"),
            site_registry=site_view, plant_registry=plant_view,
            parameters=ChillerParameters(start_delay_seconds=5.0, pump_start_delay_seconds=1.0),
        )
        for n in (1, 2, 3)
    ]
    manager = ChwPlantManagerModel("ACI-SIM-CHW-PLANT", plant_view, chillers)

    assert plant_view.get("chiller1_ok") == 0.0

    await _write_commanded(registry, "ACI-SIM-CHILLER-1.chiller_enable", True)
    await _write_commanded(registry, "ACI-SIM-CHILLER-1.chiller_ss", True)
    await _write_commanded(registry, "ACI-SIM-CHILLER-1.chw_iso_valve", True)
    await _write_commanded(registry, "ACI-SIM-CHILLER-1.chw_pump_ss", True)
    await _write_commanded(registry, "ACI-SIM-CHILLER-1.cw_pump_ss", True)
    for _ in range(400):
        site.tick(1.0)
        for c in chillers:
            c.tick(1.0)
        manager.tick(1.0)

    assert plant_view.get("chiller1_ok") == 1.0, "manager must mirror chiller 1's proof"
    assert plant_view.get("chiller2_ok") == 0.0, "chiller 2 was never commanded"
    header = plant_view.get("chws_temp_common")
    assert 44.0 < header < 60.0, (
        f"common header {header:.1f}F should be pulling the finite loop inventory "
        "down from its 70F idle temperature toward setpoint"
    )
    assert plant_view.get("chws_flow_common") > 0.0, "flow header must reflect the running CHW pump"


async def test_remote_shutdown_stops_a_proven_chiller():
    registry, site, chiller = _build_chiller_stack()
    await _command_chiller_on(registry, pumps=True)
    for _ in range(60):
        site.tick(1.0)
        chiller.tick(1.0)
    assert chiller.proven

    await _write_commanded(registry, "ACI-SIM-CHW-PLANT.remote_shutdown", True)
    for _ in range(10):
        site.tick(1.0)
        chiller.tick(1.0)
    assert not chiller.proven, "plant-level Remote Shutdown must stop the unit"


async def test_boiler_manager_mirrors_proof():
    registry, mgr_view, boiler = _build_boiler_stack()
    manager = BoilerManagerModel("ACI-SIM-BOILER-MGR", mgr_view, [boiler])
    manager.boilers = [boiler]  # single-boiler variant: boiler1_ok only

    await _write_commanded(registry, "ACI-SIM-BOILER-1.boiler_ss", True)
    await _write_commanded(registry, "ACI-SIM-BOILER-1.circ_pump_ss", True)
    for _ in range(30):
        boiler.tick(1.0)
        manager.tick(1.0)
    assert mgr_view.get("boiler1_ok") == 1.0


# ---------------------------------------------------------------------------
# 2.3 reliability flagging
# ---------------------------------------------------------------------------

async def test_reliability_fail_sets_and_restores_bacnet_reliability():
    fm = FaultManager()
    registry = PointRegistry([_group("vav_1.json")])
    registry.build_objects()
    view = registry.view("ACI-SIM-VAV-1", fault_manager=fm)
    vav = SingleDuctVavModel("ACI-SIM-VAV-1", view, parameters=VavParameters())

    obj = registry.all_points()["ACI-SIM-VAV-1.discharge_temp"].bacnet_object
    vav.tick(1.0)
    assert obj.reliability in (None, Reliability("noFaultDetected"))

    fm.set_fault("r1", FaultType.reliability_fail, "ACI-SIM-VAV-1", "discharge_temp", {"value": -50.0})
    vav.tick(1.0)
    assert obj.reliability == Reliability("noSensor"), "reliability_fail must flag the object unreliable"
    assert view.get("discharge_temp") == pytest.approx(-50.0), "and still substitute the bad reading"

    fm.clear_fault("r1")
    vav.tick(1.0)
    assert obj.reliability == Reliability("noFaultDetected"), "clearing the fault must restore reliability"


# ---------------------------------------------------------------------------
# live-caught: binary force writes + engine start off-loop
# ---------------------------------------------------------------------------

async def test_instructor_force_works_on_binary_writable_points():
    fm = FaultManager()
    registry = PointRegistry([_group("ahu_1.json")])
    registry.build_objects()
    engine = ScenarioEngine(fm, registry, get_sim_seconds=lambda: 0.0, get_equipment=lambda: [])

    engine._apply_force_or_release("ACI-SIM-AHU-1", "sa_fan_ss", "set_value", True)
    await asyncio.sleep(0.05)  # let the scheduled priority write land
    obj = registry.all_points()["ACI-SIM-AHU-1.sa_fan_ss"].bacnet_object
    assert str(obj.presentValue) == "active", "binary force must land in the priority array (was TypeError before)"

    engine._apply_force_or_release("ACI-SIM-AHU-1", "sa_fan_ss", "release_value", None)
    await asyncio.sleep(0.05)
    assert str(obj.presentValue) == "inactive", "release must clear the instructor priority slot"


async def test_instructor_release_clears_analog_priority_slot():
    fm = FaultManager()
    registry = PointRegistry([_group("vav_3.json")])
    registry.build_objects()
    engine = ScenarioEngine(fm, registry, get_sim_seconds=lambda: 0.0, get_equipment=lambda: [])
    obj = registry.all_points()["ACI-SIM-VAV-3.airflow_setpoint"].bacnet_object

    engine._apply_force_or_release(
        "ACI-SIM-VAV-3",
        "airflow_setpoint",
        "set_value",
        350.0,
    )
    await asyncio.sleep(0.05)
    assert float(obj.presentValue) == 350.0

    engine._apply_force_or_release(
        "ACI-SIM-VAV-3",
        "airflow_setpoint",
        "release_value",
        None,
    )
    await asyncio.sleep(0.05)
    assert float(obj.presentValue) == 120.0, "release must restore the configured relinquish default"


async def test_stop_all_relinquishes_every_tracked_priority_3_override():
    fm = FaultManager()
    registry = PointRegistry([_group("ahu_1.json"), _group("vav_3.json")])
    registry.build_objects()
    engine = ScenarioEngine(fm, registry, get_sim_seconds=lambda: 0.0, get_equipment=lambda: [])

    binary = registry.all_points()["ACI-SIM-AHU-1.sa_fan_ss"].bacnet_object
    analog = registry.all_points()["ACI-SIM-VAV-3.airflow_setpoint"].bacnet_object
    from bacpypes3.basetypes import BinaryPV
    from bacpypes3.primitivedata import Real

    await binary.write_property(
        "presentValue",
        BinaryPV("inactive"),
        priority=8,
    )
    await analog.write_property("presentValue", Real(250.0), priority=8)

    engine._apply_force_or_release("ACI-SIM-AHU-1", "sa_fan_ss", "set_value", True)
    engine._apply_force_or_release(
        "ACI-SIM-VAV-3",
        "airflow_setpoint",
        "set_value",
        350.0,
    )
    await engine.drain_priority_writes()
    assert str(binary.presentValue) == "active"
    assert float(analog.presentValue) == 350.0
    assert engine.is_priority_forced("ACI-SIM-AHU-1", "sa_fan_ss")
    assert engine.is_priority_forced("ACI-SIM-VAV-3", "airflow_setpoint")

    engine.reset()
    await engine.drain_priority_writes()

    assert str(binary.presentValue) == "inactive"
    assert float(analog.presentValue) == 250.0
    assert binary.priorityArray[2].dict_contents() == {"null": ()}
    assert analog.priorityArray[2].dict_contents() == {"null": ()}
    assert not engine._priority_overrides


async def test_instructor_force_rejects_out_of_range_commandable_value():
    fm = FaultManager()
    registry = PointRegistry([_group("vav_3.json")])
    registry.build_objects()
    engine = ScenarioEngine(fm, registry, get_sim_seconds=lambda: 0.0, get_equipment=lambda: [])
    obj = registry.all_points()["ACI-SIM-VAV-3.damper_position_command"].bacnet_object
    original = float(obj.presentValue)

    accepted = engine._apply_force_or_release(
        "ACI-SIM-VAV-3",
        "damper_position_command",
        "set_value",
        500.0,
    )
    await engine.drain_priority_writes()

    assert accepted is False
    assert float(obj.presentValue) == original
    assert not engine._priority_overrides


async def test_engine_lifecycle_is_explicitly_async_and_clean():
    engine = SimulationEngine(equipment=[])
    await engine.start()
    assert engine.running is True
    assert engine._task is not None
    await engine.stop()
    assert engine.running is False
    assert engine._task is None


# ---------------------------------------------------------------------------
# single-point connection: peer allowlist drops non-allowlisted sources
# ---------------------------------------------------------------------------

async def test_peer_allowlist_blocks_unlisted_sources_and_admits_listed():
    sup = SupervisoryDeviceConfig(device_instance=242960, device_name="ACI-SIM-PEER-TEST")

    async def read_with_allowlist(allowlist):
        server_port, client_port = _allocate_ports()  # fresh pair per attempt; sockets aren't closed between runs
        registry = PointRegistry([_group("site.json")])
        transport = BacnetTransport(
            NetworkConfig(
                bind_address="127.0.0.1", subnet_bits=24, udp_port=server_port,
                respond_to_who_is=True, write_source_allowlist=[], peer_allowlist=allowlist,
            ),
            sup, registry,
        )
        app = transport.start()
        try:
            client = Application.from_object_list([
                DeviceObject(objectIdentifier=("device", 599997), objectName="PeerTestClient", vendorIdentifier=999),
                NetworkPortObject(
                    IPv4Address(f"127.0.0.1/24:{client_port}"),
                    objectIdentifier=("network-port", 1), objectName="NetworkPort-1",
                ),
            ])
            oa_id = registry.all_points()["ACI-SIM-SITE.oa_temp"].global_instance
            try:
                value = await asyncio.wait_for(
                    client.read_property(
                        Address(f"127.0.0.1:{server_port}"),
                        ObjectIdentifier(f"analog-value,{oa_id}"), "present-value",
                    ),
                    timeout=4,
                )
                return value, app.messages_blocked
            except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001 - abort/timeout both mean "no answer"
                if type(e).__name__ in ("TimeoutError", "AbortPDU"):
                    return None, app.messages_blocked
                raise
        finally:
            transport.stop()

    value, blocked = await read_with_allowlist(["10.99.99.99"])  # our 127.0.0.1 client is NOT listed
    assert value is None, "a non-allowlisted source must get no reply at all"
    assert blocked >= 1, "the drop must be counted in messages_blocked"

    value, blocked = await read_with_allowlist(["127.0.0.1"])  # now we are listed
    assert value is not None and float(value) == pytest.approx(70.0)
    assert blocked == 0


# ---------------------------------------------------------------------------
# COV: confirmed and unconfirmed delivery over real BACnet/IP
# ---------------------------------------------------------------------------

async def test_confirmed_cov_no_response_is_counted_without_callback_exception():
    app = object.__new__(NetworkGuardedApplication)
    app.cov_notification_failures = 0
    app.last_cov_notification_failure = None
    future = MagicMock()
    future.result.side_effect = AbortPDU(reason="no-response")

    app.cov_confirmation(SimpleNamespace(), future)

    assert app.cov_notification_failures == 1
    assert app.last_cov_notification_failure["reason"] == "no-response"


@pytest.mark.parametrize("confirmed", [False, True], ids=["unconfirmed", "confirmed"])
async def test_cov_notification_delivery(confirmed):
    server_port, client_port = _allocate_ports()
    network_config = NetworkConfig(
        bind_address="127.0.0.1", subnet_bits=24, udp_port=server_port,
        respond_to_who_is=True, write_source_allowlist=[],
    )
    sup = SupervisoryDeviceConfig(device_instance=242950, device_name="ACI-SIM-COV-TEST")
    registry = PointRegistry([_group("site.json")])
    transport = BacnetTransport(network_config, sup, registry)
    transport.start()
    try:
        client = Application.from_object_list([
            DeviceObject(objectIdentifier=("device", 599998), objectName="CovTestClient", vendorIdentifier=999),
            NetworkPortObject(
                IPv4Address(f"127.0.0.1/24:{client_port}"),
                objectIdentifier=("network-port", 1), objectName="NetworkPort-1",
            ),
        ])
        oa_temp_id = registry.all_points()["ACI-SIM-SITE.oa_temp"].global_instance
        async with client.change_of_value(
            Address(f"127.0.0.1:{server_port}"),
            ObjectIdentifier(f"analog-value,{oa_temp_id}"),
            subscriber_process_identifier=6000 + int(confirmed),
            issue_confirmed_notifications=confirmed,
            lifetime=60,
        ) as scm:
            # greeting notification on subscribe
            prop, value = await asyncio.wait_for(scm.get_value(), timeout=10)
            assert str(prop) == "present-value"

            # change-driven notification, published exactly the way the
            # engine does it (GroupView.set on the registry)
            registry.view("ACI-SIM-SITE").set("oa_temp", float(value) + 5.0)
            prop, value2 = await asyncio.wait_for(scm.get_value(), timeout=10)
            assert prop is not None, f"no {'confirmed' if confirmed else 'unconfirmed'} notification on change"
    finally:
        transport.stop()
