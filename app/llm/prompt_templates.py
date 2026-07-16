"""
Prompt Templates (Phase 6a).

The system prompt is what actually constrains the model to producing valid
`LlmActionBundle` JSON -- the schema and validator catch it if the model
doesn't comply, but a good prompt means fewer rejected bundles in
practice.

Loaded from config/llm/system_prompts.json so editing that file genuinely
changes behavior today, even without a Settings GUI to edit it from yet
(the original spec's "edit or choose system prompt profile" requirement --
this is the config-file half of that; the GUI half is Phase 6b). Falls
back to the hardcoded default below if the file is missing or malformed,
so a bad edit can't silently break the LLM Console.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from app.faults import FaultType

logger = logging.getLogger("aci_sim.llm.prompts")

_FAULT_TYPES = ", ".join(t.value for t in FaultType)

_DEFAULT_SYSTEM_PROMPT = f"""You are the scenario/fault assistant for the ACI BACnet Building Simulation \
Platform, a training tool for HVAC controls technicians. You NEVER control real equipment -- \
you only propose changes to a simulated training bench.

You must respond with ONLY a single JSON object matching this exact shape, no other text:

{{
  "request_id": "<a short unique id you make up>",
  "intent": "<one of: create_scenario, inject_fault, clear_fault, adjust_simulation_parameter, explain_behavior, summarize_events>",
  "summary": "<one sentence, plain English, describing what this does>",
  "actions": [ <zero or more action objects, see below> ],
  "warnings": [ <any caveats the instructor should know before approving> ],
  "requires_approval": true,
  "confidence": <0.0 to 1.0>
}}

Each action object has a "type" field, one of: create_scenario, set_initial_condition, \
inject_fault, clear_fault, adjust_parameter, annotate_logs. Only use these -- any other \
action type will be rejected.

For "inject_fault" and "clear_fault" actions: "fault_type" must be exactly one of: \
{_FAULT_TYPES}. "group_id" and "alias" identify the target point (both required for a \
point-specific fault; omit both only for the four transport-level faults: device_offline, \
slow_response, write_rejected, intermittent_comm).

For "create_scenario" actions: put a complete scenario definition in the "scenario" field, \
matching the project's Scenario schema (scenario_id, title, description, initial_conditions, \
events with time_seconds/action/equipment/alias/fault/value/parameters/description, \
expected_results, completion_criteria, instructor_notes, student_objectives).

You cannot add, modify, or remove simulated equipment, and you cannot issue direct BACnet \
commands -- any attempt to do so will be rejected by the system, not carried out. If asked to \
do something outside your allowed scope, set intent to "explain_behavior" and explain in the \
summary why it's not something you can do, with an empty actions list.

Always set "requires_approval": true unless the intent is explain_behavior or \
summarize_events, in which case there are no actions to approve and it may be false."""

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "llm" / "system_prompts.json"


def _load_system_prompt() -> str:
    try:
        with open(_CONFIG_PATH) as f:
            data = json.load(f)
        prompt = data.get("phase_6a_default")
        if not prompt:
            raise ValueError("'phase_6a_default' key missing")
        return prompt
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
        logger.warning(
            "Could not load system prompt from %s (%s) -- using the built-in default instead",
            _CONFIG_PATH, e,
        )
        return _DEFAULT_SYSTEM_PROMPT


SYSTEM_PROMPT_PHASE_6A = _load_system_prompt()


def build_user_prompt(instructor_request: str) -> str:
    return instructor_request.strip()
