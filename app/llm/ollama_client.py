"""
Ollama Client (Phase 6a).

Talks to a local Ollama server's HTTP API (default http://localhost:11434).
This is the only file in the project that knows Ollama's specific
endpoints -- everything else works against `LlmActionBundle`, so swapping
to a different local LLM runtime later only touches this file.

Honesty note: this was written and reviewed against Ollama's documented
API shape, but has NOT been tested against a real running Ollama server in
this environment -- no Ollama install is available in the dev sandbox this
project has been built in. `test_connection()` and `list_models()` exist
specifically so the first real check (from the dashboard, once deployed)
is a one-click "does this actually work" rather than discovering it mid-use.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import httpx
from pydantic import ValidationError

from app.llm.action_schema import LlmActionBundle

logger = logging.getLogger("aci_sim.llm.ollama")


class OllamaConnectionError(Exception):
    pass


class OllamaResponseError(Exception):
    """Ollama responded, but not with a parseable LlmActionBundle."""


class OllamaClient:
    def __init__(self, host: str = "http://localhost:11434", model: str = "llama3.1", timeout_seconds: float = 60.0):
        self.host = host.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def test_connection(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.host}/api/tags")
                return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def list_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.host}/api/tags")
                resp.raise_for_status()
                data = resp.json()
                return [m["name"] for m in data.get("models", [])]
        except httpx.HTTPError as e:
            raise OllamaConnectionError(f"could not reach Ollama at {self.host}: {e}") from e

    async def generate_action_bundle(self, system_prompt: str, user_prompt: str, model: Optional[str] = None) -> LlmActionBundle:
        """
        Sends system_prompt + user_prompt to Ollama's /api/generate with
        format="json" (Ollama's structured-output mode), and parses the
        result as an LlmActionBundle. Raises OllamaConnectionError if
        Ollama can't be reached at all, or OllamaResponseError if it
        responded but not with something that parses as a valid bundle --
        the caller (orchestration_service.py) is expected to surface either
        as a clear failure, never to fall back to guessing what the model
        meant.
        """
        payload = {
            "model": model or self.model,
            "prompt": user_prompt,
            "system": system_prompt,
            "format": "json",
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.post(f"{self.host}/api/generate", json=payload)
                resp.raise_for_status()
        except httpx.HTTPError as e:
            raise OllamaConnectionError(f"could not reach Ollama at {self.host}: {e}") from e

        raw = resp.json()
        response_text = raw.get("response", "")
        try:
            parsed = json.loads(response_text)
        except json.JSONDecodeError as e:
            raise OllamaResponseError(f"Ollama's response was not valid JSON: {e}\nRaw: {response_text[:500]}") from e

        try:
            return LlmActionBundle.model_validate(parsed)
        except ValidationError as e:
            raise OllamaResponseError(f"Ollama's JSON did not match the action bundle schema: {e}") from e
