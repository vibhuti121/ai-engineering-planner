"""The plan store — which is also the cache.

Because ``cache_key.compute_plan_id`` derives the id from the input, "save this plan" and "have I
planned this before" are the same lookup in the same map. There is no second data structure and no
invalidation logic to get wrong.

``PlanStore`` is an abstract port with one implementation today. The brief for this build was
explicitly "for now, in memory" — which implies that later it will not be, so the seam is drawn
now: a ``RedisPlanStore`` or ``SqlitePlanStore`` is a new class and a one-line change in the
factory, with ``PlanningService`` untouched.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections import OrderedDict

from app.domain.models import PlanResponse


class PlanStore(ABC):
    @abstractmethod
    def get(self, plan_id: str) -> PlanResponse | None:
        """Cache lookup — counts towards hit/miss statistics."""

    @abstractmethod
    def peek(self, plan_id: str) -> PlanResponse | None:
        """Direct fetch by id — does NOT count as a cache hit.

        Separate from ``get`` so that browsing history through the API cannot inflate the hit rate
        the cache reports.
        """

    @abstractmethod
    def put(self, plan: PlanResponse) -> None: ...

    @abstractmethod
    def list(self) -> list[PlanResponse]:
        """Newest first."""

    @abstractmethod
    def stats(self) -> dict[str, object]: ...


class InMemoryPlanStore(PlanStore):
    """Bounded LRU map guarded by a lock.

    Bounded because an unbounded dict in a service that accepts uploads is a memory leak with a
    slow fuse. Locked because uvicorn runs request handlers concurrently and ``OrderedDict`` is not
    safe against interleaved ``move_to_end`` + ``popitem``.
    """

    def __init__(self, capacity: int = 100) -> None:
        self._capacity = max(1, capacity)
        self._entries: OrderedDict[str, PlanResponse] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, plan_id: str) -> PlanResponse | None:
        with self._lock:
            plan = self._entries.get(plan_id)
            if plan is None:
                self._misses += 1
                return None
            self._entries.move_to_end(plan_id)
            self._hits += 1
            return plan

    def peek(self, plan_id: str) -> PlanResponse | None:
        with self._lock:
            return self._entries.get(plan_id)

    def put(self, plan: PlanResponse) -> None:
        with self._lock:
            self._entries[plan.plan_id] = plan
            self._entries.move_to_end(plan.plan_id)
            while len(self._entries) > self._capacity:
                self._entries.popitem(last=False)

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
