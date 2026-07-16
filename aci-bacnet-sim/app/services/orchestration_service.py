"""
Orchestration Service (Phase 6a).

The one place that turns a validated LlmActionBundle into real calls
against the existing, already-tested FaultManager and ScenarioEngine. This
file does NOT talk to bacpypes3 or the BACnet transport directly -- it
goes through the exact same APIs the Instructor Panel's REST endpoints
already use, so an LLM-proposed fault or scenario behaves identically to
one an instructor triggered by hand.

Two-step flow, matching the "preview before apply" requirement:
  propose() -- calls Ollama, validates, returns the bundle. Nothing in the
               simulator has changed yet.
  apply()   -- re-validates (never trust a bundle handed back by a client;
               re-validate what's about to be applied, not what was
               originally proposed), then executes each action.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from app.faults import FaultManager, FaultType
from app.llm.action_schema import AllowedActionType, LlmAction, LlmActionBundle
from app.llm.action_validator import ActionValidator, ValidationResult
from app.llm.ollama_client import OllamaClient, OllamaConnectionError, OllamaResponseError
from app.llm.prompt_templates import SYSTEM_PROMPT_PHASE_6A, build_user_prompt
from app.registry import PointRegistry
from app.scenario import Scenario, ScenarioEngine
from app.services.audit_service import AuditService

logger = logging.getLogger("aci_sim.llm.orchestration")


@dataclass
class ProposeResult:
    bundle: Optional[LlmActionBundle]
    validation: Optional[ValidationResult]
    error: Optional[str] = None


@dataclass
class ApplyResult:
    applied: bool
    action_results: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None


class OrchestrationService:
    def __init__(
        self,
        ollama_client: OllamaClient,
        registry: PointRegistry,
        fault_manager: FaultManager,
        scenario_engine: ScenarioEngine,
        audit_service: AuditService,
    ):
        self.ollama_client = ollama_client
        self.registry = registry
        self.fault_manager = fault_manager
        self.scenario_engine = scenario_engine
        self.audit_service = audit_service
        self.validator = ActionValidator(registry)

    async def propose(self, instructor_request: str, request_id: str) -> ProposeResult:
        try:
            bundle = await self.ollama_client.generate_action_bundle(
                system_prompt=SYSTEM_PROMPT_PHASE_6A,
                user_prompt=build_user_prompt(instructor_request),
            )
        except (OllamaConnectionError, OllamaResponseError) as e:
            self.audit_service.record("proposal_failed", request_id=request_id, request=instructor_request, error=str(e))
            return ProposeResult(bundle=None, validation=None, error=str(e))

        bundle.request_id = bundle.request_id or request_id
        validation = self.validator.validate_bundle(bundle)

        self.audit_service.record(
            "proposed",
            request_id=bundle.request_id,
            request=instructor_request,
            bundle=bundle.model_dump(),
            validation_errors=validation.errors,
        )
        return ProposeResult(bundle=bundle, validation=validation)

    def apply(self, bundle: LlmActionBundle) -> ApplyResult:
        # Never trust a bundle handed back by a client without re-checking it --
        # it may have been edited, or time may have passed since it was proposed.
        validation = self.validator.validate_bundle(bundle)
        if not validation.valid:
            self.audit_service.record(
                "apply_rejected", request_id=bundle.request_id, errors=validation.errors
            )
            return ApplyResult(applied=False, error="; ".join(validation.errors))

        results = []
        try:
            for action in bundle.actions:
                results.append(self._apply_action(action))
        except Exception as e:  # noqa: BLE001 - surface as a clean apply failure, not a 500
            logger.exception("Failed applying LLM action bundle %s", bundle.request_id)
            self.audit_service.record("apply_failed", request_id=bundle.request_id, error=str(e), partial_results=results)
            return ApplyResult(applied=False, action_results=results, error=str(e))

        self.audit_service.record("applied", request_id=bundle.request_id, action_results=results)
        return ApplyResult(applied=True, action_results=results)

    def _apply_action(self, action: LlmAction) -> dict[str, Any]:
        if action.type == AllowedActionType.inject_fault:
            fault_id = f"llm:{action.group_id}.{action.alias}.{action.fault_type}" if action.alias else f"llm:transport.{action.fault_type}"
            instance = self.fault_manager.set_fault(
                fault_id=fault_id,
                fault_type=FaultType(action.fault_type),
                group_id=action.group_id,
                alias=action.alias,
                parameters=action.parameters,
            )
            return {"type": "inject_fault", "fault_id": instance.fault_id}

        if action.type == AllowedActionType.clear_fault:
            fault_id = f"llm:{action.group_id}.{action.alias}.{action.fault_type}" if action.alias else f"llm:transport.{action.fault_type}"
            cleared = self.fault_manager.clear_fault(fault_id)
            return {"type": "clear_fault", "fault_id": fault_id, "cleared": cleared}

        if action.type == AllowedActionType.create_scenario:
            scenario = Scenario.model_validate(action.scenario)
            self.scenario_engine.register_scenario(scenario)
            return {"type": "create_scenario", "scenario_id": scenario.scenario_id}

        if action.type == AllowedActionType.set_initial_condition:
            site = self.scenario_engine._find_equipment("ACI-SIM-SITE")
            applied = {}
            if site is not None:
                if "outside_air_temperature" in action.parameters:
                    site.target_oa_temp_f = float(action.parameters["outside_air_temperature"])
                    applied["outside_air_temperature"] = site.target_oa_temp_f
                if "outside_air_humidity" in action.parameters:
                    site.target_oa_humidity_pct = float(action.parameters["outside_air_humidity"])
                    applied["outside_air_humidity"] = site.target_oa_humidity_pct
            return {"type": "set_initial_condition", "applied": applied}

        if action.type == AllowedActionType.adjust_parameter:
            # Currently only the site weather parameters are adjustable this way in Phase 6a.
            site = self.scenario_engine._find_equipment("ACI-SIM-SITE")
            if site is not None and action.parameter_name == "outside_air_temperature":
                site.target_oa_temp_f = float(action.value)
            elif site is not None and action.parameter_name == "outside_air_humidity":
                site.target_oa_humidity_pct = float(action.value)
            else:
                raise ValueError(f"adjust_parameter: unsupported parameter_name '{action.parameter_name}'")
            return {"type": "adjust_parameter", "parameter_name": action.parameter_name, "value": action.value}

        if action.type == AllowedActionType.annotate_logs:
            logger.info("LLM annotation: %s", action.note)
            return {"type": "annotate_logs", "note": action.note}

        raise ValueError(f"no apply handler for action type '{action.type.value}' -- this should have been caught by the validator")
