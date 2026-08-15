"""Fixture transport — replays a previously captured real run, one file per stage.

Its only job is that someone can clone the repo with no credentials of any kind, start the server,
and see the application work end to end.

This is a real upgrade on the adapter it replaces. The old `DemoPlanner` validated a finished
`PlanExtraction` straight from a JSON file, so demo mode exercised none of the parsing, none of the
agents, and none of the stage composition — the parts most likely to be broken. Returning raw
response *text* instead means **demo mode now runs the entire production path except the network**.

The fixtures are captured, not authored: `tools/refresh_demo_fixtures.py` copies the `.raw.txt`
files out of a real run's artifact tree, so they cannot quietly drift into a shape no model would
actually emit.
"""

from __future__ import annotations

import json

from app.config import FIXTURES_DIR
from app.ports.llm import LlmClient, LlmError, LlmRequest, LlmResult

DEMO_DIR = FIXTURES_DIR / "demo"
PROVENANCE_PATH = DEMO_DIR / "provenance.json"


def fixture_path(stage: str) -> str:
    return str(DEMO_DIR / f"{stage}.raw.txt")


def fixture_available() -> bool:
    """True only if every stage has a fixture — a half-populated demo fails mid-chain, which is a
    worse first impression than refusing to start in demo mode at all."""
    return all((DEMO_DIR / f"{stage}.raw.txt").exists() for stage in ("read", "graph", "verify"))


def provenance() -> dict:
    try:
        return json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


class FixtureLlmClient(LlmClient):
    kind = "demo"

    def __init__(self, model: str = "fixture") -> None:
        self.model = model

    def complete(self, request: LlmRequest) -> LlmResult:
        """Replays by stage, ignoring the prompt entirely.

        Deliberate: the fixture answers whatever is asked, so demo mode works for *any* uploaded
        PDF rather than only the one that was captured. The UI says plainly that it is a replay.
        """
        path = DEMO_DIR / f"{request.stage}.raw.txt"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise LlmError(
                f"Demo mode needs {path.relative_to(FIXTURES_DIR.parent)}, which is missing or "
                "unreadable. Regenerate it with tools/refresh_demo_fixtures.py after a live run.",
                status_code=500,
            ) from exc
        return LlmResult(text=text)


__all__ = ["DEMO_DIR", "FixtureLlmClient", "fixture_available", "fixture_path", "provenance"]
