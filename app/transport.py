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
from bacpypes3.apdu import AbortPDU
from bacpypes3.basetypes import ObjectTypesSupported
from bacpypes3.local.device import DeviceObject
from bacpypes3.local.networkport import NetworkPortObject
from bacpypes3.pdu import IPv4Address

from app.config_models import NetworkConfig, SupervisoryDeviceConfig
from app.registry import PointRegistry

logger = logging.getLogger("aci_sim.transport")
comm_logger = logging.getLogger("aci_sim.bacnet_traffic")


PR22_OBJECT_TYPE_BIT_COUNT = 63


class SimulatorDeviceObject(DeviceObject):
    """Device object with truthful protocol object-type reporting."""

    protocolRevision = 22

    @property
    def protocolObjectTypesSupported(self) -> ObjectTypesSupported:  # noqa: N802
        bits = ObjectTypesSupported([0] * PR22_OBJECT_TYPE_BIT_COUNT)
        objects = [self] if self._app is None else list(self._app.iter_objects())
        for obj in objects:
            object_type_number = int(obj.objectIdentifier[0])
            if 0 <= object_type_number < PR22_OBJECT_TYPE_BIT_COUNT:
                bits[object_type_number] = 1
        return bits

    @protocolObjectTypesSupported.setter
    def protocolObjectTypesSupported(self, value) -> None:  # noqa: N802, ARG002
        # Computed from the live object database; initialization writes are ignored.
        return


class NetworkGuardedApplication(Application):
    """BACpypes application with network safety and transport-fault guards.

    IMPORTANT: the peer allowlist guards READ/WRITE/COV data operations only.
    The Who-Is / I-Am discovery handshake is intentionally NOT peer-filtered,
    because that traffic is broadcast-based and the supervisor's point-binding
    Who-Is does not always arrive as a directed unicast from the supervisor's
    own IP. Blocking it makes WebCTRL hang on "Waiting for I-Am Device" even
    though reads succeed. Answering Who-Is is still gated by respond_to_who_is.
    """

    _network_config: NetworkConfig
    _fault_manager = None
    messages_in: int = 0
    messages_out: int = 0
    messages_blocked: int = 0
    cov_notification_failures: int = 0
    last_cov_notification_failure: Optional[dict] = None
    last_command_received: Optional[dict] = None
    _analog_limits: dict[tuple, tuple[Optional[float], Optional[float]]] = {}
    _configured_writable: dict[tuple, bool] = {}

    @classmethod
    def from_object_list_with_guard(
        cls, objects, network_config: NetworkConfig, fault_manager=None
    ) -> "NetworkGuardedApplication":
        app = cls.from_object_list(objects)
        app._network_config = network_config
        app._fault_manager = fault_manager
        app.messages_in = 0
        app.messages_out = 0
        app.messages_blocked = 0
        app.cov_notification_failures = 0
        app.last_cov_notification_failure = None
        app.last_command_received = None
        app._analog_limits = {}
        app._configured_writable = {}
        return app

    def _validate_configured_write_access(self, object_identifier) -> None:
        """Enforce the simulator point contract before BACpypes mutates it."""
        writable = self._configured_writable.get(tuple(object_identifier))
        if writable is not False:
            return

        from bacpypes3.errors import ExecutionError

        raise ExecutionError(
            errorClass="property",
            errorCode="write-access-denied",
        )

    def _validate_analog_write_bounds(
        self,
        object_identifier,
        property_identifier,
        encoded_value,
        priority,
    ) -> None:
        """Reject out-of-range analog present-value writes before commit."""
        property_name = str(property_identifier).replace("-", "").lower()
        limits = self._analog_limits.get(tuple(object_identifier))
        if property_name != "presentvalue" or limits is None:
            return

        obj = self.get_object_id(object_identifier)
        if obj is None:
            return
        property_type = obj.get_property_type(property_identifier)
        value = encoded_value.cast_out(
            property_type,
            null=(priority is not None),
        )
        from bacpypes3.primitivedata import Null

        if value is None or isinstance(value, Null):
            return
        numeric_value = float(value)
        minimum, maximum = limits
        if (
            (minimum is not None and numeric_value < minimum)
            or (maximum is not None and numeric_value > maximum)
        ):
            from bacpypes3.errors import ExecutionError

            raise ExecutionError(
                errorClass="property",
                errorCode="value-out-of-range",
            )

    def _peer_blocked(self, apdu) -> bool:
        allowlist = self._network_config.peer_allowlist
        if not allowlist:
            return False
        source_ip = self._normalize_source_ip(apdu.pduSource)
        if source_ip in allowlist:
            return False
        self.messages_blocked += 1
        comm_logger.warning(
            "BLOCKED request from %s (normalized %s not in peer_allowlist %s) -- dropped without reply",
            apdu.pduSource,
            source_ip,
            allowlist,
        )
        return True

    @staticmethod
    def _normalize_source_ip(pdu_source) -> str:
        """Extract a plain IPv4 string from a bacpypes3 address.

        Handles '192.168.168.200', '192.168.168.200:47809',
        and IPv6-mapped '::ffff:192.168.168.200'.
        """
        raw = str(pdu_source)
        if "::ffff:" in raw:
            raw = raw.split("::ffff:")[-1]
        if raw.count(":") == 1:
            raw = raw.split(":")[0]
        return raw

    async def _apply_transport_faults(self, apdu) -> bool:
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

    async def do_WhoIsRequest(self, apdu):  # noqa: N802
        # NOT peer-filtered: Who-Is/I-Am is the binding handshake and is
        # broadcast-based. Answering is still gated by respond_to_who_is.
        self.messages_in += 1
        if await self._apply_transport_faults(apdu):
            return
        if not self._network_config.respond_to_who_is:
            comm_logger.debug("Who-Is received from %s — not answering (respond_to_who_is=False)", apdu.pduSource)
            return
        comm_logger.debug("Who-Is from %s — answering I-Am", apdu.pduSource)
        await super().do_WhoIsRequest(apdu)

    async def do_WritePropertyRequest(self, apdu):  # noqa: N802
        if self._peer_blocked(apdu):
            return
        self.messages_in += 1
        if await self._apply_transport_faults(apdu):
            return

        from bacpypes3.errors import ExecutionError

        if self._fault_manager is not None:
            from app.faults import FaultType

            if self._fault_manager.is_transport_fault_active(FaultType.write_rejected):
                raise ExecutionError(errorClass="device", errorCode="write-access-denied")

        source_ip = self._normalize_source_ip(apdu.pduSource)
        allowlist = self._network_config.write_source_allowlist
        if allowlist and source_ip not in allowlist:
            raise ExecutionError(errorClass="device", errorCode="write-access-denied")

        self._validate_configured_write_access(apdu.objectIdentifier)
        self._validate_analog_write_bounds(
            apdu.objectIdentifier,
            apdu.propertyIdentifier,
            apdu.propertyValue,
            apdu.priority,
        )
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
        if self._peer_blocked(apdu):
            return
        self.messages_in += 1
        if await self._apply_transport_faults(apdu):
            return
        comm_logger.debug(
            "READ from %s: %s.%s", apdu.pduSource, apdu.objectIdentifier, apdu.propertyIdentifier
        )
        await super().do_ReadPropertyRequest(apdu)

    async def do_ReadPropertyMultipleRequest(self, apdu):  # noqa: N802
        if self._peer_blocked(apdu):
            return
        self.messages_in += 1
        if await self._apply_transport_faults(apdu):
            return
        comm_logger.debug("READ-MULTIPLE from %s", apdu.pduSource)
        await super().do_ReadPropertyMultipleRequest(apdu)

    async def do_WritePropertyMultipleRequest(self, apdu):  # noqa: N802
        if self._peer_blocked(apdu):
            return
        self.messages_in += 1
        if await self._apply_transport_faults(apdu):
            return

        from bacpypes3.errors import ExecutionError

        if self._fault_manager is not None:
            from app.faults import FaultType

            if self._fault_manager.is_transport_fault_active(FaultType.write_rejected):
                raise ExecutionError(errorClass="device", errorCode="write-access-denied")

        source_ip = self._normalize_source_ip(apdu.pduSource)
        allowlist = self._network_config.write_source_allowlist
        if allowlist and source_ip not in allowlist:
            raise ExecutionError(errorClass="device", errorCode="write-access-denied")

        for write_access_spec in apdu.listOfWriteAccessSpecs:
            self._validate_configured_write_access(
                write_access_spec.objectIdentifier,
            )
            for prop_value in write_access_spec.listOfProperties:
                self._validate_analog_write_bounds(
                    write_access_spec.objectIdentifier,
                    prop_value.propertyIdentifier,
                    prop_value.value,
                    prop_value.priority,
                )
        comm_logger.info("WRITE-MULTIPLE from %s", apdu.pduSource)
        await super().do_WritePropertyMultipleRequest(apdu)

    async def do_SubscribeCOVRequest(self, apdu):  # noqa: N802
        if self._peer_blocked(apdu):
            return
        self.messages_in += 1
        if await self._apply_transport_faults(apdu):
            return
        comm_logger.info(
            "COV SUBSCRIBE from %s: %s confirmed=%s lifetime=%s",
            apdu.pduSource,
            apdu.monitoredObjectIdentifier,
            apdu.issueConfirmedNotifications,
            apdu.lifetime,
        )
        await super().do_SubscribeCOVRequest(apdu)

    def cov_confirmation(self, cov, future) -> None:
        """Contain confirmed-COV timeouts instead of flooding the event loop.

        BACpypes3 calls ``future.result()`` from an asyncio callback.  A client
        that stops acknowledging confirmed notifications raises ``AbortPDU``
        there; the upstream callback does not catch it, so every timeout emits
        a full traceback.  Keep the subscription available for client
        recovery, count the failure for diagnostics, and rate-limit the log.
        """
        try:
            super().cov_confirmation(cov, future)
        except AbortPDU as abort:
            self.cov_notification_failures += 1
            self.last_cov_notification_failure = {
                "timestamp": time.time(),
                "reason": str(getattr(abort, "apduAbortRejectReason", abort)),
            }
            if (
                self.cov_notification_failures <= 3
                or self.cov_notification_failures % 100 == 0
            ):
                comm_logger.warning(
                    "Confirmed COV notification was not acknowledged "
                    "(failure %d): %s",
                    self.cov_notification_failures,
                    self.last_cov_notification_failure["reason"],
                )


class BacnetTransport:
    """Builds and owns the simulator's single BACpypes application."""

    def __init__(
        self,
        network_config: NetworkConfig,
        supervisory_config: SupervisoryDeviceConfig,
        registry: PointRegistry,
        fault_manager=None,
    ):
        self.network_config = network_config
        self.supervisory_config = supervisory_config
        self.registry = registry
        self.fault_manager = fault_manager
        self.app: Optional[NetworkGuardedApplication] = None

    def start(self) -> NetworkGuardedApplication:
        device_object = SimulatorDeviceObject(
            objectIdentifier=("device", self.supervisory_config.device_instance),
            objectName=self.supervisory_config.device_name,
            description=self.supervisory_config.description,
            vendorIdentifier=self.network_config.vendor_identifier,
        )

        addr_string = (
            f"{self.network_config.bind_address}/"
            f"{self.network_config.subnet_bits}:"
            f"{self.network_config.udp_port}"
        )
        bind_addr = IPv4Address(addr_string)
        logger.info(
            "DIAG addr_string=%s -> IPv4Address str=%s tuple=%s",
            addr_string,
            str(bind_addr),
            getattr(bind_addr, "addrTuple", None),
        )

        network_port_object = NetworkPortObject(
            bind_addr,
            objectIdentifier=("network-port", 1),
            objectName="NetworkPort-1",
            networkNumber=self.network_config.network_number,
            networkNumberQuality="configured" if self.network_config.network_number else "unknown",
            apduLength=device_object.maxApduLengthAccepted,
            ipDefaultGateway=bytes(
                int(part) for part in self.network_config.ip_default_gateway.split(".")
            ),
            ipDNSServer=[
                bytes(int(part) for part in address.split("."))
                for address in self.network_config.ip_dns_servers
            ],
        )

        point_objects = self.registry.build_objects()

        self.app = NetworkGuardedApplication.from_object_list_with_guard(
            [device_object, network_port_object, *point_objects],
            network_config=self.network_config,
            fault_manager=self.fault_manager,
        )
        self.app._analog_limits = {
            tuple(registered.bacnet_object.objectIdentifier): (
                registered.config.minimum,
                registered.config.maximum,
            )
            for registered in self.registry.all_points().values()
            if (
                registered.config.minimum is not None
                or registered.config.maximum is not None
            )
        }
        self.app._configured_writable = {
            tuple(registered.bacnet_object.objectIdentifier): bool(
                registered.config.writable
            )
            for registered in self.registry.all_points().values()
        }

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
        # Server-side COV subscriptions own lifetime TimerHandles that retain
        # the old application and object graph. Cancel them explicitly before
        # closing the link layers so repeated dashboard restarts do not keep
        # stale BACnet applications alive until every client lifetime expires.
        for detection in list(self.app._cov_detections.values()):
            for subscription in list(detection.cov_subscriptions):
                try:
                    self.app.cancel_subscription(subscription)
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Error cancelling COV subscription during shutdown"
                    )
        for link_layer in list(self.app.link_layers.values()):
            try:
                link_layer.close()
            except Exception:  # noqa: BLE001
                logger.exception("Error closing BACnet link layer during shutdown")
        logger.info("Supervisory BACnet device '%s' offline", self.supervisory_config.device_name)
        self.app = None
