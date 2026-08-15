"""The one seam that matters: how a PDF becomes a PlanExtraction.

Development runs against the Claude Code CLI (a Claude subscription, no API key). Production runs
against the Anthropic API. Both are implementations of this port, they send the same system prompt
and share the same parser, so swapping one for the other cannot change the response contract.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.domain.models import PlanExtraction
from app.services.pdf_validator import PdfDocument


class PlannerError(Exception):
    """The provider failed or refused. Surfaces as 502 with a real reason, never a stack trace."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass
class PlannerOutput:
    extraction: PlanExtraction
    input_tokens: int = 0
    output_tokens: int = 0


class Planner(ABC):
    #: Part of the cache key — two providers must never share a cache slot, because the CLI adapter
    #: sends extracted text while the API adapter sends the PDF itself.
    kind: str = "unknown"
    model: str = ""

    @abstractmethod
    def plan(self, document: PdfDocument) -> PlannerOutput: ...
