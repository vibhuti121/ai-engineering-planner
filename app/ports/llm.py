"""The one seam that matters: how a request for text becomes text.

The previous port was PRD-in / whole-plan-out, which forced prompt building and parsing to live
*inside* each adapter, duplicated across CLI and API. That is exactly what blocked splitting the
work into stages. This port inverts it: **adapters are dumb transports, agents own the thinking.**

The rule that resolves both provider asymmetries in one line: *the agent declares intent, the
adapter chooses representation.* An agent says "this request is about this document"; whether that
becomes a base64 PDF block (API) or extracted text inside a delimiter (CLI) is the adapter's
business, and no agent contains a branch on which provider is live.

`stage` is a **label** — for logging and artifact naming. An adapter may log it; an adapter that
branches on it has reintroduced the coupling this port exists to remove.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.domain.document import PdfDocument


class LlmError(Exception):
    """The provider failed or refused. Surfaces as 502 with a real reason, never a stack trace."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class LlmRequest:
    """A request object rather than a widening kwarg list — `max_tokens` already differs per stage
    (the reader is compact, the graph is not), and timeout and temperature will follow."""

    stage: str
    system: str
    prompt: str
    document: PdfDocument | None = None
    max_tokens: int = 8000


@dataclass
class LlmResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


class LlmClient(ABC):
    #: Part of every cache key — two providers must never share a cache slot, because the CLI
    #: adapter sends extracted text while the API adapter sends the PDF itself.
    kind: str = "unknown"
    model: str = ""

    @abstractmethod
    def complete(self, request: LlmRequest) -> LlmResult: ...

    def model_for(self, stage: str) -> str:
        """Which model this transport will *actually* use for `stage`.

        Per-stage model selection and the cache have to agree, and this method is where they do.
        `stage_key` folds this exact string into the key, so a transport that quietly swapped models
        per stage would otherwise file one model's answer under another model's key and serve it
        back forever. A transport with a single model returns it — which is the default here.

        Note this is *not* the branch-on-stage the class docstring warns about: that rule is about
        **representation** (how a document becomes bytes), which no adapter may vary by stage. Which
        model answers is configuration, and it is declared to the key layer rather than hidden.
        """
        return self.model


__all__ = ["LlmClient", "LlmError", "LlmRequest", "LlmResult"]
