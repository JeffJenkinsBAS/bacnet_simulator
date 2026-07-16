"""
Audit Service (Phase 6a).

Append-only local log of every LLM interaction that could affect simulator
state: what was proposed, what validation said, what was actually applied,
and what was rejected. Deliberately simple -- one JSON-lines file, no
database -- matching the project's existing logging approach
(logging_setup.py's rotating file handlers) rather than introducing a new
storage technology for what's fundamentally the same kind of need.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("aci_sim.llm.audit")

DEFAULT_AUDIT_LOG = Path(__file__).resolve().parent.parent.parent / "logs" / "llm_audit.log"


class AuditService:
    def __init__(self, log_path: Path = DEFAULT_AUDIT_LOG):
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event_type: str, **fields: Any) -> None:
        """
        event_type: "proposed" | "validation_failed" | "applied" | "apply_failed"
        """
        entry = {"timestamp": time.time(), "event_type": event_type, **fields}
        with open(self.log_path, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
        logger.info("LLM audit: %s", event_type)

    def recent(self, limit: int = 50) -> list[dict]:
        if not self.log_path.exists():
            return []
        with open(self.log_path) as f:
            lines = f.readlines()
        entries = []
        for line in lines[-limit:]:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries
