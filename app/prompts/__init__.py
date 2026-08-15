"""Prompts live as .txt files, not as Python string literals.

They are a graded deliverable and are quoted verbatim into AI_PROMPTS.md, so they need to be
readable and diffable on their own.

Each of the three pipeline stages gets its own system prompt, *composed* from files rather than
written as one blob. Composition buys two things a single file cannot:

* **A shared fragment can be shared.** `sizing_rubric.txt` goes into the graph prompt and the
  verify prompt, so the verifier necessarily applies the same rubric it is auditing against. Two
  copies of a rubric drift; one file cannot.
* **The version can be derived instead of declared.** `stage_prompt_version` hashes the composed
  bytes, so editing a prompt file changes that stage's cache key automatically. The old design was
  a single hand-maintained `PROMPT_VERSION` constant, and forgetting to bump it served stale plans.
  The declared prefix (`graph-v1`) survives only because it is human-meaningful in the README.

The composition is also why editing `agent_prompt_guidance.txt` invalidates the graph stage and
nothing else: it is composed into that stage alone, so only that stage's hash moves.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from typing import Literal

from app.config import PROMPTS_DIR

Stage = Literal["read", "graph", "verify"]

STAGES: tuple[Stage, ...] = ("read", "graph", "verify")

# Which files make up each stage's system prompt, in order. The single source of truth for both
# the prompt text and its version — they cannot disagree because both are computed from this.
_COMPOSITION: dict[str, tuple[str, ...]] = {
    "read": ("read_system.txt",),
    "graph": ("graph_system.txt", "sizing_rubric.txt", "agent_prompt_guidance.txt"),
    "verify": ("verify_system.txt", "sizing_rubric.txt"),
}

# The human-facing half of the version. Bump when a stage's *contract* changes (a new field, a
# different job) — not for wording, which the content hash already tracks.
_DECLARED: dict[str, str] = {"read": "read-v1", "graph": "graph-v1", "verify": "verify-v2"}


@lru_cache(maxsize=None)
def _read(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8").strip()


@lru_cache(maxsize=None)
def stage_system_prompt(stage: str) -> str:
    """The composed system prompt for one stage."""
    try:
        parts = _COMPOSITION[stage]
    except KeyError:
        raise ValueError(f"Unknown prompt stage {stage!r}; expected one of {list(_COMPOSITION)}.") from None
    return "\n\n".join(_read(name) for name in parts)


@lru_cache(maxsize=None)
def stage_prompt_version(stage: str) -> str:
    """`read-v1.9f3c1a02` — the declared contract, then the hash of the bytes actually composed."""
    composed = stage_system_prompt(stage)
    digest = hashlib.sha256(composed.encode("utf-8")).hexdigest()[:8]
    return f"{_DECLARED[stage]}.{digest}"


def prompt_versions() -> dict[str, str]:
    return {stage: stage_prompt_version(stage) for stage in STAGES}


def prompt_version_summary() -> str:
    """One line for the UI banner and `PlanMeta.prompt_version`, which is typed `str`."""
    return " · ".join(f"{stage}:{stage_prompt_version(stage)}" for stage in STAGES)


def agent_prompt_guidance() -> str:
    return _read("agent_prompt_guidance.txt")


__all__ = [
    "STAGES",
    "Stage",
    "agent_prompt_guidance",
    "prompt_version_summary",
    "prompt_versions",
    "stage_prompt_version",
    "stage_system_prompt",
]
