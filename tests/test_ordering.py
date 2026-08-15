"""The load-bearing test. Everything else the app does is plumbing around this."""

from __future__ import annotations

import pytest

from app.domain.models import TaskDraft
from app.domain.ordering import TaskOrderer


def draft(task_id: str, deps: list[str] | None = None) -> TaskDraft:
    return TaskDraft(
        id=task_id,
        title=task_id.replace("-", " ").title(),
        description="…",
        dependencies=deps or [],
    )


def order_map(result) -> dict[str, int]:
    return {t.id: t.order for t in result.tasks}


def assert_order_respects_dependencies(result) -> None:
    """The invariant the whole design exists to guarantee."""
    orders = order_map(result)
    for task in result.tasks:
        for dep in task.dependencies:
            assert orders[dep] < orders[task.id], f"{dep} must precede {task.id}"


def test_dependencies_decide_order_not_input_position():
    # 'c' is listed first but depends on everything, so it must be ordered last.
    result = TaskOrderer().order([draft("c", ["a", "b"]), draft("a"), draft("b", ["a"])])

    assert [t.id for t in result.tasks] == ["a", "b", "c"]
    assert order_map(result) == {"a": 1, "b": 2, "c": 3}
    assert_order_respects_dependencies(result)
    assert result.warnings == []


def test_independent_tasks_share_a_wave():
    result = TaskOrderer().order([draft("a"), draft("b"), draft("c", ["a", "b"])])

    assert result.waves == [["a", "b"], ["c"]]
    assert {t.id: t.wave for t in result.tasks} == {"a": 1, "b": 1, "c": 2}


def test_dangling_dependency_is_dropped_with_a_warning():
    result = TaskOrderer().order([draft("a", ["ghost"]), draft("b", ["a"])])

    assert [t.id for t in result.tasks] == ["a", "b"]
    assert [t.dependencies for t in result.tasks] == [[], ["a"]]
    assert any("ghost" in w for w in result.warnings)


def test_self_dependency_is_dropped_with_a_warning():
    result = TaskOrderer().order([draft("a", ["a"])])

    assert result.tasks[0].dependencies == []
    assert any("itself" in w for w in result.warnings)


def test_cycle_is_broken_rather_than_raised():
    result = TaskOrderer().order([draft("a", ["c"]), draft("b", ["a"]), draft("c", ["b"])])

    assert len(result.tasks) == 3
    assert any("Circular dependency" in w for w in result.warnings)
    assert_order_respects_dependencies(result)  # still coherent after the break


def test_duplicate_ids_keep_the_first_occurrence():
    result = TaskOrderer().order([draft("a"), draft("a"), draft("b", ["a"])])

    assert [t.id for t in result.tasks] == ["a", "b"]
    assert any("Duplicate" in w for w in result.warnings)


def test_ordering_is_deterministic_across_runs():
    drafts = [draft("d", ["a"]), draft("a"), draft("c", ["a"]), draft("b", ["a"])]

    first = TaskOrderer().order(drafts)
    second = TaskOrderer().order(drafts)

    # Ties are broken by the model's original array position, then id — so 'd' precedes 'c'.
    assert [t.id for t in first.tasks] == ["a", "d", "c", "b"]
    assert [t.id for t in first.tasks] == [t.id for t in second.tasks]
    assert first.waves == second.waves


def test_empty_input_is_not_an_error():
    result = TaskOrderer().order([])

    assert result.tasks == [] and result.waves == [] and result.warnings == []


@pytest.mark.parametrize("size", [1, 5, 25])
def test_chain_of_any_length_orders_cleanly(size: int):
    drafts = [draft("t0")] + [draft(f"t{i}", [f"t{i - 1}"]) for i in range(1, size)]

    result = TaskOrderer().order(drafts)

    assert [t.order for t in result.tasks] == list(range(1, size + 1))
    assert len(result.waves) == size  # a pure chain parallelises not at all
    assert_order_respects_dependencies(result)
