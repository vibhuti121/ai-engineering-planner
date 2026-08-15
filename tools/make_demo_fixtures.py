"""Build `fixtures/demo/` — the no-credentials demo path — offline, with no model call.

`FixtureLlmClient` replays one `.raw.txt` per stage, so demo mode needs three files that look like
what each agent actually returned. This script re-projects them from `fixtures/demo-plan.json`, a
real captured run of the pre-split single-prompt planner.

**What that capture does and does not contain**, because the difference matters and the generated
`provenance.json` says so out loud:

* `read.raw.txt` and `graph.raw.txt` are re-projections. The bytes are new; the content — the summary,
  the 14 requirements, the 17 tasks and their edges — came out of a real model run.
* `verify.raw.txt` is **not** a capture. v1 had no review stage, so there is nothing to re-project.
  It is synthesized here, and every claim in it is *computed* by this script (a set difference over
  `requirement_ids`), not asserted. `open_questions` comes out empty for the same reason: judging
  what a PRD leaves undecided is the one thing a set difference cannot do, and a fixture that
  claimed to be a captured review would be the one kind of dishonesty a demo can't afford.

Re-run this after any change to the stage schemas — a fixture that no longer validates makes demo
mode degrade to "the review stage did not complete".

    python3 tools/make_demo_fixtures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "fixtures" / "demo-plan.json"
DEMO_DIR = REPO / "fixtures" / "demo"


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  wrote {path.relative_to(REPO)}  ({path.stat().st_size:,} bytes)")


def main() -> int:
    if not SOURCE.exists():
        print(f"missing {SOURCE.relative_to(REPO)} — nothing to project from", file=sys.stderr)
        return 1

    plan = json.loads(SOURCE.read_text(encoding="utf-8"))
    requirements = plan.get("requirements", [])
    tasks = plan.get("tasks", [])
    DEMO_DIR.mkdir(parents=True, exist_ok=True)

    # ── ① read ────────────────────────────────────────────────────────────────
    # The v1 prompt asked for a summary and requirements and nothing else, so the remaining
    # Understanding fields stay empty rather than being invented. They are all defaulted.
    _write(
        DEMO_DIR / "read.raw.txt",
        {
            "project_summary": plan.get("project_summary", ""),
            "domain": "",
            "actors": [],
            "requirements": requirements,
            "constraints": [],
            "out_of_scope": [],
            "open_questions": [],
        },
    )

    # ── ② graph ───────────────────────────────────────────────────────────────
    # Tasks carry no `order` — that was never a model output, and TaskOrderer recomputes it on
    # every replay. Dropping it here keeps the fixture honest about the contract.
    _write(
        DEMO_DIR / "graph.raw.txt",
        {"tasks": [{k: v for k, v in task.items() if k not in ("order", "wave")} for task in tasks]},
    )

    # ── ③ verify ──────────────────────────────────────────────────────────────
    known = {r["id"] for r in requirements}
    covered = {rid for task in tasks for rid in task.get("requirement_ids", [])}
    uncovered = sorted(known - covered)

    improvements = [
        {
            "text": f"Add a task that implements {rid} — no task in the plan claims it.",
            "task_ids": [],
            "requirement_ids": [rid],
        }
        for rid in uncovered
    ]
    _write(
        DEMO_DIR / "verify.raw.txt",
        {
            "coverage_note": (
                f"{len(covered & known)} of {len(known)} requirements are implemented by at least "
                f"one of the {len(tasks)} tasks. Computed by tools/make_demo_fixtures.py, not "
                "reviewed by a model — see provenance.json."
            ),
            "improvements": improvements,
            # Left empty on purpose: what a PRD leaves undecided is a judgement, and this script
            # only does arithmetic. Better an honest empty list than an invented question.
            "open_questions": [],
        },
    )

    _write(
        DEMO_DIR / "provenance.json",
        {
            "source": "fixtures/demo-plan.json",
            "generated_by": "tools/make_demo_fixtures.py",
            "read": "re-projected from a real captured run of the pre-split single-prompt planner; "
            "domain/actors/constraints/out_of_scope/open_questions were not captured by that prompt "
            "and are empty rather than invented",
            "graph": "re-projected from the same capture; order/wave stripped because they are "
            "computed by TaskOrderer, never generated",
            "verify": "SYNTHESIZED, not captured — the pre-split planner had no review stage. The "
            "coverage note and the improvements are computed by a set difference over "
            "requirement_ids: real, but mechanical, not a model review. open_questions is empty "
            "because judging what a PRD leaves undecided is not something arithmetic can do",
            "replaces": "re-run tools/make_demo_fixtures.py after any stage-schema change, or "
            "capture a live three-stage run to replace all three with genuine model output",
        },
    )
    print(f"\ndemo fixtures ready: {len(requirements)} requirements, {len(tasks)} tasks, "
          f"{len(improvements)} computed improvements")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
