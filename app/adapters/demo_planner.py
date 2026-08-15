"""Fixture adapter — replays a previously captured real run.

Its only job is that someone can clone the repo with no credentials of any kind, start the server,
and see the application work end to end. The fixture is a genuine captured response, not a
hand-written one, and the UI says plainly that demo mode is replaying it.
"""

from __future__ import annotations

import json

from app.config import FIXTURES_DIR
from app.domain.models import PlanExtraction
from app.ports.planner import Planner, PlannerError, PlannerOutput
from app.services.pdf_validator import PdfDocument

FIXTURE_PATH = FIXTURES_DIR / "demo-plan.json"


def fixture_available() -> bool:
    return FIXTURE_PATH.exists()


class DemoPlanner(Planner):
    kind = "demo"

    def __init__(self, model: str = "fixture") -> None:
        self.model = model

    def plan(self, document: PdfDocument) -> PlannerOutput:
        if not FIXTURE_PATH.exists():
            raise PlannerError(
                "Demo mode needs fixtures/demo-plan.json, which is missing.", status_code=500
            )
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        return PlannerOutput(extraction=PlanExtraction.model_validate(payload))
