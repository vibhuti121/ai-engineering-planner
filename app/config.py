"""Single place where environment turns into settings.

Read once at import. Everything downstream takes values from here rather than reaching for
``os.environ`` itself, so the active configuration is inspectable in one file.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("config")

BASE_DIR = Path(__file__).resolve().parent
REPO_DIR = BASE_DIR.parent
PROMPTS_DIR = BASE_DIR / "prompts"
STATIC_DIR = BASE_DIR / "static"
FIXTURES_DIR = REPO_DIR / "fixtures"
MODELS_FILE = REPO_DIR / "models.json"


def _load_dotenv() -> None:
    """Minimal .env reader — avoids a dependency for one job we do once at boot."""
    path = REPO_DIR / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_dotenv()

# Intermediate stage outputs, one folder per PRD. Tracked in git on purpose: a fresh clone ships a
# complete worked example, and the reuse claim can be inspected without spending a token. Override
# with ARTIFACTS_DIR — the tests point it at a tmp_path so a test run never touches the real tree.
ARTIFACTS_DIR = Path(os.environ.get("ARTIFACTS_DIR", "").strip() or (REPO_DIR / "artifacts"))

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
PLANNER_PROVIDER = os.environ.get("PLANNER_PROVIDER", "auto").strip().lower()
PLANNER_MODEL = os.environ.get("PLANNER_MODEL", "claude-sonnet-5").strip()

# One model per pipeline stage. The three stages are not equally hard — reading a PRD into a
# requirement list is extraction, building the task graph is the actual reasoning, and auditing a
# finished plan is checking — so paying the graph stage's price for all three is the easy waste in
# this pipeline. Every stage falls back to PLANNER_MODEL, so a single-model setup keeps working
# untouched, and both the API and CLI transports honour the split (each stage is its own call).
#
# The stage names are literal here rather than imported from app.prompts, because app.prompts
# imports this module.
PIPELINE_STAGES: tuple[str, ...] = ("read", "graph", "verify")


def _read_models_file() -> dict[str, str]:
    """Load `models.json` — the one file to edit when changing which model runs which stage.

    Tracked in git, unlike `.env`: it holds no secret, and which model plans the graph is a property
    of the project rather than of one laptop.

    A missing, unreadable or malformed file is a *fallback*, never an exception — the same rule the
    artifact store follows for a corrupt artifact. Losing the split degrades the run to one model;
    refusing to boot over a stray comma loses the application.
    """
    try:
        raw = json.loads(MODELS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Ignoring %s (%s) — falling back to PLANNER_MODEL for every stage.", MODELS_FILE, exc)
        return {}
    if not isinstance(raw, dict):
        logger.warning("Ignoring %s — expected a JSON object, got %s.", MODELS_FILE, type(raw).__name__)
        return {}
    # Non-string values (a nested object, a number) are dropped rather than coerced: str(42) would
    # become a model id that fails only once a stage actually calls out, minutes into a run.
    return {key: value.strip() for key, value in raw.items() if isinstance(value, str)}


_MODELS_FILE_VALUES = _read_models_file()

# Resolution order per stage, highest first: the PLANNER_MODEL_<STAGE> env var (so CI and tests can
# pin one stage without editing a tracked file), then this stage's entry in models.json, then that
# file's "default", then PLANNER_MODEL. A blank at any level means "not set" and falls through —
# which is what lets read/verify stay empty in models.json until each one has been chosen.
PLANNER_MODELS: dict[str, str] = {
    stage: (
        os.environ.get(f"PLANNER_MODEL_{stage.upper()}", "").strip()
        or _MODELS_FILE_VALUES.get(stage, "")
        or _MODELS_FILE_VALUES.get("default", "")
        or PLANNER_MODEL
    )
    for stage in PIPELINE_STAGES
}
PLANNER_TIMEOUT = int(os.environ.get("PLANNER_TIMEOUT", "600"))
PLAN_CACHE_SIZE = int(os.environ.get("PLAN_CACHE_SIZE", "100"))

MAX_UPLOAD_BYTES = 32 * 1024 * 1024
MAX_PAGES = 100
MAX_TASKS = 40

# Below this many extracted characters *per page*, a PDF is treated as having no usable text layer
# — i.e. a scan or an image export. Deliberately a density and not a flat count: a one-page PDF
# holding a single short brief is a legitimate document, while a ten-page scan whose only
# extractable characters are page numbers is not, and a flat minimum cannot tell those apart.
MIN_TEXT_CHARS_PER_PAGE = 25
