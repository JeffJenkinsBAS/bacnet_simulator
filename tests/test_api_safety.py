from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from app.api import create_app
from app.llm.action_schema import LlmAction, LlmActionBundle


def _bundle() -> LlmActionBundle:
    return LlmActionBundle(
        request_id="req-test",
        intent="inject_fault",
        summary="Apply a sensor offset for training.",
        actions=[
            LlmAction(
                type="inject_fault",
                group_id="AHU-1",
                alias="oa_temp",
                fault_type="offset",
                parameters={"offset": 5.0},
            )
        ],
        requires_approval=True,
        confidence=0.9,
    )


def _client() -> tuple[TestClient, MagicMock]:
    bundle = _bundle()
    orchestration = MagicMock()
    orchestration.propose = AsyncMock(
        return_value=SimpleNamespace(
            error=None,
            bundle=bundle,
            validation=SimpleNamespace(valid=True, errors=[]),
        )
    )
    orchestration.apply.return_value = SimpleNamespace(
        applied=True,
        action_results=[{"applied": True}],
        error=None,
    )
    orchestration.audit_service.recent.return_value = []

    ollama = MagicMock()
    ollama.test_connection = AsyncMock(return_value=False)
    ollama.host = "http://127.0.0.1:11434"
    ollama.model = "hermes3:3b"

    app = create_app(
        transport=MagicMock(),
        engine=MagicMock(),
        fault_manager=MagicMock(),
        scenario_engine=MagicMock(),
        orchestration_service=orchestration,
        ollama_client=ollama,
    )
    return TestClient(app), orchestration


def test_llm_apply_requires_recorded_matching_one_time_proposal() -> None:
    client, orchestration = _client()
    proposal = client.post("/api/llm/propose", json={"instructor_request": "Offset OA temperature"})
    assert proposal.status_code == 200
    payload = proposal.json()
    assert payload["proposal_token"]

    apply_payload = {
        "bundle": payload["bundle"],
        "proposal_token": payload["proposal_token"],
    }
    first_apply = client.post("/api/llm/apply", json=apply_payload)
    assert first_apply.status_code == 200
    assert first_apply.json()["applied"] is True
    orchestration.apply.assert_called_once()

    replay = client.post("/api/llm/apply", json=apply_payload)
    assert replay.status_code == 403
    assert "already used" in replay.json()["error"]


def test_llm_apply_rejects_tampered_bundle_and_missing_token() -> None:
    client, orchestration = _client()
    proposal = client.post("/api/llm/propose", json={"instructor_request": "Offset OA temperature"}).json()
    proposal["bundle"]["summary"] = "Tampered after instructor preview."

    tampered = client.post(
        "/api/llm/apply",
        json={"bundle": proposal["bundle"], "proposal_token": proposal["proposal_token"]},
    )
    assert tampered.status_code == 403
    assert "does not match" in tampered.json()["error"]
    orchestration.apply.assert_not_called()

    missing = client.post("/api/llm/apply", json={"bundle": proposal["bundle"]})
    assert missing.status_code == 422
