"""
Typed configuration models for the ACI BACnet Building Simulation Platform.

Architecture note (this replaces the earlier one-BACnet-device-per-equipment
design): per Jeff's correction, everything runs as ONE BACnet device on the
standard port 47808, hosting every AI/AO/AV/BI/BO/BV/MSx point from every
piece of simulated equipment. Equipment is still organized into logical
"groups" (AHU-1, Chiller-1, VAV-3, etc.) for config-file readability, the UI,
and the in-process equipment models -- but a group is no longer a separate
BACnet device or a separate UDP port. Every group gets an `instance_offset`;
a point's real, on-the-wire object instance is `instance_offset +
object_instance` (the small per-group number, e.g. AI:1, that was previously
enough on its own back when each group was its own device). This is what
keeps 143 objects collision-free under one device without hand-numbering
each one globally.

These models are the single source of truth for what a config file is
allowed to contain. Nothing in the equipment/transport layers should read a
raw dict -- everything goes through these types first, so a bad config file
fails loudly at startup instead of causing a confusing runtime error later.
"""
from __future__ import annotations

from enum import Enum
from ipaddress import IPv4Address
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class ObjectType(str, Enum):
    analog_input = "analog-input"
    analog_output = "analog-output"
    analog_value = "analog-value"
    binary_input = "binary-input"
    binary_output = "binary-output"
    binary_value = "binary-value"
    multi_state_input = "multi-state-input"
    multi_state_output = "multi-state-output"
    multi_state_value = "multi-state-value"


class SignalDirection(str, Enum):
    """
    Every point must declare exactly one of these. See Phase 1 architecture
    §9 (Signal-Direction Discipline). No point ships without this tag.
    """

    sim_to_webctrl = "sim_to_webctrl"
    webctrl_to_sim = "webctrl_to_sim"
    bidirectional = "bidirectional"
    instructor_only = "instructor_only"
    calculated = "calculated"
    alarm_fault = "alarm_fault"


class NormalRange(BaseModel):
    low: float
    high: float


class PointConfig(BaseModel):
    """
    One BACnet object definition, as it appears in an equipment group's
    points config file. `object_instance` here is the small, per-group
    local number (e.g. AI:1) -- the group's `instance_offset` gets added to
    it at load time to produce the real global object instance published on
    the wire. `alias` is the internal name the equipment model uses to look
    this point up in the Point Registry -- equipment code never references a
    raw BACnet object type/instance directly, and never needs to know about
    the offset either (see registry.GroupView).
    """

    alias: str = Field(..., description="Internal name used by equipment models and the point registry")
    object_type: ObjectType
    object_instance: int = Field(..., description="Local instance within the group, before instance_offset is applied")
    object_name: str
    description: str = ""
    units: str = "no-units"
    signal_direction: SignalDirection
    writable: bool = False
    commandable: bool = False
    initial_value: float = 0.0
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    normal_range: Optional[NormalRange] = None
    update_interval_seconds: float = 1.0
    cov_increment: Optional[float] = None
    relinquish_default: Optional[float] = None
    interlock: bool = Field(
        default=False,
        description=(
            "If true, this point is a hard interlock (see Phase 1 Addendum 2/3): "
            "checked first every simulation tick, ahead of normal command "
            "processing, and forces the equipment model into a fixed safe "
            "state while true. Only meaningful for writable/commandable points."
        ),
    )

    @model_validator(mode="after")
    def _writable_points_must_be_commandable_or_intentional(self) -> "PointConfig":
        if self.writable and not self.commandable and self.object_type not in (
            ObjectType.analog_output,
            ObjectType.binary_output,
            ObjectType.multi_state_output,
        ):
            raise ValueError(
                f"point '{self.alias}': writable AV/BV/MSV points must set "
                f"commandable=true explicitly"
            )
        return self


class EquipmentGroupConfig(BaseModel):
    """One logical piece of equipment's full point list."""

    group_id: str
    description: str = ""
    instance_offset: int = Field(
        ...,
        description=(
            "Added to every point's object_instance in this group to produce the real, "
            "global object instance on the single supervisory device."
        ),
    )
    points: list[PointConfig]

    @model_validator(mode="after")
    def _object_instances_unique_within_group(self) -> "EquipmentGroupConfig":
        seen: dict[tuple[str, int], str] = {}
        aliases: set[str] = set()
        for p in self.points:
            key = (p.object_type.value, p.object_instance)
            if key in seen:
                raise ValueError(
                    f"group '{self.group_id}': duplicate object "
                    f"{p.object_type.value}:{p.object_instance} used by both "
                    f"'{seen[key]}' and '{p.alias}'"
                )
            seen[key] = p.alias
            if p.alias in aliases:
                raise ValueError(f"group '{self.group_id}': duplicate point alias '{p.alias}'")
            aliases.add(p.alias)
        return self


def validate_equipment_groups(groups: list["EquipmentGroupConfig"]) -> None:
    """Validate global object identifiers and object names."""
    seen_global: dict[tuple[str, int], str] = {}
    seen_names: dict[str, str] = {}
    seen_group_ids: set[str] = set()
    for g in groups:
        if g.group_id in seen_group_ids:
            raise ValueError(f"duplicate equipment group_id '{g.group_id}'")
        seen_group_ids.add(g.group_id)
        for p in g.points:
            global_instance = g.instance_offset + p.object_instance
            key = (p.object_type.value, global_instance)
            if key in seen_global:
                raise ValueError(
                    f"duplicate global object {p.object_type.value}:{global_instance} "
                    f"(group '{g.group_id}' point '{p.alias}' collides with "
                    f"'{seen_global[key]}') -- check instance_offset values"
                )
            seen_global[key] = f"{g.group_id}.{p.alias}"

            if p.object_name in seen_names:
                raise ValueError(
                    f"duplicate object name '{p.object_name}' (group '{g.group_id}' point "
                    f"'{p.alias}' collides with '{seen_names[p.object_name]}') -- BACnet "
                    f"requires unique object names within a device"
                )
            seen_names[p.object_name] = f"{g.group_id}.{p.alias}"


class SupervisoryDeviceConfig(BaseModel):
    """The single BACnet device every equipment group's objects live under."""

    device_instance: int = Field(..., ge=0, le=4194302)
    device_name: str
    description: str = ""


class NetworkConfig(BaseModel):
    """Validated BACnet/IP network and safety settings."""

    bind_address: str = Field(default="127.0.0.1")
    subnet_bits: int = Field(default=24)
    udp_port: int = Field(default=47809)
    vendor_identifier: int = 999
    network_number: int = Field(default=0, ge=0, le=65534)
    ip_default_gateway: str = Field(default="0.0.0.0")
    ip_dns_servers: list[str] = Field(default_factory=list)
    private_lab_mode: bool = True
    respond_to_who_is: bool = True
    write_source_allowlist: list[str] = Field(default_factory=list)
    peer_allowlist: list[str] = Field(default_factory=list)
    startup_duplicate_instance_check: bool = True

    @model_validator(mode="after")
    def _validate_network_values(self) -> "NetworkConfig":
        if self.bind_address == "0.0.0.0":
            raise ValueError("bind_address must identify a specific interface, not 0.0.0.0")
        try:
            IPv4Address(self.bind_address)
            IPv4Address(self.ip_default_gateway)
            for address in self.ip_dns_servers:
                IPv4Address(address)
        except ValueError as exc:
            raise ValueError(f"invalid IPv4 network configuration: {exc}") from exc
        if not 0 <= self.subnet_bits <= 32:
            raise ValueError("subnet_bits must be between 0 and 32")
        if not 1 <= self.udp_port <= 65535:
            raise ValueError("udp_port must be between 1 and 65535")
        if not 0 <= self.vendor_identifier <= 65535:
            raise ValueError("vendor_identifier must be between 0 and 65535")
        return self
