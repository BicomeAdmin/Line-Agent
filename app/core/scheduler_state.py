from __future__ import annotations

import threading
import time


class SchedulerState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_patrol_enqueued: dict[str, float] = {}
        self._last_patrol_completed: dict[str, float] = {}

    def mark_enqueued(self, community_key: str, at: float | None = None) -> None:
        with self._lock:
            self._last_patrol_enqueued[community_key] = at or time.time()

    def mark_completed(self, community_key: str, at: float | None = None) -> None:
        with self._lock:
            self._last_patrol_completed[community_key] = at or time.time()

    def last_enqueued(self, community_key: str) -> float | None:
        with self._lock:
            return self._last_patrol_enqueued.get(community_key)

    def last_completed(self, community_key: str) -> float | None:
        with self._lock:
            return self._last_patrol_completed.get(community_key)

    def snapshot(self) -> dict[str, dict[str, float]]:
        with self._lock:
            return {
                "last_patrol_enqueued": dict(self._last_patrol_enqueued),
                "last_patrol_completed": dict(self._last_patrol_completed),
            }


scheduler_state = SchedulerState()
