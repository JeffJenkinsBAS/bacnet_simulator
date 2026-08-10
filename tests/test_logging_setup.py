import logging
from pathlib import Path

from app.logging_setup import configure_logging


def test_logging_configuration_is_idempotent(tmp_path: Path) -> None:
    configure_logging(tmp_path)
    configure_logging(tmp_path)

    root = logging.getLogger("aci_sim")
    traffic = logging.getLogger("aci_sim.bacnet_traffic")
    assert len(root.handlers) == 3
    assert len(traffic.handlers) == 2
