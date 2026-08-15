"""Production transport — the Anthropic API.

Differs from the CLI transport in exactly one respect: when a request carries a document, the PDF is
sent **natively** as a base64 block rather than as text extracted by pypdf. PRDs put requirements in
tables, and table structure is the first thing text extraction destroys. Everything else — the
system text, the parser, the ordering downstream — is shared.

Only the reader stage ever carries a document, so this asymmetry costs one PDF upload per chain
instead of three.

Which **model** answers is per stage here, as it is on the CLI: each stage is its own call, so the
`models` map simply decides what goes in each one.
"""

from __future__ import annotations

import base64
import logging

from app.config import ANTHROPIC_API_KEY, PLANNER_TIMEOUT
from app.ports.llm import LlmClient, LlmError, LlmRequest, LlmResult

logger = logging.getLogger("api")


class AnthropicApiClient(LlmClient):
    kind = "api"

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        models: dict[str, str] | None = None,
    ) -> None:
        #: The fallback, and what every stage uses unless `models` names something else for it.
        self.model = model
        self._models = dict(models or {})
        self._api_key = api_key or ANTHROPIC_API_KEY
        if not self._api_key:
            raise LlmError("ANTHROPIC_API_KEY is not set.", status_code=500)

    def model_for(self, stage: str) -> str:
        return self._models.get(stage) or self.model

    def complete(self, request: LlmRequest) -> LlmResult:
        try:
            from anthropic import Anthropic
        except ImportError as exc:  # pragma: no cover - dependency is in requirements.txt
            raise LlmError("The `anthropic` package is not installed.", status_code=500) from exc

        client = Anthropic(api_key=self._api_key, timeout=float(PLANNER_TIMEOUT))

        content: list[dict] = []
        if request.document is not None:
            content.append(
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": base64.standard_b64encode(request.document.raw).decode("ascii"),
                    },
                }
            )
        content.append({"type": "text", "text": request.prompt})

        model = self.model_for(request.stage)
        logger.info("stage %s: calling %s (max_tokens=%d)", request.stage, model, request.max_tokens)

        try:
            message = client.messages.create(
                model=model,
                max_tokens=request.max_tokens,
                system=request.system,
                messages=[{"role": "user", "content": content}],
            )
        except Exception as exc:
            raise LlmError(
                f"The Anthropic API call failed on the {request.stage} stage: {type(exc).__name__}: {exc}"
            ) from exc

        if message.stop_reason == "max_tokens":
            raise LlmError(
                f"The model hit the {request.max_tokens}-token output limit on the {request.stage} "
                "stage before finishing. The PRD is likely too large — split it and plan each part."
            )

        text = "".join(block.text for block in message.content if getattr(block, "type", "") == "text")
        return LlmResult(
            text=text,
            input_tokens=getattr(message.usage, "input_tokens", 0) or 0,
            output_tokens=getattr(message.usage, "output_tokens", 0) or 0,
        )


__all__ = ["AnthropicApiClient"]
