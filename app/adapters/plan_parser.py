"""Turn whatever the model actually returned into a PlanExtraction.

Shared by both live adapters, so "the model wrapped its JSON in a fence again" is handled once.
Models are asked for bare JSON and mostly comply; this module handles the ways they don't, in
increasing order of desperation, and gives up with a message a human can act on.
"""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from app.domain.models import PlanExtraction
from app.ports.planner import PlannerError

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _candidates(text: str):
    text = (text or "").strip()
    if not text:
        return
    yield text

    fenced = _FENCE.search(text)
    if fenced:
        yield fenced.group(1)

    # Last resort: the outermost braces. Catches a stray "Here is the plan:" preamble.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        yield text[start : end + 1]


def parse_extraction(text: str) -> PlanExtraction:
    last_error = "the response was empty"

    for candidate in _candidates(text):
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = f"the response was not valid JSON ({exc.msg})"
            continue
        if not isinstance(payload, dict):
            last_error = "the response was JSON but not an object"
            continue
        try:
            return PlanExtraction.model_validate(payload)
        except ValidationError as exc:
            first = exc.errors()[0]
            location = ".".join(str(p) for p in first["loc"])
            last_error = f"the JSON did not match the plan schema at {location}: {first['msg']}"
            continue

    raise PlannerError(f"Could not read a plan from the model's response — {last_error}.")
