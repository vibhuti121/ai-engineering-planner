"""Production adapter — the Anthropic API.

Differs from the CLI adapter in exactly one respect: the PDF is sent **natively**, as a base64
document block, rather than as text extracted by pypdf. PRDs put requirements in tables, and table
structure is the first thing text extraction destroys. Everything else — system prompt, parser,
downstream ordering — is shared.
"""

from __future__ import annotations

import base64

from app.adapters.plan_parser import parse_extraction
from app.config import ANTHROPIC_API_KEY, PLANNER_TIMEOUT
from app.ports.planner import Planner, PlannerError, PlannerOutput
from app.prompts import agent_prompt_guidance, system_prompt
from app.services.pdf_validator import PdfDocument

MAX_TOKENS = 16000


class AnthropicApiPlanner(Planner):
    kind = "api"

    def __init__(self, model: str, api_key: str | None = None) -> None:
        self.model = model
        self._api_key = api_key or ANTHROPIC_API_KEY
        if not self._api_key:
            raise PlannerError("ANTHROPIC_API_KEY is not set.", status_code=500)

    def plan(self, document: PdfDocument) -> PlannerOutput:
        try:
            from anthropic import Anthropic
        except ImportError as exc:  # pragma: no cover - dependency is in requirements.txt
            raise PlannerError("The `anthropic` package is not installed.", status_code=500) from exc

        client = Anthropic(api_key=self._api_key, timeout=float(PLANNER_TIMEOUT))
        content = [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.standard_b64encode(document.raw).decode("ascii"),
                },
            },
            {
                "type": "text",
                "text": (
                    f"{agent_prompt_guidance()}\n\n"
                    f"The attached PDF ({document.filename}, {document.page_count} pages) is the "
                    "PRD. Return the JSON object now."
                ),
            },
        ]

        try:
            message = client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=system_prompt(),
                messages=[{"role": "user", "content": content}],
            )
        except Exception as exc:
            raise PlannerError(f"The Anthropic API call failed: {type(exc).__name__}: {exc}") from exc

        if message.stop_reason == "max_tokens":
            raise PlannerError(
                "The model hit the output limit before finishing the plan. The PRD is likely too "
                "large — split it and plan each part."
            )

        text = "".join(block.text for block in message.content if getattr(block, "type", "") == "text")
        return PlannerOutput(
            extraction=parse_extraction(text),
            input_tokens=getattr(message.usage, "input_tokens", 0) or 0,
            output_tokens=getattr(message.usage, "output_tokens", 0) or 0,
        )
