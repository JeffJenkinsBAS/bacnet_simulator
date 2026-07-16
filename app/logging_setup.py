"""
Logging setup. Two log files plus console output:

  logs/aci_sim.log        general application log — state changes, faults,
                           device online/offline, startup/shutdown
  logs/bacnet_traffic.log reads, writes, COV subscriptions (the
                           aci_sim.bacnet_traffic logger used by transport.py)

Both are also mirrored to a small in-memory ring buffer so the UI's Logs
view can show recent activity without re-reading files from disk on every
poll.
"""
from __future__ import annotations

import collections
import logging
import logging.handlers
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"

# Shared ring buffers the UI reads from. Deliberately simple (a deque, not a
# database) — this is a training-bench tool, not a production log pipeline.
recent_app_events: collections.deque = collections.deque(maxlen=500)
recent_bacnet_traffic: collections.deque = collections.deque(maxlen=500)


class _RingBufferHandler(logging.Handler):
    def __init__(self, buffer: collections.deque):
        super().__init__()
        self._buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        self._buffer.append(self.format(record))


def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    root = logging.getLogger("aci_sim")
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    root.addHandler(console)

    app_file = logging.handlers.RotatingFileHandler(
        LOG_DIR / "aci_sim.log", maxBytes=5_000_000, backupCount=3
    )
    app_file.setLevel(logging.DEBUG)
    app_file.setFormatter(fmt)
    root.addHandler(app_file)

    app_ring = _RingBufferHandler(recent_app_events)
    app_ring.setLevel(logging.INFO)
    app_ring.setFormatter(fmt)
    root.addHandler(app_ring)

    # Separate file (and separate ring buffer) specifically for BACnet
    # traffic, which is much higher-volume and belongs in its own view.
    traffic_logger = logging.getLogger("aci_sim.bacnet_traffic")
    traffic_logger.setLevel(logging.DEBUG)
    traffic_logger.propagate = False

    traffic_file = logging.handlers.RotatingFileHandler(
        LOG_DIR / "bacnet_traffic.log", maxBytes=5_000_000, backupCount=3
    )
    traffic_file.setFormatter(fmt)
    traffic_logger.addHandler(traffic_file)

    traffic_ring = _RingBufferHandler(recent_bacnet_traffic)
    traffic_ring.setFormatter(fmt)
    traffic_logger.addHandler(traffic_ring)
