"""Making a three-minute request observable.

A cache miss calls the model and takes minutes. uvicorn logs a request only when it *completes*, so
without this module the terminal is silent for the entire slow step and the browser shows one static
line — indistinguishable from a hang.

Two sinks, one event. `configure_logging` handles the terminal; `ProgressTracker` holds the last
reported stage per upload so the browser can poll it. Both are fed by the same stage callback, so
what the UI shows and what the log says cannot drift apart.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict

TRACKER_CAPACITY = 50

# The closed vocabulary of stage names, in the order they occur. Both the log and the browser
# checklist render against this list, and a test asserts `planning_service` emits nothing outside it.
#
# That test exists because of a real bug: the service used to emit `model-returned`, which the UI's
# step list did not contain, so `indexOf` returned -1 and the checklist blanked every step for that
# instant — during the slowest part of the run, which is exactly when somebody is watching it.
# A name only has to be agreed in one place now.
STAGE_SEQUENCE: tuple[str, ...] = (
    "validating",
    "cache-lookup",
    "reading",
    "graphing",
    "ordering",
    "verifying",
    "rendering",
    "stored",
)

# Ordering runs *before* verifying: the reviewer reads the computed sequence, so it cannot run
# until `TaskOrderer` has produced one. The stage *id* stays `verifying` — it is the wire name
# shared with `models.json`, the artifact folder and the `data-stage` attribute; only the label
# below moved when the stage stopped grading and started advising.

STAGE_LABELS: dict[str, str] = {
    "validating": "Validating the PDF",
    "cache-lookup": "Checking the cache",
    "reading": "① Reading the PRD",
    "graphing": "② Building the task graph",
    "ordering": "Ordering (topological sort)",
    "verifying": "③ Reviewing the plan",
    "rendering": "Rendering markdown",
    "stored": "Stored",
}


def configure_logging(level: int = logging.INFO) -> None:
    """Compact single-line format, so stage lines read cleanly beside uvicorn's own output."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-5s %(name)-8s %(message)s",
        datefmt="%H:%M:%S",
    )


class ProgressTracker:
    """The stage each in-flight upload is currently in, keyed by a client-generated trace id.

    Bounded and lock-guarded for the same reason `InMemoryPlanStore` is: this is fed by an endpoint
    that accepts uploads, and an unbounded dict behind one is a memory leak.
    """

    def __init__(self, capacity: int = TRACKER_CAPACITY) -> None:
        self._capacity = capacity
        self._lock = threading.Lock()
        self._traces: OrderedDict[str, dict] = OrderedDict()

    def start(self, trace_id: str) -> None:
        with self._lock:
            self._traces[trace_id] = {
                "stage": "received",
                "detail": "upload received",
                "started": time.monotonic(),
                "done": False,
                "error": None,
            }
            self._traces.move_to_end(trace_id)
            while len(self._traces) > self._capacity:
                self._traces.popitem(last=False)

    def stage(self, trace_id: str, stage: str, detail: str) -> None:
        with self._lock:
            record = self._traces.get(trace_id)
            if record is None:
                return
            record["stage"] = stage
            record["detail"] = detail

    def finish(self, trace_id: str, error: str | None = None) -> None:
        with self._lock:
            record = self._traces.get(trace_id)
            if record is None:
                return
            record["done"] = True
            record["error"] = error
            if error:
                record["stage"] = "failed"
                record["detail"] = error

    def get(self, trace_id: str) -> dict | None:
        with self._lock:
            record = self._traces.get(trace_id)
            if record is None:
                return None
            return {
                "trace_id": trace_id,
                "stage": record["stage"],
                "detail": record["detail"],
                "elapsed_s": round(time.monotonic() - record["started"], 1),
                "done": record["done"],
                "error": record["error"],
            }


__all__ = ["STAGE_LABELS", "STAGE_SEQUENCE", "ProgressTracker", "configure_logging"]
