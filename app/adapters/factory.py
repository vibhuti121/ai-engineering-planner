"""Provider resolution — the only place that decides which adapter is live.

`auto` encodes the intended lifecycle of this application: it was developed against a Claude
subscription through the CLI, and it runs in production against the API. Set the key and the API
takes over; nothing else changes.
"""

from __future__ import annotations

from app.adapters.api_planner import AnthropicApiPlanner
from app.adapters.cli_planner import ClaudeCliPlanner, cli_available
from app.adapters.demo_planner import DemoPlanner, fixture_available
from app.config import ANTHROPIC_API_KEY, PLANNER_MODEL, PLANNER_PROVIDER
from app.ports.planner import Planner, PlannerError


def resolve_provider(requested: str | None = None) -> str:
    choice = (requested or PLANNER_PROVIDER or "auto").lower()
    if choice != "auto":
        return choice
    if ANTHROPIC_API_KEY:
        return "api"
    if cli_available():
        return "cli"
    return "demo"


def build_planner(requested: str | None = None) -> Planner:
    provider = resolve_provider(requested)
    if provider == "api":
        return AnthropicApiPlanner(PLANNER_MODEL)
    if provider == "cli":
        return ClaudeCliPlanner(PLANNER_MODEL)
    if provider == "demo":
        return DemoPlanner()
    raise PlannerError(
        f"Unknown provider {provider!r}. Use one of: auto, api, cli, demo.", status_code=500
    )


def provider_status() -> dict[str, object]:
    provider = resolve_provider()
    return {
        "provider": provider,
        "model": "fixture" if provider == "demo" else PLANNER_MODEL,
        "api_key_present": bool(ANTHROPIC_API_KEY),
        "cli_available": cli_available(),
        "fixture_available": fixture_available(),
    }
