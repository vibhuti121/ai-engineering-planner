"""② The graph agent — tasks and the edges between them, from the understanding alone.

It never sees the PRD. It is handed the reader's JSON and plans from that.

That constraint was the riskiest assumption in this design, so it was tested before anything was
built on top of it: against the sample PRD it produced 15 tasks with zero dangling dependencies,
zero order fields, and a requirement id on every task — against 17 from the single call that could
see the document. Equivalent coverage, and now independently cacheable.

Still no order. `TaskGraph` has no field for one, the prompt forbids stating one, and the sequence
is computed from these edges by `domain.ordering`.
"""

from __future__ import annotations

import json

from app.agents.base import Agent, AgentOutput
from app.domain.models import TaskGraph, Understanding

# The widest output in the chain: up to 25 tasks, each with a description, acceptance criteria and
# an 80-200 word agent prompt.
MAX_TOKENS = 16000


class GraphAgent(Agent[TaskGraph]):
    stage = "graph"
    schema = TaskGraph
    what = "a task graph"
    max_tokens = MAX_TOKENS

    def build(self, understanding: Understanding) -> AgentOutput[TaskGraph]:
        payload = json.dumps(understanding.model_dump(), indent=2, ensure_ascii=False)
        prompt = (
            "Here is the structured understanding of the PRD, produced by the reading stage. "
            "This is your input — plan from it.\n\n"
            "UNDERSTANDING BEGINS\n"
            f"{payload}\n"
            "UNDERSTANDING ENDS\n\n"
            "Return the JSON object now."
        )
        return self._run(prompt)


__all__ = ["GraphAgent"]
