"""
LLM Action Schema (Phase 6a).

Every LLM output that can affect simulator state must take this shape --
never freeform text execution. This mirrors the `llm_action_bundle`
contract from the Phase 6 spec exactly, so a future phase (6d, equipment
topology changes) can widen what's *allowed* without changing the schema
itself.

Phase 6a intentionally only wires a subset of `AllowedIntent` /
`AllowedActionType` through to real effects -- see
`action_validator.py`'s `PHASE_6A_ALLOWED_INTENTS`. The full enums are
defined here (matching the original spec) so the schema doesn't need to
change shape when 6d is unlocked later; only the validator's allowlist
does.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class AllowedIntent(str, Enum):
    generate_training_lab = "generate_training_lab"
    create_scenario = "create_scenario"
    inject_fault = "inject_fault"
    clear_fault = "clear_fault"
    add_equipment = "add_equipment"
    modify_equipment = "modify_equipment"
    remove_equipment = "remove_equipment"
    adjust_simulation_parameter = "adjust_simulation_parameter"
    explain_behavior = "explain_behavior"
    summarize_events = "summarize_events"
    propose_dashboard_layout = "propose_dashboard_layout"


class AllowedActionType(str, Enum):
    add_equipment = "add_equipment"
    modify_equipment = "modify_equipment"
    remove_equipment = "remove_equipment"
    create_scenario = "create_scenario"
    set_initial_condition = "set_initial_condition"
    inject_fault = "inject_fault"
    clear_fault = "clear_fault"
    set_network_condition = "set_network_condition"
    adjust_parameter = "adjust_parameter"
    annotate_logs = "annotate_logs"


class LlmAction(BaseModel):
    """
    One action within a bundle. Deliberately loose on the specific fields
    beyond `type` -- different action types need different data (a
    create_scenario action carries a full scenario definition; an
    inject_fault action carries group_id/alias/fault_type/parameters).
    `action_validator.py` is what actually checks the right fields are
    present and valid for a given `type`, not this schema -- Pydantic only
    guarantees "this is well-formed JSON shaped roughly like an action,"
    not "this is a valid, safe action."
    """

    type: AllowedActionType
    equipment_type: Optional[str] = None
    template: Optional[str] = None
    count: Optional[int] = None
    group_id: Optional[str] = None
    alias: Optional[str] = None
    fault_type: Optional[str] = None
    scenario: Optional[dict[str, Any]] = None  # full Scenario-shaped dict, for create_scenario
    parameter_name: Optional[str] = None
    value: Optional[Any] = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    note: Optional[str] = None  # for annotate_logs


class LlmActionBundle(BaseModel):
    request_id: str
    intent: AllowedIntent
    summary: str
    actions: list[LlmAction] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    requires_approval: bool = True
    confidence: float = 0.0
