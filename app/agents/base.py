"""What all three agents share: build a request, send it, parse the answer, keep the raw text.

The raw text is returned alongside the parsed model rather than discarded, because the caller writes
it to `artifacts/<doc_id>/<stage>/<key>.raw.txt` *before* parsing. That single decision is what turns
a schema failure from "the run died, try again" into "here is exactly what the model said, diff it
against the prompt" — and it makes demo fixtures a file copy instead of a hand-written mock.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from pydantic import BaseModel

from app.agents.json_parser import parse_model
from app.domain.document import PdfDocument
from app.ports.llm import LlmClient, LlmRequest, LlmResult
from app.prompts import stage_prompt_version, stage_system_prompt

T = TypeVar("T", bound=BaseModel)


@dataclass
class AgentOutput(Generic[T]):
    payload: T
    raw: str
    input_tokens: int = 0
    output_tokens: int = 0

    @classmethod
    def of(cls, payload: T, result: LlmResult) -> "AgentOutput[T]":
        return cls(
            payload=payload,
            raw=result.text,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )


class Agent(Generic[T]):
    """One stage. Subclasses supply the stage name, the output schema, and the user turn."""

    stage: str = ""
    schema: type[T]
    what: str = ""
    max_tokens: int = 8000

    def __init__(self, client: LlmClient) -> None:
        self.client = client

    @property
    def prompt_version(self) -> str:
        return stage_prompt_version(self.stage)

    def _run(self, prompt: str, *, document: PdfDocument | None = None) -> AgentOutput[T]:
        result = self.client.complete(
            LlmRequest(
                stage=self.stage,
                system=stage_system_prompt(self.stage),
                prompt=prompt,
                document=document,
                max_tokens=self.max_tokens,
            )
        )
        return AgentOutput.of(parse_model(result.text, self.schema, what=self.what), result)


__all__ = ["Agent", "AgentOutput"]
