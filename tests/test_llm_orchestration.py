"""
Tests for Phase 6a: LLM action schema/validation, the orchestration
service applying actions against a REAL FaultManager/ScenarioEngine (not
mocks), and the Ollama client against a mocked HTTP transport (no real
Ollama server available in this environment -- see ollama_client.py's
docstring).
"""
import json

import httpx
import pytest
from pydantic import ValidationError

from app.config_models import EquipmentGroupConfig
from app.faults import FaultManager, FaultType
from app.llm.action_schema import AllowedActionType, AllowedIntent, LlmAction, LlmActionBundle
from app.llm.action_validator import ActionValidator
from app.llm.ollama_client import OllamaClient, OllamaConnectionError, OllamaResponseError
from app.registry import PointRegistry
from app.scenario import ScenarioEngine
from app.services.audit_service import AuditService
from app.services.orchestration_service import OrchestrationService

pytestmark = pytest.mark.asyncio


# ---- fixtures: a real, small registry + fault manager + scenario engine ----

def _vav_group() -> EquipmentGroupConfig:
    return EquipmentGroupConfig.model_validate({
        "group_id": "ACI-SIM-VAV-1", "instance_offset": 11000,
        "points": [
            {
                "alias": "airflow", "object_type": "analog-value", "object_instance": 80,
                "object_name": "VAV-1 Airflow", "units": "cubic-feet-per-minute",
                "signal_direction": "sim_to_webctrl", "initial_value": 500.0,
            },
            {
                "alias": "damper_position_command", "object_type": "analog-output", "object_instance": 20,
                "object_name": "VAV-1 Damper Position Command", "units": "percent",
                "signal_direction": "webctrl_to_sim", "writable": True, "commandable": True,
            },
        ],
    })


def _site_group() -> EquipmentGroupConfig:
    return EquipmentGroupConfig.model_validate({
        "group_id": "ACI-SIM-SITE", "instance_offset": 0,
        "points": [
            {
                "alias": "oa_temp", "object_type": "analog-value", "object_instance": 80,
                "object_name": "SITE Outside Air Temperature", "units": "degrees-fahrenheit",
                "signal_direction": "sim_to_webctrl", "initial_value": 70.0,
            },
        ],
    })


def _build_stack():
    registry = PointRegistry([_vav_group(), _site_group()])
    registry.build_objects()
    fault_manager = FaultManager()

    class DummySite:
        equipment_id = "ACI-SIM-SITE"
        target_oa_temp_f = 70.0
        target_oa_humidity_pct = 50.0

    dummy_site = DummySite()
    scenario_engine = ScenarioEngine(
        fault_manager, registry, get_sim_seconds=lambda: 0.0, get_equipment=lambda: [dummy_site]
    )
    return registry, fault_manager, scenario_engine, dummy_site


class _InMemoryAuditService:
    """Same interface as AuditService, no filesystem, for fast isolated tests."""

    def __init__(self):
        self.entries = []

    def record(self, event_type, **fields):
        self.entries.append({"event_type": event_type, **fields})

    def recent(self, limit=50):
        return self.entries[-limit:]


# ---------------------------------------------------------------- schema ---

async def test_action_bundle_rejects_unknown_intent():
    with pytest.raises(ValidationError):
        LlmActionBundle.model_validate({
            "request_id": "r1", "intent": "reformat_hard_drive", "summary": "x", "actions": [],
        })


async def test_action_bundle_accepts_well_formed_inject_fault():
    bundle = LlmActionBundle.model_validate({
        "request_id": "r1", "intent": "inject_fault", "summary": "freeze a sensor",
        "actions": [{"type": "inject_fault", "group_id": "ACI-SIM-VAV-1", "alias": "airflow", "fault_type": "frozen_value"}],
        "confidence": 0.9,
    })
    assert bundle.actions[0].type == AllowedActionType.inject_fault


# -------------------------------------------------------------- validator --

async def test_validator_rejects_disallowed_phase_6d_intent():
    registry = PointRegistry([_vav_group()])
    registry.build_objects()
    validator = ActionValidator(registry)

    bundle = LlmActionBundle(
        request_id="r1", intent=AllowedIntent.add_equipment, summary="add a VAV",
        actions=[LlmAction(type=AllowedActionType.add_equipment, equipment_type="vav")],
    )
    result = validator.validate_bundle(bundle)
    assert result.valid is False
    assert any("not enabled in this phase" in e for e in result.errors)


async def test_validator_rejects_fault_on_nonexistent_point():
    registry = PointRegistry([_vav_group()])
    registry.build_objects()
    validator = ActionValidator(registry)

    bundle = LlmActionBundle(
        request_id="r1", intent=AllowedIntent.inject_fault, summary="fault a point that doesn't exist",
        actions=[LlmAction(
            type=AllowedActionType.inject_fault, group_id="ACI-SIM-VAV-1",
            alias="does_not_exist", fault_type="frozen_value",
        )],
    )
    result = validator.validate_bundle(bundle)
    assert result.valid is False
    assert any("does not exist in the running registry" in e for e in result.errors)


async def test_validator_rejects_unknown_fault_type():
    registry = PointRegistry([_vav_group()])
    registry.build_objects()
    validator = ActionValidator(registry)

    bundle = LlmActionBundle(
        request_id="r1", intent=AllowedIntent.inject_fault, summary="bogus fault type",
        actions=[LlmAction(
            type=AllowedActionType.inject_fault, group_id="ACI-SIM-VAV-1",
            alias="airflow", fault_type="turn_it_into_a_toaster",
        )],
    )
    result = validator.validate_bundle(bundle)
    assert result.valid is False
    assert any("unknown fault_type" in e for e in result.errors)


async def test_validator_accepts_valid_create_scenario_action():
    registry = PointRegistry([_vav_group()])
    registry.build_objects()
    validator = ActionValidator(registry)

    scenario_dict = {
        "scenario_id": "llm_test_scenario", "title": "LLM Test", "events": [],
    }
    bundle = LlmActionBundle(
        request_id="r1", intent=AllowedIntent.create_scenario, summary="a trivial scenario",
        actions=[LlmAction(type=AllowedActionType.create_scenario, scenario=scenario_dict)],
    )
    result = validator.validate_bundle(bundle)
    assert result.valid is True, result.errors


async def test_validator_rejects_malformed_scenario():
    registry = PointRegistry([_vav_group()])
    registry.build_objects()
    validator = ActionValidator(registry)

    bundle = LlmActionBundle(
        request_id="r1", intent=AllowedIntent.create_scenario, summary="missing required fields",
        actions=[LlmAction(type=AllowedActionType.create_scenario, scenario={"title": "no scenario_id"})],
    )
    result = validator.validate_bundle(bundle)
    assert result.valid is False
    assert any("scenario definition is invalid" in e for e in result.errors)


async def test_validator_accepts_explain_behavior_with_no_actions():
    registry = PointRegistry([_vav_group()])
    registry.build_objects()
    validator = ActionValidator(registry)

    bundle = LlmActionBundle(
        request_id="r1", intent=AllowedIntent.explain_behavior,
        summary="explains how the VAV reheat sequence works",
        actions=[], warnings=["training tool only, no real equipment affected"],
        requires_approval=True, confidence=0.95,
    )
    result = validator.validate_bundle(bundle)
    assert result.valid is True, result.errors
    assert result.errors == []


async def test_validator_accepts_summarize_events_with_no_actions():
    registry = PointRegistry([_vav_group()])
    registry.build_objects()
    validator = ActionValidator(registry)

    bundle = LlmActionBundle(
        request_id="r1", intent=AllowedIntent.summarize_events,
        summary="summarizes the last few faults", actions=[],
    )
    result = validator.validate_bundle(bundle)
    assert result.valid is True, result.errors


async def test_validator_rejects_action_intent_with_no_actions():
    registry = PointRegistry([_vav_group()])
    registry.build_objects()
    validator = ActionValidator(registry)

    bundle = LlmActionBundle(
        request_id="r1", intent=AllowedIntent.inject_fault,
        summary="claims to inject a fault but carries no actions", actions=[],
    )
    result = validator.validate_bundle(bundle)
    assert result.valid is False
    assert any("bundle has no actions" in e for e in result.errors)


# ---------------------------------------------------------- orchestration --

async def test_orchestration_apply_inject_fault_actually_activates_it():
    registry, fault_manager, scenario_engine, _ = _build_stack()
    audit = _InMemoryAuditService()
    orch = OrchestrationService(
        ollama_client=None, registry=registry, fault_manager=fault_manager,
        scenario_engine=scenario_engine, audit_service=audit,
    )

    bundle = LlmActionBundle(
        request_id="r1", intent=AllowedIntent.inject_fault, summary="freeze VAV-1 airflow",
        actions=[LlmAction(
            type=AllowedActionType.inject_fault, group_id="ACI-SIM-VAV-1",
            alias="airflow", fault_type="frozen_value",
        )],
    )
    result = orch.apply(bundle)

    assert result.applied is True
    active = fault_manager.list_faults()
    assert len(active) == 1
    assert active[0].fault_type == FaultType.frozen_value
    assert active[0].group_id == "ACI-SIM-VAV-1" and active[0].alias == "airflow"
    assert any(e["event_type"] == "applied" for e in audit.entries)


async def test_orchestration_apply_rejects_disallowed_action_and_applies_nothing():
    registry, fault_manager, scenario_engine, _ = _build_stack()
    audit = _InMemoryAuditService()
    orch = OrchestrationService(
        ollama_client=None, registry=registry, fault_manager=fault_manager,
        scenario_engine=scenario_engine, audit_service=audit,
    )

    bundle = LlmActionBundle(
        request_id="r1", intent=AllowedIntent.add_equipment, summary="try to add equipment (should be rejected)",
        actions=[LlmAction(type=AllowedActionType.add_equipment, equipment_type="vav")],
    )
    result = orch.apply(bundle)

    assert result.applied is False
    assert len(fault_manager.list_faults()) == 0, "a rejected bundle must not partially apply"
    assert any(e["event_type"] == "apply_rejected" for e in audit.entries)


async def test_orchestration_apply_create_scenario_registers_it_runnable():
    registry, fault_manager, scenario_engine, _ = _build_stack()
    audit = _InMemoryAuditService()
    orch = OrchestrationService(
        ollama_client=None, registry=registry, fault_manager=fault_manager,
        scenario_engine=scenario_engine, audit_service=audit,
    )

    scenario_dict = {
        "scenario_id": "llm_generated_test", "title": "LLM Generated Test Scenario",
        "events": [{"time_seconds": 0, "action": "set_weather", "parameters": {"outside_air_temperature": 20}}],
    }
    bundle = LlmActionBundle(
        request_id="r1", intent=AllowedIntent.create_scenario, summary="a generated winter scenario",
        actions=[LlmAction(type=AllowedActionType.create_scenario, scenario=scenario_dict)],
    )
    result = orch.apply(bundle)

    assert result.applied is True
    assert "llm_generated_test" in scenario_engine.scenarios
    # And it's actually runnable through the normal ScenarioEngine.start() path:
    scenario_engine.start("llm_generated_test")
    assert scenario_engine.status()["running"] is True


async def test_orchestration_apply_set_initial_condition_updates_site_model():
    registry, fault_manager, scenario_engine, dummy_site = _build_stack()
    audit = _InMemoryAuditService()
    orch = OrchestrationService(
        ollama_client=None, registry=registry, fault_manager=fault_manager,
        scenario_engine=scenario_engine, audit_service=audit,
    )

    bundle = LlmActionBundle(
        request_id="r1", intent=AllowedIntent.adjust_simulation_parameter, summary="make it winter",
        actions=[LlmAction(type=AllowedActionType.set_initial_condition, parameters={"outside_air_temperature": 15.0})],
    )
    result = orch.apply(bundle)

    assert result.applied is True
    assert dummy_site.target_oa_temp_f == 15.0


async def test_orchestration_apply_explain_bundle_is_not_applyable_and_mutates_nothing():
    registry, fault_manager, scenario_engine, dummy_site = _build_stack()
    audit = _InMemoryAuditService()
    orch = OrchestrationService(
        ollama_client=None, registry=registry, fault_manager=fault_manager,
        scenario_engine=scenario_engine, audit_service=audit,
    )

    bundle = LlmActionBundle(
        request_id="r1", intent=AllowedIntent.explain_behavior,
        summary="just an explanation, no changes", actions=[], confidence=0.95,
    )
    result = orch.apply(bundle)

    assert result.applied is False
    assert len(fault_manager.list_faults()) == 0
    assert scenario_engine.scenarios == {}
    assert dummy_site.target_oa_temp_f == 70.0
    assert dummy_site.target_oa_humidity_pct == 50.0
    assert any(e["event_type"] == "apply_noop" for e in audit.entries)
    assert not any(e["event_type"] == "applied" for e in audit.entries)


# ----------------------------------------------------------- ollama client -

async def test_ollama_client_generate_action_bundle_success(httpx_mock):
    valid_bundle = {
        "request_id": "abc", "intent": "explain_behavior", "summary": "test",
        "actions": [], "warnings": [], "requires_approval": False, "confidence": 0.95,
    }
    httpx_mock.add_response(
        url="http://fake-ollama:11434/api/generate",
        json={"response": json.dumps(valid_bundle)},
    )
    client = OllamaClient(host="http://fake-ollama:11434", model="llama3.1")
    bundle = await client.generate_action_bundle("system prompt", "user prompt")
    assert bundle.intent == AllowedIntent.explain_behavior
    assert bundle.request_id == "abc"


async def test_ollama_client_raises_on_connection_failure(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("connection refused"))
    client = OllamaClient(host="http://fake-ollama:11434", model="llama3.1")
    with pytest.raises(OllamaConnectionError):
        await client.generate_action_bundle("system prompt", "user prompt")


async def test_ollama_client_raises_on_malformed_json_response(httpx_mock):
    httpx_mock.add_response(
        url="http://fake-ollama:11434/api/generate",
        json={"response": "this is not json at all"},
    )
    client = OllamaClient(host="http://fake-ollama:11434", model="llama3.1")
    with pytest.raises(OllamaResponseError):
        await client.generate_action_bundle("system prompt", "user prompt")


async def test_ollama_client_raises_on_response_not_matching_schema(httpx_mock):
    httpx_mock.add_response(
        url="http://fake-ollama:11434/api/generate",
        json={"response": json.dumps({"this_is": "not a valid action bundle shape"})},
    )
    client = OllamaClient(host="http://fake-ollama:11434", model="llama3.1")
    with pytest.raises(OllamaResponseError):
        await client.generate_action_bundle("system prompt", "user prompt")


async def test_ollama_client_test_connection_false_when_unreachable(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("connection refused"))
    client = OllamaClient(host="http://fake-ollama:11434")
    assert await client.test_connection() is False
