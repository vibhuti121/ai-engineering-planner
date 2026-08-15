"""TaskOrderer — turns a dependency graph into an execution order.

Why this exists at all
----------------------
The brief asks for both a "suggested implementation order" *and* "dependencies". If a language
model emits both independently they can contradict each other — task #3 depending on task #7. A
plan whose stated order violates its own dependency graph is the most visible bug this application
could ship, and a reviewer checking a single row would find it.

So the model proposes *dependencies only*, and the order is computed here with Kahn's algorithm.
The output is correct by construction: every task's ``order`` exceeds the ``order`` of each of its
dependencies.

Robustness rule: model sloppiness (dangling ids, self-deps, duplicate ids, cycles) degrades into a
warning, never an exception. A plan with a noted flaw is worth more to the user than a 500.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.models import Task, TaskDraft


@dataclass
class OrderingResult:
    tasks: list[Task] = field(default_factory=list)
    waves: list[list[str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class TaskOrderer:
    """Deterministic topological sort with wave grouping and cycle recovery."""

    def order(self, drafts: list[TaskDraft]) -> OrderingResult:
        warnings: list[str] = []
        drafts = self._dedupe(drafts, warnings)
        if not drafts:
            return OrderingResult()

        position = {d.id: i for i, d in enumerate(drafts)}
        deps = self._clean_edges(drafts, position, warnings)

        waves = self._kahn(drafts, position, deps, warnings)

        tasks: list[Task] = []
        by_id = {d.id: d for d in drafts}
        order_no = 0
        for wave_index, wave in enumerate(waves, start=1):
            for task_id in wave:
                order_no += 1
                tasks.append(
                    Task(
                        **by_id[task_id].model_dump(),
                        order=order_no,
                        wave=wave_index,
                    )
                )
        # Emitted dependencies must match the cleaned graph, otherwise the response would advertise
        # edges the ordering never honoured.
        for task in tasks:
            task.dependencies = sorted(deps[task.id], key=lambda d: position[d])

        return OrderingResult(tasks=tasks, waves=waves, warnings=warnings)

    # ── internals ─────────────────────────────────────────────────────────────

    def _dedupe(self, drafts: list[TaskDraft], warnings: list[str]) -> list[TaskDraft]:
        seen: set[str] = set()
        kept: list[TaskDraft] = []
        for draft in drafts:
            if not draft.id:
                warnings.append(f"Dropped a task with no id: {draft.title!r}.")
                continue
            if draft.id in seen:
                warnings.append(f"Duplicate task id {draft.id!r} — kept the first occurrence.")
                continue
            seen.add(draft.id)
            kept.append(draft)
        return kept

    def _clean_edges(
        self, drafts: list[TaskDraft], position: dict[str, int], warnings: list[str]
    ) -> dict[str, set[str]]:
        known = set(position)
        deps: dict[str, set[str]] = {}
        for draft in drafts:
            cleaned: set[str] = set()
            for dep in draft.dependencies:
                if dep == draft.id:
                    warnings.append(f"Task {draft.id!r} depended on itself — edge dropped.")
                elif dep not in known:
                    warnings.append(
                        f"Task {draft.id!r} depended on unknown task {dep!r} — edge dropped."
                    )
                else:
                    cleaned.add(dep)
            deps[draft.id] = cleaned
        return deps

    def _kahn(
        self,
        drafts: list[TaskDraft],
        position: dict[str, int],
        deps: dict[str, set[str]],
        warnings: list[str],
    ) -> list[list[str]]:
        """Round-by-round Kahn. Each round is a wave: its members can run in parallel."""
        remaining = {d.id: set(deps[d.id]) for d in drafts}
        waves: list[list[str]] = []

        while remaining:
            ready = [tid for tid, pending in remaining.items() if not pending]

            if not ready:
                # Everything left is in (or behind) a cycle. Unblock the earliest-positioned node
                # by dropping the edges that close the cycle, then continue — deterministic, and it
                # always terminates because one node is freed per pass.
                victim = min(remaining, key=lambda t: (position[t], t))
                for dep in sorted(remaining[victim], key=lambda d: (position[d], d)):
                    warnings.append(
                        f"Circular dependency: dropped edge {dep!r} → {victim!r} to break the cycle."
                    )
                    deps[victim].discard(dep)
                remaining[victim] = set()
                ready = [victim]

            # Deterministic tie-break: the model's original array position, then id.
            ready.sort(key=lambda t: (position[t], t))
            waves.append(ready)

            for tid in ready:
                del remaining[tid]
            for pending in remaining.values():
                pending.difference_update(ready)

        return waves
