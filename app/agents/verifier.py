"""③ The verifier — an audit of the finished plan.

It runs **after** `TaskOrderer`, so it sees the computed order and waves. It is told those are
correct by construction and not its business: the order is not a model output, so questioning it
would be questioning a topological sort.

**Report-only, on two counts.** The prompt says so, and `Verification` has no `tasks` field — a
verifier that tries to hand back a corrected plan has it dropped at validation rather than trusted.
Belt and braces, because "please don't rewrite it" is the instruction models are most prone to
helpfully ignoring.

The honest limit, stated here and in ASSUMPTIONS: it audits **plan against understanding**, not plan
against PRD. If the reader missed a requirement, the verifier cannot notice — it has never seen the
document. That is the correct trade for an advisory stage that costs one extra call, and it is why
findings are advisory warnings rather than a gate.
"""

from __future__ import annotations

import json

from app.agents.base import Agent, AgentOutput
from app.domain.models import Task, Understanding, Verification

# Findings are one sentence each and capped at ten, so the ceiling is generous.
MAX_TOKENS = 4000


class VerifierAgent(Agent[Verification]):
    stage = "verify"
    schema = Verification
    what = "a verification report"
    max_tokens = MAX_TOKENS

    @staticmethod
    def projection(tasks: list[Task]) -> list[dict]:
        """The exact view of the plan this stage consumes.

        A `staticmethod` rather than an inline comprehension because the service hashes these same
        bytes into `verify_key`. If the projection and the hash could drift, the cache would key
        stage 3 on input stage 3 never saw.

        Only the fields an audit needs. Sending the whole task — `agent_prompt` included — would
        roughly double the input for text the verifier has no check to run against.
        """
        return [
            {
                "id": task.id,
                "order": task.order,
                "wave": task.wave,
                "title": task.title,
                "description": task.description,
                "dependencies": task.dependencies,
                "complexity": task.complexity.value,
                "rationale": task.rationale,
                "requirement_ids": task.requirement_ids,
            }
            for task in tasks
        ]

    def verify(
        self, understanding: Understanding, tasks: list[Task], waves: list[list[str]]
    ) -> AgentOutput[Verification]:
        plan = self.projection(tasks)
        prompt = (
            "UNDERSTANDING BEGINS\n"
            f"{json.dumps(understanding.model_dump(), indent=2, ensure_ascii=False)}\n"
            "UNDERSTANDING ENDS\n\n"
            "ORDERED PLAN BEGINS\n"
            f"{json.dumps(plan, indent=2, ensure_ascii=False)}\n"
            "ORDERED PLAN ENDS\n\n"
            f"The plan has {len(tasks)} tasks in {len(waves)} waves. "
            "Audit it and return the JSON object now."
        )
        return self._run(prompt)


__all__ = ["VerifierAgent"]
