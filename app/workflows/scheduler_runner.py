from __future__ import annotations

import threading
import time

from app.workflows.scheduler import enqueue_due_patrols


_scheduler_started = False
_scheduler_lock = threading.Lock()


def ensure_scheduler_runner(interval_seconds: int = 60) -> None:
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        worker = threading.Thread(
            target=_scheduler_loop,
            args=(interval_seconds,),
            name="project-echo-scheduler",
            daemon=True,
        )
        worker.start()
        _scheduler_started = True


def _scheduler_loop(interval_seconds: int) -> None:
    while True:
        enqueue_due_patrols()
        time.sleep(interval_seconds)
