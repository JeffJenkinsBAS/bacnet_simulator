"""
BACnet Transport Layer.

This module owns every direct interaction with bacpypes3. Equipment models
and the simulation engine never import bacpypes3 -- they only ever go
through the PointRegistry. This file is the only place that knows what a
WhoIsRequest, a WritePropertyRequest, or a priority array actually is.

Architecture note: ONE Application, ONE DeviceObject, ONE NetworkPortObject,
bound to the standard BACnet port (47808 by default) -- see
config_models.py's module docstring for why. Every equipment group's
objects (built by PointRegistry from all groups) get folded into this same
Application.

Phase 4: also where the four transport-level faults are applied
(device_offline, slow_response, write_rejected, intermittent_comm) -- these
affect the whole device rather than one point, so they belong here rather
than in faults.py's per-point mechanics.
"""
from __future__ import annotations

import logging
import random
import time
from typing import Optional

from bacpypes3.app import Application
from bacpypes3.local.device import DeviceObject
from bacpypes3.local.networkport import NetworkPortObject
from bacpypes3.pdu import IPv4Address

from app.config_models import NetworkConfig, SupervisoryDeviceConfig
from app.registry import PointRegistry

logger = logging.getLogger("aci_sim.transport")
comm_logger = logging.getLogger("aci_sim.bacnet_traffic")


class NetworkGuardedApplication(Application):
    """
    Application subclass that enforces the Network Safety requirements from
    the Phase 1 architecture: an option to disable Who-Is responses, and an
    option to restrict accepted writes by source IP. Everything else behaves
    like a normal bacpypes3 Application.

    `_network_config` and `_fault_manager` are attached after construction
    (see `from_object_list_with_guard` below) rather than through `__init__`,
    since `Application.from_object_list()` controls how the class gets
    instantiated and doesn't forward extra keyword arguments to it.
    """

    _network_config: NetworkConfig
    _fault_manager = None  # app.faults.FaultManager, set post-construction; Optional to avoid an import cycle
    messages_in: int = 0
    messages_out: int = 0
    last_command_received: Optional[dict] = None

    @classmethod
    def from_object_list_with_guard(
        cls, objects, network_config: NetworkConfig, fault_manager=None
    ) -> "NetworkGuardedApplication":
        app = cls.from_object_list(objects)
        app._network_config = network_config
        app._fault_manager = fault_manager
        app.messages_in = 0
        app.messages_out = 0
        app.last_command_received = None
        return app

    async def _apply_transport_faults(self, apdu) -> bool:
        """
        Returns True if the request should be dropped (device_offline or an
        intermittent_comm coin-flip), applies slow_response's delay if
        active, and returns False otherwise. Called first in every do_*
        handler below, before any normal processing.
        """
        if self._fault_manager is None:
            return False
        from app.faults import FaultType

        if self._fault_manager.is_transport_fault_active(FaultType.device_offline):
            comm_logger.debug("DROPPED (device_offline fault active): request from %s", apdu.pduSource)
            return True

        intermittent = self._fault_manager.is_transport_fault_active(FaultType.intermittent_comm)
        if intermittent and random.random() < intermittent.parameters.get("drop_probability", 0.3):
            comm_logger.debug("DROPPED (intermittent_comm fault, random): request from %s", apdu.pduSource)
            return True

        slow = self._fault_manager.is_transport_fault_active(FaultType.slow_response)
        if slow:
            import asyncio

            await asyncio.sleep(slow.parameters.get("delay_seconds", 3.0))

        return False

    async def do_WhoIsRequest(self, apdu):  # noqa: N802 - bacpypes3 naming convention
        self.messages_in += 1
        if await self._apply_transport_faults(apdu):
            return
        if not self._network_config.respond_to_who_is:
            comm_logger.debug("Who-Is received from %s — not answering (respond_to_who_is=False)", apdu.pduSource)
            return
        comm_logger.debug("Who-Is received from %s — answering", apdu.pduSource)
        await super().do_WhoIsRequest(apdu)

    async def do_WritePropertyRequest(self, apdu):  # noqa: N802
        self.messages_in += 1
        if await self._apply_transport_faults(apdu):
            return

        from bacpypes3.errors import ExecutionError

        if self._fault_manager is not None:
            from app.faults import FaultType

            if self._fault_manager.is_transport_fault_active(FaultType.write_rejected):
                logger.warning("REJECTED write from %s (write_rejected fault active)", apdu.pduSource)
                raise ExecutionError(errorClass="device", errorCode="write-access-denied")

        source_ip = str(apdu.pduSource).split(":")[0]
        allowlist = self._network_config.write_source_allowlist
        if allowlist and source_ip not in allowlist:
            logger.warning(
                "REJECTED write from %s (not in write_source_allowlist %s) to object %s property %s",
                apdu.pduSource,
                allowlist,
                apdu.objectIdentifier,
                apdu.propertyIdentifier,
            )
            raise ExecutionError(errorClass="device", errorCode="write-access-denied")

        self.last_command_received = {
            "source": str(apdu.pduSource),
            "object_identifier": str(apdu.objectIdentifier),
            "property": str(apdu.propertyIdentifier),
            "priority": apdu.priority,
            "timestamp": time.time(),
        }
        comm_logger.info(
            "WRITE from %s: %s.%s = (priority %s)",
            apdu.pduSource,
            apdu.objectIdentifier,
            apdu.propertyIdentifier,
            apdu.priority,
        )
        await super().do_WritePropertyRequest(apdu)

    async def do_ReadPropertyRequest(self, apdu):  # noqa: N802
        self.messages_in += 1
        if await self._apply_transport_faults(apdu):
            return
        comm_logger.debug(
            "READ from %s: %s.%s", apdu.pduSource, apdu.objectIdentifier, apdu.propertyIdentifier
        )
        await super().do_ReadPropertyRequest(apdu)

    async def do_ReadPropertyMultipleRequest(self, apdu):  # noqa: N802
        # WebCTRL's poll engine primarily uses ReadPropertyMultiple, not
        # single ReadProperty -- without this override, the device_offline /
        # slow_response / intermittent_comm faults were invisible to real
        # WebCTRL polling and messages_in undercounted actual bench traffic.
        self.messages_in += 1
        if await self._apply_transport_faults(apdu):
            return
        comm_logger.debug("READ-MULTIPLE from %s", apdu.pduSource)
        await super().do_ReadPropertyMultipleRequest(apdu)

    async def do_WritePropertyMultipleRequest(self, apdu):  # noqa: N802
        self.messages_in += 1
        if await self._apply_transport_faults(apdu):
            return

        from bacpypes3.errors import ExecutionError

        if self._fault_manager is not None:
            from app.faults import FaultType

            if self._fault_manager.is_transport_fault_active(FaultType.write_rejected):
                logger.warning("REJECTED write-multiple from %s (write_rejected fault active)", apdu.pduSource)
                raise ExecutionError(errorClass="device", errorCode="write-access-denied")

        source_ip = str(apdu.pduSource).split(":")[0]
        allowlist = self._network_config.write_source_allowlist
        if allowlist and source_ip not in allowlist:
            logger.warning("REJECTED write-multiple from %s (not in write_source_allowlist)", apdu.pduSource)
            raise ExecutionError(errorClass="device", errorCode="write-access-denied")

        comm_logger.info("WRITE-MULTIPLE from %s", apdu.pduSource)
        await super().do_WritePropertyMultipleRequest(apdu)

    async def do_SubscribeCOVRequest(self, apdu):  # noqa: N802
        self.messages_in += 1
        if await self._apply_transport_faults(apdu):
            return
        comm_logger.info(
            "COV SUBSCRIBE from %s: %s confirmed=%s lifetime=%s",
            apdu.pduSource, apdu.monitoredObjectIdentifier,
            apdu.issueConfirmedNotifications, apdu.lifetime,
        )
        await super().do_SubscribeCOVRequest(apdu)


class BacnetTransport:
    """
    Builds and owns the single bacpypes3 Application that every equipment
    group's objects live under.
    """

    def __init__(
        self, network_config: NetworkConfig, supervisory_config: SupervisoryDeviceConfig,
        registry: PointRegistry, fault_manager=None,
    ):
        self.network_config = network_config
        self.supervisory_config = supervisory_config
        self.registry = registry
        self.fault_manager = fault_manager
        self.app: Optional[NetworkGuardedApplication] = None

    def start(self) -> NetworkGuardedApplication:
        device_object = DeviceObject(
            objectIdentifier=("device", self.supervisory_config.device_instance),
            objectName=self.supervisory_config.device_name,
            description=self.supervisory_config.description,
            vendorIdentifier=self.network_config.vendor_identifier,
        )

        bind_addr = IPv4Address(
            f"{self.network_config.bind_address}/{self.network_config.subnet_bits}:"
            f"{self.network_config.udp_port}"
        )
        network_port_object = NetworkPortObject(
            bind_addr,
            objectIdentifier=("network-port", 1),
            objectName="NetworkPort-1",
            networkNumber=self.network_config.network_number,
            networkNumberQuality="configured" if self.network_config.network_number else "unknown",
        )

        point_objects = self.registry.build_objects()

        self.app = NetworkGuardedApplication.from_object_list_with_guard(
            [device_object, network_port_object, *point_objects],
            network_config=self.network_config,
            fault_manager=self.fault_manager,
        )

        logger.info(
            "Supervisory BACnet device '%s' (instance %d) online, bound to %s:%d, hosting %d objects%s",
            self.supervisory_config.device_name,
            self.supervisory_config.device_instance,
            self.network_config.bind_address,
            self.network_config.udp_port,
            len(point_objects),
            " [PRIVATE LAB MODE]" if self.network_config.private_lab_mode else "",
        )
        return self.app

    def stop(self) -> None:
        if self.app is None:
            return
        for link_layer in list(self.app.link_layers.values()):
            try:
                link_layer.close()
            except Exception:  # noqa: BLE001 - best-effort shutdown
                logger.exception("Error closing BACnet link layer during shutdown")
        logger.info("Supervisory BACnet device '%s' offline", self.supervisory_config.device_name)
        self.app = None
