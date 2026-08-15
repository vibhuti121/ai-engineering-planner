"""The three pipeline agents — the intelligence, separated from the transport.

    PDF ─▶ ① reader ─▶ Understanding ─▶ ② graph ─▶ TaskGraph
                                                       │
                                    TaskOrderer (code) ─┤
                                                       ▼
                                             ③ verifier ─▶ Verification

Each agent owns one job, one prompt, one output schema, and one cache slot. That is the whole point
of the split: a weak requirement extraction and a weak task graph used to be the same
undifferentiated blob, and neither could be fixed without disturbing the other.

**The reader is the only agent that touches the PDF.** Everything downstream reads the previous
stage's JSON. That is what keeps stages 2 and 3 identical across providers, what stops input tokens
from tripling, and what makes each stage independently cacheable.

Naming note, because the word is overloaded here: `TaskDraft.agent_prompt` means "a prompt for a
downstream *coding* agent that will implement that task". These three are pipeline stages, not that.
"""

from app.agents.graph import GraphAgent
from app.agents.json_parser import AgentError, parse_model
from app.agents.reader import ReaderAgent
from app.agents.verifier import VerifierAgent

__all__ = ["AgentError", "GraphAgent", "ReaderAgent", "VerifierAgent", "parse_model"]
