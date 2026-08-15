"""Orchestration: PDF in, PlanResponse out — now across three agents instead of one call.

    validate → intent lookup (L1) → ① read → ② graph → order → ③ verify → render → store

Two caches, deliberately different in kind:

* **L1, in memory, keyed by ``intent_key``.** Computable from the upload alone, so a re-upload of an
  identical PRD returns the stored object without touching the disk or the model. The second
  response is not merely equivalent to the first, it *is* the first.
* **L2, on disk, keyed per stage.** The chained keys mean a changed verifier prompt re-runs stage 3
  and loads stages 1 and 2 from ``artifacts/``. Partial reuse is the whole reason the pipeline was
  split into separately-keyed stages rather than one blob.

Ordering runs **between** graph and verify. The order is computed by `TaskOrderer`, never generated,
and the verifier audits the sequence that came out of it — so it cannot run any earlier.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.agents.base import AgentOutput
from app.agents.graph import GraphAgent
from app.agents.json_parser import AgentError
from app.agents.reader import ReaderAgent
from app.agents.verifier import VerifierAgent
from app.config import MAX_TASKS
from app.domain.models import (
    PlanExtraction,
    PlanMeta,
    PlanResponse,
    StageMeta,
    Task,
    TaskGraph,
    Understanding,
    Verification,
)
from app.domain.ordering import TaskOrderer
from app.ports.llm import LlmClient, LlmError
from app.prompts import STAGES, prompt_version_summary, prompt_versions, stage_prompt_version
from app.services import markdown_renderer
from app.services.artifact_store import ArtifactStore, StageRecord
from app.services.cache_key import content_hash, doc_id, intent_key, stage_key
from app.services.pdf_validator import PdfDocument, validate_and_extract
from app.services.plan_store import PlanStore

logger = logging.getLogger(__name__)

M = TypeVar("M", bound=BaseModel)


class PlanningService:
    def __init__(
        self,
        store: PlanStore,
        artifacts: ArtifactStore,
        orderer: TaskOrderer | None = None,
    ) -> None:
        self._store = store
        self._artifacts = artifacts
        self._orderer = orderer or TaskOrderer()

    def create_plan(
        self,
        raw: bytes,
        filename: str,
        client: LlmClient,
        refresh: bool = False,
        from_stage: str | None = None,
        on_stage: Callable[[str, str], None] | None = None,
    ) -> PlanResponse:
        # Optional so the service does not know or care whether anybody is listening.
        stage = on_stage or (lambda name, detail: None)
        started_at = time.perf_counter()

        stage("validating", f"reading {filename}")
        document = validate_and_extract(raw, filename)
        stage(
            "validating",
            f"{document.page_count} pages, {document.char_count} chars of text layer",
        )

        did = doc_id(document)
        versions = prompt_versions()
        # Asked once, up front, and then used for both the keys and the call — so the key can never
        # claim a model the transport did not actually use.
        models = {name: client.model_for(name) for name in STAGES}
        ikey = intent_key(did, versions, models, client.kind)

        if not refresh and from_stage is None:
            cached = self._store.get(ikey)
            if cached is not None:
                stage("cache-lookup", f"intent {ikey} HIT — returning stored plan, no model call")
                # A copy, so a caller mutating meta.cache cannot corrupt the stored plan.
                served = cached.model_copy(deep=True)
                served.meta.cache = "hit"
                return served
        stage(
            "cache-lookup",
            f"intent {ikey} " + ("BYPASSED (refresh)" if refresh or from_stage else "MISS"),
        )

        self._artifacts.record_document(document, did)

        # ── ① read ────────────────────────────────────────────────────────────
        read_key = stage_key(
            "read",
            parent=did,
            payload_hash="",
            prompt_version=versions["read"],
            model=models["read"],
            kind=client.kind,
        )
        stage("reading", f"{client.kind} · {models['read']} — extracting requirements from the PDF")
        understanding, read_meta = self._run_stage(
            did,
            "read",
            read_key,
            parent_key=did,
            schema=Understanding,
            client=client,
            model=models["read"],
            force=_forced("read", refresh, from_stage),
            run=lambda: ReaderAgent(client).read(document),
        )
        stage(
            "reading",
            f"{len(understanding.requirements)} requirements, "
            f"{len(understanding.open_questions)} open questions ({read_meta.cache})",
        )

        # ── ② graph ───────────────────────────────────────────────────────────
        graph_key = stage_key(
            "graph",
            parent=read_key,
            payload_hash=content_hash(understanding.model_dump(mode="json")),
            prompt_version=versions["graph"],
            model=models["graph"],
            kind=client.kind,
        )
        stage("graphing", f"{models['graph']} — planning tasks and dependencies from the understanding")
        graph, graph_meta = self._run_stage(
            did,
            "graph",
            graph_key,
            parent_key=read_key,
            schema=TaskGraph,
            client=client,
            model=models["graph"],
            force=_forced("graph", refresh, from_stage),
            run=lambda: GraphAgent(client).build(understanding),
        )
        stage("graphing", f"{len(graph.tasks)} tasks drafted ({graph_meta.cache})")

        extraction = PlanExtraction.from_stages(understanding, graph)
        if not extraction.tasks:
            raise LlmError(
                "The model returned no tasks. Is this document actually a PRD?", status_code=422
            )

        # ── order (code, not a model) ─────────────────────────────────────────
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

        # ── ③ verify ──────────────────────────────────────────────────────────
        projection = VerifierAgent.projection(result.tasks)
        verify_key = stage_key(
            "verify",
            parent=graph_key,
            payload_hash=content_hash(projection),
            prompt_version=versions["verify"],
            model=models["verify"],
            kind=client.kind,
        )
        stage("verifying", f"{models['verify']} — reviewing the ordered plan against the understanding")
        verification: Verification | None = None
        try:
            verification, verify_meta = self._run_stage(
                did,
                "verify",
                verify_key,
                parent_key=graph_key,
                schema=Verification,
                client=client,
                model=models["verify"],
                force=_forced("verify", refresh, from_stage),
                run=lambda: VerifierAgent(client).verify(understanding, result.tasks, result.waves),
            )
        except LlmError as exc:
            # Advisory means advisory. A failed review degrades to a warning; it never costs the
            # caller a plan that stages 1 and 2 already paid for.
            logger.warning("verify stage failed (%s); continuing without it", exc.message)
            warnings.append(f"The review stage did not complete: {exc.message}")
            verify_meta = StageMeta(
                name="verify",
                key=verify_key,
                cache="skipped",
                model=models["verify"],
                prompt_version=versions["verify"],
            )
        else:
            # Deliberately not folded into `warnings`. Advice on a working plan is not a problem
            # with it; it travels as `plan.verification` and is rendered at the bottom.
            stage(
                "verifying",
                f"{len(verification.improvements)} improvements, "
                f"{len(verification.open_questions)} open questions ({verify_meta.cache})",
            )

        stages = [read_meta, graph_meta, verify_meta]
        plan_id = verify_key

        plan = PlanResponse(
            plan_id=plan_id,
            project_summary=extraction.project_summary,
            requirements=extraction.requirements,
            tasks=result.tasks,
            waves=result.waves,
            warnings=warnings,
            markdown="",
            understanding=understanding,
            verification=verification,
            meta=PlanMeta(
                plan_id=plan_id,
                provider=client.kind,
                model=_model_summary(models),
                prompt_version=prompt_version_summary(),
                cache="miss",
                cached_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                page_count=document.page_count,
                task_count=len(result.tasks),
                requirement_count=len(extraction.requirements),
                duration_ms=int((time.perf_counter() - started_at) * 1000),
                input_tokens=sum(s.input_tokens for s in stages),
                output_tokens=sum(s.output_tokens for s in stages),
                source_filename=document.filename,
                doc_id=did,
                intent_key=ikey,
                prompt_versions=versions,
                stages=stages,
            ),
        )

        stage("rendering", "markdown")
        plan.markdown = markdown_renderer.render(plan)

        self._store.put(plan, ikey)
        self._persist(did, plan, stages)
        stage("stored", f"plan_id {plan_id} — a re-upload of this PRD is now free")
        return plan

    # ── internals ─────────────────────────────────────────────────────────────

    def _run_stage(
        self,
        doc: str,
        name: str,
        key: str,
        *,
        parent_key: str,
        schema: type[M],
        client: LlmClient,
        model: str,
        force: bool,
        run: Callable[[], AgentOutput],
    ) -> tuple[M, StageMeta]:
        """One stage: try the disk, else call the agent and file everything it produced.

        The raw response is written on **both** paths out of the model call — success and schema
        failure — because a parse failure with no evidence attached is a bug report nobody can act
        on. `AgentError` carries the offending text precisely so this can happen.
        """
        version = stage_prompt_version(name)

        if not force:
            record = self._artifacts.read_stage(doc, name, key)
            if record is not None:
                try:
                    payload = schema.model_validate(record.payload)
                except ValidationError as exc:
                    # A stored payload from an older schema. Same rule as the store itself: a bad
                    # artifact is a miss, not a 500.
                    logger.warning("artifact %s/%s no longer matches its schema (%s); recomputing", name, key, exc.errors()[0]["msg"])
                else:
                    return payload, StageMeta(
                        name=name,
                        key=key,
                        cache="hit-disk",
                        # From the record, not from the config: the artifact was written by whatever
                        # model ran then, and the key guarantees that is the model asked for now.
                        model=record.model or model,
                        input_tokens=record.input_tokens,
                        output_tokens=record.output_tokens,
                        prompt_version=record.prompt_version or version,
                    )

        started = time.perf_counter()
        try:
            output = run()
        except AgentError as exc:
            self._artifacts.write_raw(doc, name, key, exc.raw)
            raise
        duration_ms = int((time.perf_counter() - started) * 1000)

        self._artifacts.write_raw(doc, name, key, output.raw)
        self._write(
            lambda: self._artifacts.write_stage(
                StageRecord(
                    stage=name,
                    key=key,
                    doc_id=doc,
                    parent_key=parent_key,
                    prompt_version=version,
                    provider=client.kind,
                    model=model,
                    duration_ms=duration_ms,
                    input_tokens=output.input_tokens,
                    output_tokens=output.output_tokens,
                    payload=output.payload.model_dump(mode="json"),
                )
            ),
            f"stage record {name}/{key}",
        )

        return output.payload, StageMeta(
            name=name,
            key=key,
            cache="miss",
            model=model,
            duration_ms=duration_ms,
            input_tokens=output.input_tokens,
            output_tokens=output.output_tokens,
            prompt_version=version,
        )

    def _persist(self, doc: str, plan: PlanResponse, stages: list[StageMeta]) -> None:
        self._write(
            lambda: self._artifacts.write_plan(doc, plan, plan.markdown),
            f"plan {plan.plan_id}",
        )
        self._write(
            lambda: self._artifacts.append_manifest(
                doc,
                {
                    "plan_id": plan.plan_id,
                    "intent_key": plan.meta.intent_key,
                    "provider": plan.meta.provider,
                    "model": plan.meta.model,
                    "source_filename": plan.meta.source_filename,
                    "task_count": plan.meta.task_count,
                    "warning_count": len(plan.warnings),
                    "stages": [
                        {"name": s.name, "key": s.key, "cache": s.cache, "prompt_version": s.prompt_version}
                        for s in stages
                    ],
                },
            ),
            "manifest",
        )

    @staticmethod
    def _write(action: Callable[[], None], what: str) -> None:
        """Persistence is best-effort. A read-only or full disk should cost the artifact tree, not
        the plan the caller already waited minutes for."""
        try:
            action()
        except (OSError, ValueError) as exc:
            logger.warning("artifact: could not write %s: %s", what, exc)

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


def _forced(stage: str, refresh: bool, from_stage: str | None) -> bool:
    """`refresh` re-runs everything; `from_stage` re-runs that stage and every stage after it.

    The second exists because the common reason to bypass the cache is "I edited one prompt", and
    re-running the two stages upstream of it wastes minutes to produce identical bytes.
    """
    if refresh:
        return True
    if from_stage is None:
        return False
    if from_stage not in STAGES:
        raise LlmError(
            f"Unknown stage {from_stage!r}. Use one of: {', '.join(STAGES)}.", status_code=400
        )
    return STAGES.index(stage) >= STAGES.index(from_stage)


def _model_summary(models: dict[str, str]) -> str:
    """One string for `PlanMeta.model`, which is typed `str` and shown in a single UI chip.

    Collapses to the plain model name in the ordinary case where all three stages share one, and
    spells out the split only when there is one — the per-stage breakdown lives in `meta.stages`.
    """
    distinct = set(models.values())
    if len(distinct) == 1:
        return distinct.pop()
    return " · ".join(f"{stage}:{models[stage]}" for stage in STAGES)


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
