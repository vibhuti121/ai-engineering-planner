"""Domain model.

Two shapes are deliberately kept apart:

* the *model-facing* shape (``TaskDraft`` / ``PlanExtraction``) — what the LLM is asked to produce
* the *API-facing* shape (``Task`` / ``PlanResponse``) — what the browser receives

``TaskDraft`` has no ``order`` field. The model is never given the opportunity to state an
implementation order; order is derived from the dependency graph by ``domain.ordering``.

Each pipeline stage has its own model-facing shape — ``Understanding`` (read), ``TaskGraph``
(graph), ``Verification`` (verify). Keeping them separate is what lets each stage be validated,
cached and replaced on its own; ``PlanExtraction.from_stages`` recomposes the first two into the
shape the ordering, coverage and rendering code already speaks.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Complexity(str, Enum):
    """Estimated effort band. Defined by the rubric in ``prompts/sizing_rubric.txt``."""

    S = "S"
    M = "M"
    L = "L"


class Severity(str, Enum):
    """How much a verifier finding should worry the reader."""

    INFO = "info"
    WARN = "warn"
    ERROR = "error"


# ── Model-facing ──────────────────────────────────────────────────────────────


class Requirement(BaseModel):
    id: str = Field(description="Stable id, e.g. FR-1")
    text: str = Field(description="The requirement, in the PRD's own words where possible")
    category: str = Field(default="general", description="e.g. auth, payments, reporting")


class TaskDraft(BaseModel):
    id: str = Field(description="Stable kebab-case slug, e.g. auth-register-api")
    title: str
    description: str = Field(description="What to build, ending in explicit acceptance criteria")
    dependencies: list[str] = Field(default_factory=list, description="Direct prerequisite task ids")
    complexity: Complexity = Complexity.M
    rationale: str = Field(default="", description="One sentence justifying the complexity band")
    agent_prompt: str = Field(default="", description="Pasteable prompt for an AI coding agent")
    requirement_ids: list[str] = Field(default_factory=list, description="Requirement ids this task implements")


# ── Stage 1: what the reader agent returns ────────────────────────────────────


class Understanding(BaseModel):
    """The PRD as read, before anyone has thought about tasks.

    This is the only artifact produced from the PDF itself. Every later stage plans and audits
    against *this*, never against the document — which is what keeps stages 2 and 3 identical
    across providers and independently cacheable.
    """

    project_summary: str = ""
    domain: str = ""
    actors: list[str] = Field(default_factory=list)
    requirements: list[Requirement] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(
        default_factory=list,
        description="Explicit non-goals. Load-bearing: an unrecorded non-goal becomes an invented task.",
    )
    open_questions: list[str] = Field(
        default_factory=list,
        description="What the PRD leaves unstated. The graph stage resolves these; the reader must not.",
    )


# ── Stage 2: what the graph agent returns ─────────────────────────────────────


class TaskGraph(BaseModel):
    """Tasks and the edges between them. Still no order — that is computed, not generated."""

    tasks: list[TaskDraft] = Field(default_factory=list)


# ── Stage 3: what the verifier agent returns ──────────────────────────────────


class Finding(BaseModel):
    code: str = Field(
        description="requirement-uncovered | dependency-missing | complexity-suspect | "
        "acceptance-criteria-missing"
    )
    severity: Severity = Severity.WARN
    message: str
    task_ids: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)

    @property
    def cites_something(self) -> bool:
        """A finding that names nothing is not actionable, and the verifier is told to omit it."""
        return bool(self.task_ids or self.requirement_ids)


class Verification(BaseModel):
    """The audit result. Advisory: it reports, it never rewrites the plan.

    Note the schema has no ``tasks`` field. That is the enforcement, not just the instruction — a
    verifier that tries to hand back a corrected plan has it dropped at validation.
    """

    verdict: Literal["pass", "pass_with_findings", "fail"] = "pass"
    coverage_note: str = ""
    findings: list[Finding] = Field(default_factory=list)


# ── Composition ───────────────────────────────────────────────────────────────


class PlanExtraction(BaseModel):
    """The two generative stages, recombined.

    Ordering, coverage checks and rendering were written against this shape and are unchanged by
    the split — the pipeline reassembles it rather than teaching four modules about stages.
    """

    project_summary: str = ""
    requirements: list[Requirement] = Field(default_factory=list)
    tasks: list[TaskDraft] = Field(default_factory=list)

    @classmethod
    def from_stages(cls, understanding: Understanding, graph: TaskGraph) -> "PlanExtraction":
        return cls(
            project_summary=understanding.project_summary,
            requirements=understanding.requirements,
            tasks=graph.tasks,
        )


# ── API-facing ────────────────────────────────────────────────────────────────


class Task(TaskDraft):
    order: int = Field(description="1..N, computed by TaskOrderer — never supplied by the model")
    wave: int = Field(description="Tasks sharing a wave have no dependency between them")


class StageMeta(BaseModel):
    """Per-stage accounting. Makes partial reuse visible: a cached stage costs 0 tokens and ~0 ms."""

    name: str
    key: str = ""
    cache: str = Field(default="miss", description="miss | hit-disk | hit-memory | skipped")
    duration_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    prompt_version: str = ""


class PlanMeta(BaseModel):
    plan_id: str
    provider: str
    model: str
    prompt_version: str = Field(description="Summary across stages; the breakdown is prompt_versions")
    cache: str = "miss"
    cached_at: str | None = None
    page_count: int = 0
    task_count: int = 0
    requirement_count: int = 0
    duration_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    source_filename: str = ""
    # ── the three-stage additions; all defaulted so an older stored plan still validates ──
    doc_id: str = ""
    intent_key: str = ""
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    stages: list[StageMeta] = Field(default_factory=list)


class PlanResponse(BaseModel):
    plan_id: str
    project_summary: str
    requirements: list[Requirement]
    tasks: list[Task]
    waves: list[list[str]]
    warnings: list[str]
    markdown: str
    meta: PlanMeta
    # Defaulted so `samples/sample-output.json`, written before the split, still validates.
    understanding: Understanding | None = None
    verification: Verification | None = None
