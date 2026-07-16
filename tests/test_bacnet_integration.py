"""
Integration test: starts the real transport layer (a real bacpypes3
Application bound to loopback, hosting one equipment group's objects under
the single supervisory device) and a real bacpypes3 client, then performs
actual BACnet/IP ReadProperty and WriteProperty requests over the network
stack -- not a mock.

Uses a non-standard loopback port so it never collides with a real BACnet
device on the machine running the test suite. Object instances below use
ACI-SIM-VAV-1's real offset (11000) plus its local instance numbers, exactly
as they'd appear on the wire in production.
"""
import json
from pathlib import Path

import pytest
from bacpypes3.app import Application
from bacpypes3.local.device import DeviceObject
from bacpypes3.local.networkport import NetworkPortObject
from bacpypes3.pdu import IPv4Address

from app.config_models import EquipmentGroupConfig, NetworkConfig, SupervisoryDeviceConfig, validate_equipment_groups
from app.registry import PointRegistry
from app.transport import BacnetTransport

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
_next_port = [47850]  # mutable counter so each test gets its own server+client port pair, avoiding
                        # socket-release races between sequential tests that plagued a shared fixed port


def _allocate_ports() -> tuple[int, int]:
    server_port = _next_port[0]
    client_port = _next_port[0] + 1
    _next_port[0] += 2
    return server_port, client_port


pytestmark = pytest.mark.asyncio


def _load_group_config() -> EquipmentGroupConfig:
    with open(CONFIG_DIR / "devices" / "vav_1.json") as f:
        return EquipmentGroupConfig.model_validate(json.load(f))


def _supervisory_config() -> SupervisoryDeviceConfig:
    return SupervisoryDeviceConfig(device_instance=242000, device_name="ACI-SIM-SUPERVISOR-TEST")


async def _make_client(client_port: int) -> Application:
    dev = DeviceObject(objectIdentifier=("device", 599999), objectName="TestClient", vendorIdentifier=999)
    netport = NetworkPortObject(
        IPv4Address(f"127.0.0.1/24:{client_port}"),
        objectIdentifier=("network-port", 1),
        objectName="NetworkPort-1",
        networkNumber=0,
        networkNumberQuality="unknown",
    )
    return Application.from_object_list([dev, netport])


@pytest.fixture()
async def running_transport():
    server_port, _ = _allocate_ports()
    network_config = NetworkConfig(
        bind_address="127.0.0.1",
        subnet_bits=24,
        udp_port=server_port,
        respond_to_who_is=True,
        write_source_allowlist=[],
    )
    registry = PointRegistry([_load_group_config()])
    transport = BacnetTransport(network_config, _supervisory_config(), registry)
    transport.start()
    transport.test_port = server_port  # stashed for the test to read
    yield transport
    transport.stop()


async def test_read_discharge_temp_over_bacnet(running_transport):
    _, client_port = _allocate_ports()
    client = await _make_client(client_port)
    server_addr = f"127.0.0.1:{running_transport.test_port}"

    # VAV-1's offset is 11000; discharge_temp's local instance is 1 -> global 11001.
    value = await client.read_property(server_addr, "analog-input,11001", "presentValue")
    assert value == pytest.approx(55.0)


async def test_write_damper_position_and_read_back_with_priority_array(running_transport):
    _, client_port = _allocate_ports()
    client = await _make_client(client_port)
    server_addr = f"127.0.0.1:{running_transport.test_port}"

    # damper_position_command local instance 20 -> global 11020.
    await client.write_property(server_addr, "analog-output,11020", "presentValue", 65.0, priority=8)
    value = await client.read_property(server_addr, "analog-output,11020", "presentValue")
    assert value == pytest.approx(65.0)

    priority_array = await client.read_property(server_addr, "analog-output,11020", "priorityArray")
    assert priority_array is not None


async def test_write_source_allowlist_rejects_disallowed_source():
    """A write from a source IP not on the allowlist must be rejected, not applied."""
    server_port, client_port = _allocate_ports()
    network_config = NetworkConfig(
        bind_address="127.0.0.1",
        subnet_bits=24,
        udp_port=server_port,
        respond_to_who_is=True,
        write_source_allowlist=["10.255.255.255"],  # deliberately not our test client's address
    )
    registry = PointRegistry([_load_group_config()])
    transport = BacnetTransport(network_config, _supervisory_config(), registry)
    transport.start()
    try:
        client = await _make_client(client_port)
        server_addr = f"127.0.0.1:{server_port}"

        with pytest.raises(BaseException):
            # bacpypes3 raises its BACnet Error/Reject/Abort PDUs as
            # subclasses of BaseException rather than Exception, so the
            # rejection has to be caught at that level.
            await client.write_property(server_addr, "analog-output,11020", "presentValue", 99.0, priority=8)

        value = await client.read_property(server_addr, "analog-output,11020", "presentValue")
        assert value != pytest.approx(99.0), "rejected write must not have been applied to the object"
    finally:
        transport.stop()


async def test_object_instances_unique_within_group_enforced_at_load():
    """Config validation must reject a group with two points sharing one object instance."""
    from pydantic import ValidationError

    bad_config = {
        "group_id": "ACI-SIM-TEST-DUP",
        "instance_offset": 99000,
        "points": [
            {
                "alias": "point_a", "object_type": "analog-input", "object_instance": 1,
                "object_name": "Point A", "units": "no-units", "signal_direction": "sim_to_webctrl",
            },
            {
                "alias": "point_b", "object_type": "analog-input", "object_instance": 1,  # duplicate on purpose
                "object_name": "Point B", "units": "no-units", "signal_direction": "sim_to_webctrl",
            },
        ],
    }
    with pytest.raises(ValidationError):
        EquipmentGroupConfig.model_validate(bad_config)


async def test_global_object_instance_collision_across_groups_is_rejected():
    """
    Two groups whose instance_offset + local_instance overlap must be
    rejected at the fleet level, even though each group is individually
    valid on its own -- this is the exact bug class introduced by merging
    everything under one device, so it needs direct coverage.
    """
    group_a = EquipmentGroupConfig.model_validate({
        "group_id": "GROUP-A", "instance_offset": 5000,
        "points": [{"alias": "x", "object_type": "analog-input", "object_instance": 1,
                    "object_name": "X", "units": "no-units", "signal_direction": "sim_to_webctrl"}],
    })
    group_b = EquipmentGroupConfig.model_validate({
        "group_id": "GROUP-B", "instance_offset": 5000,  # same offset AND same local instance -> collides at 5001
        "points": [{"alias": "y", "object_type": "analog-input", "object_instance": 1,
                    "object_name": "Y", "units": "no-units", "signal_direction": "sim_to_webctrl"}],
    })
    with pytest.raises(ValueError):
        validate_equipment_groups([group_a, group_b])
