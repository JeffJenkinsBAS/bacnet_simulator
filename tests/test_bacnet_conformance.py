"""Regression coverage for BACnet self-description and configuration integrity."""

import pytest
from pydantic import ValidationError

from app.config_models import NetworkConfig, SupervisoryDeviceConfig
from app.registry import PointRegistry
from tests.test_bacnet_integration import _load_group_config, running_transport

pytestmark = pytest.mark.asyncio


async def test_device_reports_live_protocol_object_types(running_transport):
    device = running_transport.app.device_object
    supported = device.protocolObjectTypesSupported
    live_type_numbers = {
        int(obj.objectIdentifier[0]) for obj in running_transport.app.iter_objects()
    }

    for object_type_number in live_type_numbers:
        assert supported[object_type_number] == 1

    assert supported[3] == 0  # binary-input is absent from the reduced VAV fixture


async def test_network_port_exposes_required_ipv4_properties(running_transport):
    network_ports = [
        obj
        for obj in running_transport.app.iter_objects()
        if str(obj.objectIdentifier[0]) == "network-port"
    ]
    assert len(network_ports) == 1
    network_port = network_ports[0]

    assert int(network_port.apduLength) == int(
        running_transport.app.device_object.maxApduLengthAccepted
    )
    assert bytes(network_port.ipDefaultGateway) == b"\x00\x00\x00\x00"
    assert list(network_port.ipDNSServer) == []


async def test_registry_preserves_explicit_zero_cov_increment():
    group = _load_group_config()
    group.points[0].cov_increment = 0.0
    registry = PointRegistry([group])
    first_object = registry.build_objects()[0]
    assert float(first_object.covIncrement) == 0.0


async def test_invalid_bacnet_configuration_is_rejected():
    with pytest.raises(ValidationError):
        SupervisoryDeviceConfig(device_instance=4194303, device_name="Wildcard")
    with pytest.raises(ValidationError):
        NetworkConfig(bind_address="0.0.0.0")
    with pytest.raises(ValidationError):
        NetworkConfig(bind_address="127.0.0.1", udp_port=70000)
