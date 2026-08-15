"""Development adapter — shells out to the Claude Code CLI.

This is the path that runs on a Claude subscription with no API key. The CLI is used as a plain
completion endpoint (`-p`, one shot, no tool use, no agentic loop) precisely so its output is
comparable to the API adapter's: same system prompt, same parser, same downstream.

The CLI cannot be handed a binary, so the PRD is sent as text extracted by pypdf. That is the one
behavioural difference from the API adapter, and it is why `kind` is part of the cache key.
"""

from __future__ import annotations

import json
import shutil
import subprocess

from app.adapters.plan_parser import parse_extraction
from app.config import PLANNER_TIMEOUT
from app.ports.planner import Planner, PlannerError, PlannerOutput
from app.prompts import agent_prompt_guidance, system_prompt
from app.services.pdf_validator import PdfDocument

CLI_BINARY = "claude"

# The CLI is an agent by default: it would load MCP servers and could decide to go read files
# before answering. None of that belongs in a completion call, and all of it is latency. These
# flags pin it to "one prompt in, one answer out" so its behaviour matches the API adapter's.
NON_AGENTIC_FLAGS = [
    "--strict-mcp-config",  # with no --mcp-config, this means: load no MCP servers
    "--disallowedTools",
    "Task",
    "Bash",
    "Read",
    "Edit",
    "Write",
    "Glob",
    "Grep",
    "WebSearch",
    "WebFetch",
    "TodoWrite",
]


def cli_available() -> bool:
    return shutil.which(CLI_BINARY) is not None


class ClaudeCliPlanner(Planner):
    kind = "cli"

    def __init__(self, model: str) -> None:
        self.model = model

    def plan(self, document: PdfDocument) -> PlannerOutput:
        if not document.has_text:
            raise PlannerError(
                "No text could be extracted from this PDF, and the CLI provider cannot read a "
                "scanned document. Set ANTHROPIC_API_KEY to use the API provider, which reads the "
                "PDF natively.",
                status_code=422,
            )

        prompt = (
            f"{system_prompt()}\n\n{agent_prompt_guidance()}\n\n"
            f"PRD FILENAME: {document.filename}\n"
            f"PRD PAGE COUNT: {document.page_count}\n\n"
            "PRD CONTENT BEGINS\n"
            f"{document.text}\n"
            "PRD CONTENT ENDS\n\n"
            "Return the JSON object now."
        )

        try:
            completed = subprocess.run(
                [CLI_BINARY, "-p", "--output-format", "json", "--model", self.model]
                + NON_AGENTIC_FLAGS,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=PLANNER_TIMEOUT,
            )
        except FileNotFoundError as exc:
            raise PlannerError(f"The `{CLI_BINARY}` CLI is not on PATH.") from exc
        except subprocess.TimeoutExpired as exc:
            raise PlannerError(f"The CLI did not respond within {PLANNER_TIMEOUT}s.") from exc

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "no output").strip()[:400]
            raise PlannerError(f"The CLI exited with code {completed.returncode}: {detail}")

        # `--output-format json` wraps the answer in an envelope; the answer itself is `.result`.
        body = completed.stdout
        usage: dict = {}
        try:
            envelope = json.loads(completed.stdout)
        except json.JSONDecodeError:
            envelope = None
        if isinstance(envelope, dict) and "result" in envelope:
            if envelope.get("is_error"):
                raise PlannerError(f"The CLI reported an error: {str(envelope['result'])[:400]}")
            body = envelope["result"]
            usage = envelope.get("usage") or {}

        return PlannerOutput(
            extraction=parse_extraction(body),
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
        )
