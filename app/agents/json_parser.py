"""Turn whatever the model actually returned into a validated pydantic model.

One parser for all three stages, so "the model wrapped its JSON in a fence again" is handled once.
Models are asked for bare JSON and mostly comply; this handles the ways they don't, in increasing
order of desperation, and gives up with a message a human can act on.

`AgentError` subclasses `LlmError` so the route's existing handler still catches it, while the logs
can distinguish "the provider failed" from "the provider answered and its answer was unusable" —
two different bugs with two different fixes.
"""

from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.ports.llm import LlmError

T = TypeVar("T", bound=BaseModel)

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


class AgentError(LlmError):
    """The model answered, but its answer could not be read as the schema this stage expects.

    Carries the offending text on ``raw`` so the caller can file it under
    ``artifacts/<doc_id>/<stage>/<key>.raw.txt`` before the exception leaves the building. A schema
    failure with no evidence attached is a bug report nobody can act on.
    """

    def __init__(self, message: str, status_code: int = 502, raw: str = "") -> None:
        super().__init__(message, status_code)
        self.raw = raw


def _candidates(text: str):
    text = (text or "").strip()
    if not text:
        return
    yield text

    fenced = _FENCE.search(text)
    if fenced:
        yield fenced.group(1)

    start = text.find("{")
    if start != -1:
        # Decode from the first `{` and ignore whatever follows. This rung exists because the
        # outermost-braces rung below fails the moment the model writes a `}` in a closing
        # sentence — and a short-output stage like the verifier is exactly the one prone to
        # adding "Hope this helps :}" after its JSON.
        try:
            payload, _ = json.JSONDecoder().raw_decode(text, start)
        except ValueError:
            pass
        else:
            yield json.dumps(payload)

    # Last resort: the outermost braces. Catches a stray "Here is the plan:" preamble.
    end = text.rfind("}")
    if start != -1 and end > start:
        yield text[start : end + 1]


def parse_model(text: str, model: type[T], *, what: str) -> T:
    last_error = "the response was empty"
    raw = text or ""

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
            return model.model_validate(payload)
        except ValidationError as exc:
            first = exc.errors()[0]
            location = ".".join(str(p) for p in first["loc"])
            last_error = f"the JSON did not match the {what} schema at {location}: {first['msg']}"
            continue

    raise AgentError(f"Could not read {what} from the model's response — {last_error}.", raw=raw)


__all__ = ["AgentError", "parse_model"]
