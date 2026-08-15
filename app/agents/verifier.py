"""③ The reviewer — advice on the finished plan.

It runs **after** `TaskOrderer`, so it sees the computed order and waves. It is told those are
correct by construction and not its business: the order is not a model output, so questioning it
would be questioning a topological sort.

**It advises; it does not grade.** There is no verdict, because there is no standing for one: this
stage has never seen the PRD. It reads the reader's *understanding* and the ordered plan — two
upstream model outputs — so if the reader missed a requirement, the reviewer cannot notice. What it
can honestly produce over those two inputs is what would make the plan better (`improvements`) and
what the PRD leaves for a human to decide (`open_questions`).

**Report-only, on two counts.** The prompt says so, and `Verification` has no `tasks` field — a
reviewer that tries to hand back a corrected plan has it dropped at validation rather than trusted.
Belt and braces, because "please don't rewrite it" is the instruction models are most prone to
helpfully ignoring.

Its output is rendered at the *bottom* of the plan and never enters `plan.warnings`. Advice on a
usable plan is not a problem with it, and putting it up top read like one.
"""

from __future__ import annotations

import json

from app.agents.base import Agent, AgentOutput
from app.domain.models import Task, Understanding, Verification

# Notes are one sentence each, capped at 8 improvements and 6 open questions, so this is generous.
MAX_TOKENS = 4000


class VerifierAgent(Agent[Verification]):
    stage = "verify"
    schema = Verification
    what = "a review of the plan"
    max_tokens = MAX_TOKENS

    @staticmethod
    def projection(tasks: list[Task]) -> list[dict]:
        """The exact view of the plan this stage consumes.

        A `staticmethod` rather than an inline comprehension because the service hashes these same
        bytes into `verify_key`. If the projection and the hash could drift, the cache would key
        stage 3 on input stage 3 never saw.

        Only the fields a review needs. Sending the whole task — `agent_prompt` included — would
        roughly double the input for text the reviewer has nothing to say about.
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
            "Review it and return the JSON object now."
        )
        return self._run(prompt)


__all__ = ["VerifierAgent"]
