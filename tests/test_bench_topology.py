"""Regression coverage locking in the verified bench network topology.

Verified bench topology (do not regress):
  - Simulator: 192.168.168.201, UDP 47808, device instance 242000.
  - WebCTRL/controllers: 192.168.168.1-.7 and 192.168.168.200.

These tests guard both the deployed configuration files and the runtime
guard behavior against the prior incorrect values (simulator 192.168.168.100
and/or UDP 47809, empty write_source_allowlist)."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config_models import NetworkConfig, SupervisoryDeviceConfig
from app.transport import NetworkGuardedApplication

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

SIMULATOR_IP = "192.168.168.201"
WEBCTRL_IP = "192.168.168.200"
VERIFIED_PEERS = [f"192.168.168.{n}" for n in range(1, 8)] + [WEBCTRL_IP]
SIMULATOR_PORT = 47808
DEVICE_INSTANCE = 242000

# Values that were shipped incorrectly before the bench was verified.
PRIOR_BAD_IPS = ("192.168.168.100", "0.0.0.0")
PRIOR_BAD_PORT = 47809


def _load_network_config() -> NetworkConfig:
    with open(CONFIG_DIR / "network.json") as f:
        return NetworkConfig.model_validate(json.load(f))


def _load_supervisory_config() -> SupervisoryDeviceConfig:
    with open(CONFIG_DIR / "supervisory_device.json") as f:
        return SupervisoryDeviceConfig.model_validate(json.load(f))


# ---------------------------------------------------------------------------
# Deployed configuration files match the verified topology
# ---------------------------------------------------------------------------

def test_deployed_network_config_matches_verified_topology():
    cfg = _load_network_config()
    assert cfg.bind_address == SIMULATOR_IP
    assert cfg.subnet_bits == 24
    assert cfg.udp_port == SIMULATOR_PORT
    assert cfg.peer_allowlist == VERIFIED_PEERS
    assert cfg.write_source_allowlist == VERIFIED_PEERS


def test_deployed_supervisory_device_instance_is_verified_value():
    assert _load_supervisory_config().device_instance == DEVICE_INSTANCE


def test_deployed_config_does_not_regress_to_prior_incorrect_values():
    cfg = _load_network_config()
    assert cfg.bind_address not in PRIOR_BAD_IPS
    assert cfg.udp_port != PRIOR_BAD_PORT
    # The prior config left write_source_allowlist empty (accept-all writes)
    # and used the simulator's own host as the peer -- both are regressions.
    assert cfg.write_source_allowlist, "write_source_allowlist must not be empty"
    assert SIMULATOR_IP not in cfg.peer_allowlist, (
        "peer_allowlist must be the WebCTRL host, not the simulator's own IP"
    )
    for bad_ip in PRIOR_BAD_IPS:
        assert bad_ip not in cfg.peer_allowlist
        assert bad_ip not in cfg.write_source_allowlist


# ---------------------------------------------------------------------------
# NetworkConfig validation still rejects unsafe binds (PR #2 protection)
# ---------------------------------------------------------------------------

def test_network_config_still_rejects_wildcard_bind():
    with pytest.raises(ValidationError):
        NetworkConfig(bind_address="0.0.0.0")


def test_network_config_still_rejects_out_of_range_port():
    with pytest.raises(ValidationError):
        NetworkConfig(bind_address=SIMULATOR_IP, udp_port=70000)


# ---------------------------------------------------------------------------
# Runtime peer-guard behavior against the deployed allowlist
# ---------------------------------------------------------------------------

class _FakeApdu:
    def __init__(self, source_ip: str, port: int = SIMULATOR_PORT):
        self.pduSource = f"{source_ip}:{port}"


def _guard_with(config: NetworkConfig) -> NetworkGuardedApplication:
    """A NetworkGuardedApplication with just enough state to exercise
    _peer_blocked without opening a real BACnet socket."""
    guard = NetworkGuardedApplication.__new__(NetworkGuardedApplication)
    guard._network_config = config
    guard.messages_blocked = 0
    return guard


def test_peer_guard_admits_verified_controllers_and_blocks_everything_else():
    guard = _guard_with(_load_network_config())

    for allowed_ip in VERIFIED_PEERS:
        assert guard._peer_blocked(_FakeApdu(allowed_ip)) is False
    assert guard.messages_blocked == 0

    # The simulator's own host and the prior incorrect bench IP must be blocked.
    for blocked_ip in (SIMULATOR_IP, "192.168.168.100", "192.168.45.34"):
        assert guard._peer_blocked(_FakeApdu(blocked_ip)) is True

    assert guard.messages_blocked == 3
