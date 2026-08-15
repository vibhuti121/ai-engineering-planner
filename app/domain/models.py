"""Domain model.

Two shapes are deliberately kept apart:

* the *model-facing* shape (``TaskDraft`` / ``PlanExtraction``) — what the LLM is asked to produce
* the *API-facing* shape (``Task`` / ``PlanResponse``) — what the browser receives

``TaskDraft`` has no ``order`` field. The model is never given the opportunity to state an
implementation order; order is derived from the dependency graph by ``domain.ordering``.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Complexity(str, Enum):
    """Estimated effort band. Defined by the rubric in ``prompts/system_prompt.txt``."""

    S = "S"
    M = "M"
    L = "L"


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


class PlanExtraction(BaseModel):
    """Exactly what a ``Planner`` returns. Provider-independent."""

    project_summary: str = ""
    requirements: list[Requirement] = Field(default_factory=list)
    tasks: list[TaskDraft] = Field(default_factory=list)


# ── API-facing ────────────────────────────────────────────────────────────────


class Task(TaskDraft):
    order: int = Field(description="1..N, computed by TaskOrderer — never supplied by the model")
    wave: int = Field(description="Tasks sharing a wave have no dependency between them")


class PlanMeta(BaseModel):
    plan_id: str
    provider: str
    model: str
    prompt_version: str
    cache: str = "miss"
    cached_at: str | None = None
    page_count: int = 0
    task_count: int = 0
    requirement_count: int = 0
    duration_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    source_filename: str = ""


class PlanResponse(BaseModel):
    plan_id: str
    project_summary: str
    requirements: list[Requirement]
    tasks: list[Task]
    waves: list[list[str]]
    warnings: list[str]
    markdown: str
    meta: PlanMeta
