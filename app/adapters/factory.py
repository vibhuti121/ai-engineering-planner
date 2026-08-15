"""Provider resolution — the only place that decides which transport is live.

`auto` encodes the intended lifecycle of this application: it was developed against a Claude
subscription through the CLI, and it runs in production against the API. Set the key and the API
takes over; nothing else changes. With neither, it falls back to replaying captured fixtures so a
fresh clone still works.
"""

from __future__ import annotations

from app.adapters.api_client import AnthropicApiClient
from app.adapters.cli_client import ClaudeCliClient, cli_available
from app.adapters.fixture_client import FixtureLlmClient, fixture_available
from app.config import ANTHROPIC_API_KEY, PLANNER_MODEL, PLANNER_MODELS, PLANNER_PROVIDER
from app.ports.llm import LlmClient, LlmError


def resolve_provider(requested: str | None = None) -> str:
    choice = (requested or PLANNER_PROVIDER or "auto").lower()
    if choice != "auto":
        return choice
    if ANTHROPIC_API_KEY:
        return "api"
    if cli_available():
        return "cli"
    return "demo"


def build_client(requested: str | None = None) -> LlmClient:
    provider = resolve_provider(requested)
    if provider == "api":
        return AnthropicApiClient(PLANNER_MODEL, models=PLANNER_MODELS)
    if provider == "cli":
        return ClaudeCliClient(PLANNER_MODEL, models=PLANNER_MODELS)
    if provider == "demo":
        return FixtureLlmClient()
    raise LlmError(f"Unknown provider {provider!r}. Use one of: auto, api, cli, demo.", status_code=500)


def provider_status() -> dict[str, object]:
    provider = resolve_provider()
    return {
        "provider": provider,
        "model": "fixture" if provider == "demo" else PLANNER_MODEL,
        # Empty for `demo` on purpose: the fixture transport calls no model at all and reports the
        # literal "fixture", so publishing a per-stage map there would be a health endpoint that lies.
        "models": {} if provider == "demo" else dict(PLANNER_MODELS),
        "api_key_present": bool(ANTHROPIC_API_KEY),
        "cli_available": cli_available(),
        "fixture_available": fixture_available(),
    }


__all__ = ["build_client", "provider_status", "resolve_provider"]
