"""Prompts live as .txt files, not as Python string literals.

They are a graded deliverable and are quoted verbatim into AI_PROMPTS.md, so they need to be
readable and diffable on their own. Cached after first read — they never change at runtime, and
`PROMPT_VERSION` in config.py is what makes a change to them invalidate the plan cache.
"""

from __future__ import annotations

from functools import lru_cache

from app.config import PROMPTS_DIR


@lru_cache(maxsize=None)
def _read(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8").strip()


def system_prompt() -> str:
    return _read("system_prompt.txt")


def agent_prompt_guidance() -> str:
    return _read("agent_prompt_guidance.txt")
