"""
Action Validator (Phase 6a).

This is the real safety boundary, not `action_schema.py` -- the schema only
guarantees well-formed JSON. This module checks that a bundle is *safe and
real*: every fault type actually exists, every scenario is a valid
`Scenario`, every targeted point actually exists in the running registry,
and -- critically -- that every intent/action type is actually enabled in
this phase.

`PHASE_6A_ALLOWED_*` is the allowlist. Widening it (for Phase 6d, dynamic
equipment management) is meant to be a deliberate, reviewed change to this
one list, not something that happens by an LLM being persuasive in a
prompt.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import ValidationError

from app.faults import FaultType
from app.llm.action_schema import AllowedActionType, AllowedIntent, LlmAction, LlmActionBundle
from app.registry import PointRegistry
from app.scenario import Scenario

# Phase 6a scope: scenario/fault generation and read-only explanation only.
# Nothing here touches the BACnet object model. Widening this is a Phase 6d
# decision, made deliberately -- see HANDOFF.md / PHASE6_REVIEW.md.
PHASE_6A_ALLOWED_INTENTS = {
    AllowedIntent.create_scenario,
    AllowedIntent.inject_fault,
    AllowedIntent.clear_fault,
    AllowedIntent.adjust_simulation_parameter,
    AllowedIntent.explain_behavior,
    AllowedIntent.summarize_events,
}

PHASE_6A_ALLOWED_ACTION_TYPES = {
    AllowedActionType.create_scenario,
    AllowedActionType.set_initial_condition,
    AllowedActionType.inject_fault,
    AllowedActionType.clear_fault,
    AllowedActionType.adjust_parameter,
    AllowedActionType.annotate_logs,
}

# Read-only intents that legitimately produce no actions -- they answer or
# summarize, they never change simulator state. The system prompt tells the
# model to return an empty actions list for these, so requiring nonempty
# actions here would reject every valid explanation (found live with
# hermes3:3b: a correct explain_behavior bundle was marked "bundle has no
# actions"). Every other intent must still carry at least one action.
READ_ONLY_INTENTS = {
    AllowedIntent.explain_behavior,
    AllowedIntent.summarize_events,
}


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)


class ActionValidator:
    def __init__(self, registry: PointRegistry):
        self.registry = registry

    def validate_bundle(self, bundle: LlmActionBundle) -> ValidationResult:
        errors: list[str] = []

        if bundle.intent not in PHASE_6A_ALLOWED_INTENTS:
            errors.append(
                f"intent '{bundle.intent.value}' is not enabled in this phase (Phase 6a is "
                f"scenario/fault generation and explanation only -- see action_validator.py's "
                f"PHASE_6A_ALLOWED_INTENTS)"
            )

        if not bundle.actions and bundle.intent not in READ_ONLY_INTENTS:
            errors.append("bundle has no actions")

        for i, action in enumerate(bundle.actions):
            errors.extend(f"action[{i}]: {e}" for e in self._validate_action(action))

        return ValidationResult(valid=len(errors) == 0, errors=errors)

    def _validate_action(self, action: LlmAction) -> list[str]:
        errors: list[str] = []

        if action.type not in PHASE_6A_ALLOWED_ACTION_TYPES:
            errors.append(
                f"action type '{action.type.value}' is not enabled in this phase"
            )
            return errors  # no point checking fields for a disallowed action type

        if action.type == AllowedActionType.inject_fault:
            errors.extend(self._validate_fault_action(action, require_type=True))
        elif action.type == AllowedActionType.clear_fault:
            errors.extend(self._validate_fault_action(action, require_type=False))
        elif action.type == AllowedActionType.create_scenario:
            errors.extend(self._validate_scenario_action(action))
        elif action.type == AllowedActionType.set_initial_condition:
            if not action.parameters:
                errors.append("set_initial_condition needs at least one key in 'parameters'")
        elif action.type == AllowedActionType.adjust_parameter:
            if not action.parameter_name:
                errors.append("adjust_parameter needs 'parameter_name'")
        elif action.type == AllowedActionType.annotate_logs:
            if not action.note:
                errors.append("annotate_logs needs 'note'")

        return errors

    def _validate_fault_action(self, action: LlmAction, require_type: bool) -> list[str]:
        errors = []
        if require_type:
            if not action.fault_type:
                errors.append("inject_fault needs 'fault_type'")
            else:
                try:
                    FaultType(action.fault_type)
                except ValueError:
                    errors.append(
                        f"unknown fault_type '{action.fault_type}' -- must be one of "
                        f"{[t.value for t in FaultType]}"
                    )
        # Transport-level faults (device_offline, slow_response, write_rejected,
        # intermittent_comm) legitimately have no group_id/alias -- only check
        # existence when a target was actually given.
        if action.group_id and action.alias:
            key = f"{action.group_id}.{action.alias}"
            if key not in self.registry.all_points():
                errors.append(f"point '{key}' does not exist in the running registry")
        elif action.group_id and not action.alias:
            errors.append("group_id given without alias -- fault target must be a specific point")
        return errors

    def _validate_scenario_action(self, action: LlmAction) -> list[str]:
        if not action.scenario:
            return ["create_scenario needs a full 'scenario' object"]
        try:
            Scenario.model_validate(action.scenario)
        except ValidationError as e:
            return [f"scenario definition is invalid: {e}"]
        return []
