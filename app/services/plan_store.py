"""The L1 plan cache — in memory, keyed by intent.

There are two different questions a caller can ask about a finished plan, and they deserve two
different lookups:

* **"Have I planned this exact input before?"** — keyed by ``intent_key``: the document, the prompt
  versions, the model and the provider. Computable from the upload alone, *before* any model call,
  which is what keeps a re-upload instant. This is ``get``/``put`` and it counts towards hit rate.
* **"Give me plan 3f9c…"** — keyed by ``plan_id``, for ``/api/plans/{id}`` and the markdown
  download. This is ``peek`` and it must not count as a cache hit, or browsing history through the
  API would inflate the number the app reports about itself.

Before the pipeline split these were the same key, because the plan id *was* the input hash. They
had to separate: ``plan_id`` is now the terminal link of the stage chain and depends on bytes stage
1 has not produced yet, so it cannot be known in time to answer the first question.

``PlanStore`` stays an abstract port with one implementation. The brief said "for now, in memory" —
which implies that later it will not be, so the seam is drawn now: a ``RedisPlanStore`` is a new
class and one line in the wiring, with ``PlanningService`` untouched.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections import OrderedDict

from app.domain.models import PlanResponse


class PlanStore(ABC):
    @abstractmethod
    def get(self, key: str) -> PlanResponse | None:
        """Cache lookup by ``intent_key`` — counts towards hit/miss statistics."""

    @abstractmethod
    def peek(self, plan_id: str) -> PlanResponse | None:
        """Direct fetch by plan id — does NOT count as a cache hit."""

    @abstractmethod
    def put(self, plan: PlanResponse, key: str) -> None: ...

    @abstractmethod
    def list(self) -> list[PlanResponse]:
        """Newest first."""

    @abstractmethod
    def stats(self) -> dict[str, object]: ...


class InMemoryPlanStore(PlanStore):
    """Bounded LRU map guarded by a lock, plus a plan-id index onto the same objects.

    Bounded because an unbounded dict in a service that accepts uploads is a memory leak with a slow
    fuse. Locked because uvicorn runs request handlers concurrently and ``OrderedDict`` is not safe
    against interleaved ``move_to_end`` + ``popitem``. The index is evicted alongside the entry it
    points at, so the two maps cannot disagree about what is retained.
    """

    def __init__(self, capacity: int = 100) -> None:
        self._capacity = max(1, capacity)
        self._entries: OrderedDict[str, PlanResponse] = OrderedDict()
        self._by_plan_id: dict[str, PlanResponse] = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> PlanResponse | None:
        with self._lock:
            plan = self._entries.get(key)
            if plan is None:
                self._misses += 1
                return None
            self._entries.move_to_end(key)
            self._hits += 1
            return plan

    def peek(self, plan_id: str) -> PlanResponse | None:
        with self._lock:
            return self._by_plan_id.get(plan_id)

    def put(self, plan: PlanResponse, key: str) -> None:
        with self._lock:
            self._entries[key] = plan
            self._entries.move_to_end(key)
            self._by_plan_id[plan.plan_id] = plan
            while len(self._entries) > self._capacity:
                _, evicted = self._entries.popitem(last=False)
                # Only drop the index entry if it still points at the plan being evicted — a newer
                # entry under a different intent key may have claimed the same plan id.
                if self._by_plan_id.get(evicted.plan_id) is evicted:
                    del self._by_plan_id[evicted.plan_id]

    def list(self) -> list[PlanResponse]:
        with self._lock:
            return list(reversed(self._entries.values()))

    def stats(self) -> dict[str, object]:
        with self._lock:
            total = self._hits + self._misses
            return {
                "backend": "memory",
                "entries": len(self._entries),
                "capacity": self._capacity,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / total, 3) if total else 0.0,
            }


__all__ = ["InMemoryPlanStore", "PlanStore"]
