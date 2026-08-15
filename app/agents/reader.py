"""① The reader — the only agent that sees the PDF.

It answers one question: *what does this document actually say?* It emits no tasks, no ordering and
no estimates, which is deliberate rather than modest. A single call asked to read and plan at once
will start planning while it reads, and requirements that do not fit the plan it has begun forming
quietly stop being extracted. Separating the two makes the extraction auditable on its own.

Its output is the only artifact derived from the document. Everything after this point plans and
audits against `Understanding`, never against the PDF.
"""

from __future__ import annotations

from app.agents.base import Agent, AgentOutput
from app.domain.document import PdfDocument
from app.domain.models import Understanding

# The understanding is compact by construction — a summary, a requirement list, some short string
# arrays. 4k is comfortable for a 100-page PRD and caps the damage if the model starts narrating.
MAX_TOKENS = 4000


class ReaderAgent(Agent[Understanding]):
    stage = "read"
    schema = Understanding
    what = "an understanding of the PRD"
    max_tokens = MAX_TOKENS

    def read(self, document: PdfDocument) -> AgentOutput[Understanding]:
        prompt = (
            f"The attached document ({document.filename}, {document.page_count} pages) is the PRD.\n"
            "Read it and return the JSON object now."
        )
        return self._run(prompt, document=document)


__all__ = ["ReaderAgent"]
