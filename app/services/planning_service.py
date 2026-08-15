"""Orchestration: PDF in, PlanResponse out.

    validate → derive plan id → cache lookup → (miss) call the model → order → scope the prompts
    → render → store

The cache lookup happens before the model call and the store write happens after it, which is the
whole of the "never pay twice for the same task" requirement: an identical PRD returns the stored
object, so the second response is not merely equivalent to the first, it *is* the first.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timezone

from app.config import MAX_TASKS
from app.domain.models import PlanMeta, PlanResponse, Task
from app.domain.ordering import TaskOrderer
from app.ports.planner import Planner, PlannerError
from app.services import markdown_renderer
from app.services.cache_key import compute_plan_id
from app.services.pdf_validator import PdfDocument, validate_and_extract
from app.services.plan_store import PlanStore


class PlanningService:
    def __init__(self, store: PlanStore, orderer: TaskOrderer | None = None) -> None:
        self._store = store
        self._orderer = orderer or TaskOrderer()

    def create_plan(
        self,
        raw: bytes,
        filename: str,
        planner: Planner,
        prompt_version: str,
        refresh: bool = False,
        on_stage: Callable[[str, str], None] | None = None,
    ) -> PlanResponse:
        # Optional so every existing caller and test is unaffected; the service does not know or
        # care whether anybody is listening.
        stage = on_stage or (lambda name, detail: None)

        stage("validating", f"reading {filename}")
        document = validate_and_extract(raw, filename)
        stage(
            "validating",
            f"{document.page_count} pages, {document.char_count} chars of text layer",
        )

        plan_id = compute_plan_id(document, planner.model, planner.kind)

        if not refresh:
            cached = self._store.get(plan_id)
            if cached is not None:
                stage("cache-lookup", f"plan_id {plan_id} HIT — returning stored plan, no model call")
                # Return a copy so a caller mutating meta.cache cannot corrupt the stored plan.
                served = cached.model_copy(deep=True)
                served.meta.cache = "hit"
                return served
        stage(
            "cache-lookup",
            f"plan_id {plan_id} {'BYPASSED (refresh=true)' if refresh else 'MISS'}",
        )

        stage(
            "model-call",
            f"{planner.kind} · {planner.model} — generating, this is the slow step (2-4 min)",
        )
        started = time.perf_counter()
        output = planner.plan(document)
        duration_ms = int((time.perf_counter() - started) * 1000)
        stage(
            "model-returned",
            f"{duration_ms // 1000}s, {len(output.extraction.tasks)} tasks, "
            f"{output.output_tokens} output tokens",
        )

        extraction = output.extraction
        if not extraction.tasks:
            raise PlannerError(
                "The model returned no tasks. Is this document actually a PRD?", status_code=422
            )

        stage("ordering", "topological sort over the dependency graph")
        result = self._orderer.order(extraction.tasks)
        warnings = list(result.warnings)
        if len(result.tasks) > MAX_TASKS:
            warnings.append(
                f"The model produced {len(result.tasks)} tasks (soft limit {MAX_TASKS}); the plan "
                "is probably decomposed below the useful level."
            )
        warnings.extend(_coverage_warnings(extraction, result.tasks))

        stage(
            "ordering",
            f"{len(result.tasks)} tasks → {len(result.waves)} waves, {len(warnings)} warnings",
        )

        self._scope_agent_prompts(result.tasks)

        plan = PlanResponse(
            plan_id=plan_id,
            project_summary=extraction.project_summary,
            requirements=extraction.requirements,
            tasks=result.tasks,
            waves=result.waves,
            warnings=warnings,
            markdown="",
            meta=PlanMeta(
                plan_id=plan_id,
                provider=planner.kind,
                model=planner.model,
                prompt_version=prompt_version,
                cache="miss",
                cached_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                page_count=document.page_count,
                task_count=len(result.tasks),
                requirement_count=len(extraction.requirements),
                duration_ms=duration_ms,
                input_tokens=output.input_tokens,
                output_tokens=output.output_tokens,
                source_filename=document.filename,
            ),
        )
        stage("rendering", "markdown")
        plan.markdown = markdown_renderer.render(plan)

        self._store.put(plan)
        stage("stored", f"plan_id {plan_id} — a re-upload of this PRD is now free")
        return plan

    # ── internals ─────────────────────────────────────────────────────────────

    def _scope_agent_prompts(self, tasks: list[Task]) -> None:
        """Append the boundary the model could not know.

        The model writes each prompt without knowing the final graph. These two lines are what stop
        a fresh agent from rebuilding a prerequisite or wandering into the next task's scope, and
        they are the reason the per-task prompts are actually pasteable.
        """
        title_of = {task.id: task.title for task in tasks}
        dependents: dict[str, list[str]] = {task.id: [] for task in tasks}
        for task in tasks:
            for dep in task.dependencies:
                dependents[dep].append(task.id)

        for task in tasks:
            if not task.agent_prompt:
                continue
            done = [title_of[d] for d in task.dependencies]
            later = [title_of[d] for d in dependents[task.id]]
            lines = [task.agent_prompt.rstrip(), ""]
            lines.append(
                "Already done (do not rebuild): "
                + ("; ".join(done) if done else "nothing — this is a starting task.")
            )
            lines.append(
                "Not your scope (handled separately): "
                + ("; ".join(later) if later else "nothing depends on this task.")
            )
            task.agent_prompt = "\n".join(lines)


def _coverage_warnings(extraction, tasks: list[Task]) -> list[str]:
    """A requirement nobody implements is the failure mode a reader will check for."""
    if not extraction.requirements:
        return []
    covered = {rid for task in tasks for rid in task.requirement_ids}
    known = {r.id for r in extraction.requirements}
    orphans = sorted(known - covered)
    unknown = sorted(covered - known)
    warnings = []
    if orphans:
        warnings.append(f"No task implements: {', '.join(orphans)}.")
    if unknown:
        warnings.append(f"Tasks reference requirement ids not in the PRD: {', '.join(unknown)}.")
    return warnings


__all__ = ["PlanningService", "PdfDocument"]
