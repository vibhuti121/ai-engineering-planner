"""Development transport — shells out to the Claude Code CLI.

This is the path that runs on a Claude subscription with no API key. The CLI is used as a plain
completion endpoint (`-p`, one shot, no tool use, no agentic loop) precisely so its output is
comparable to the API transport's: same system text, same parser, same downstream.

Two asymmetries with the API, both handled here and nowhere else:

* **No system channel.** `claude -p` has `--append-system-prompt`, but it *appends* to Claude Code's
  own agent system prompt rather than replacing it — which would make the CLI diverge from the API
  more, not less. So the system text is inlined as the first block of the single turn.
* **No binary input.** The PRD is sent as text extracted by pypdf. That is why `kind` is part of
  every cache key: the two providers genuinely see different input for the same document.

`_render_single_turn` exists so all three agents assemble their turn identically. Three call sites
building a prompt string by hand is three chances to drift.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess

from app.config import PLANNER_TIMEOUT
from app.ports.llm import LlmClient, LlmError, LlmRequest, LlmResult

logger = logging.getLogger("cli")

CLI_BINARY = "claude"

# The CLI is an agent by default: it would load MCP servers and could decide to go read files
# before answering. None of that belongs in a completion call, and all of it is latency. These
# flags pin it to "one prompt in, one answer out" so its behaviour matches the API transport's.
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


def _render_single_turn(request: LlmRequest) -> str:
    """System text, then the document if there is one, then the agent's own prompt."""
    parts = [request.system]
    if request.document is not None:
        document = request.document
        parts.append(
            f"PRD FILENAME: {document.filename}\n"
            f"PRD PAGE COUNT: {document.page_count}\n\n"
            "PRD CONTENT BEGINS\n"
            f"{document.text}\n"
            "PRD CONTENT ENDS"
        )
    parts.append(request.prompt)
    return "\n\n".join(parts)


class ClaudeCliClient(LlmClient):
    kind = "cli"

    def __init__(self, model: str) -> None:
        self.model = model

    def complete(self, request: LlmRequest) -> LlmResult:
        if request.document is not None and not request.document.has_text:
            raise LlmError(_no_text_layer(request), status_code=422)

        prompt = _render_single_turn(request)
        argv = [CLI_BINARY, "-p", "--output-format", "json", "--model", self.model] + NON_AGENTIC_FLAGS

        # This subprocess is where the minutes go. Without these two lines the terminal is silent
        # for the whole of it, which reads as a hang.
        logger.info("stage %s: running %s …", request.stage, " ".join(argv[:6]))
        logger.info(
            "stage %s: prompt is %d chars; waiting up to %ds for the model",
            request.stage,
            len(prompt),
            PLANNER_TIMEOUT,
        )

        try:
            completed = subprocess.run(
                argv, input=prompt, capture_output=True, text=True, timeout=PLANNER_TIMEOUT
            )
        except FileNotFoundError as exc:
            raise LlmError(f"The `{CLI_BINARY}` CLI is not on PATH.") from exc
        except subprocess.TimeoutExpired as exc:
            raise LlmError(
                f"The CLI did not respond within {PLANNER_TIMEOUT}s on the {request.stage} stage."
            ) from exc

        logger.info(
            "stage %s: returncode %d, %d bytes of stdout",
            request.stage,
            completed.returncode,
            len(completed.stdout or ""),
        )

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "no output").strip()[:400]
            raise LlmError(f"The CLI exited with code {completed.returncode}: {detail}")

        # `--output-format json` wraps the answer in an envelope; the answer itself is `.result`.
        body = completed.stdout
        usage: dict = {}
        try:
            envelope = json.loads(completed.stdout)
        except json.JSONDecodeError:
            envelope = None
        if isinstance(envelope, dict) and "result" in envelope:
            # A rate limit arrives as HTTP 200, exit code 0, `is_error: true`, and the refusal text
            # sitting where the answer should be. Without this check that text reaches the parser
            # and is reported as a schema failure — which sends the reader after the wrong bug.
            if envelope.get("is_error"):
                raise LlmError(f"The CLI reported an error: {str(envelope['result'])[:400]}")
            body = envelope["result"]
            usage = envelope.get("usage") or {}

        return LlmResult(
            text=body,
            input_tokens=_input_tokens(usage),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
        )


def _input_tokens(usage: dict) -> int:
    """Sum the three input buckets the CLI reports separately.

    It puts almost everything in `cache_creation_input_tokens` / `cache_read_input_tokens` and
    leaves `input_tokens` at single digits. Reading only the last one shows "2 input tokens" for a
    12k-token prompt, which makes the UI's cost chip actively misleading.
    """
    return sum(
        int(usage.get(field, 0) or 0)
        for field in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    )


def _no_text_layer(request: LlmRequest) -> str:
    document = request.document
    assert document is not None
    # Report what was actually found. "No text could be extracted" is a lie when 37 characters came
    # out, and it sends the reader looking for the wrong problem.
    found = (
        "No text at all could be extracted"
        if document.char_count == 0
        else f"Only {document.char_count} characters of text could be extracted"
    )
    return (
        f"{found} from this {document.page_count}-page PDF, so it is almost certainly a scan or an "
        "image export. The CLI provider can only read a text layer — set ANTHROPIC_API_KEY to use "
        "the API provider, which reads the PDF natively."
    )


__all__ = ["CLI_BINARY", "NON_AGENTIC_FLAGS", "ClaudeCliClient", "cli_available"]
