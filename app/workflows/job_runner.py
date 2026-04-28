from __future__ import annotations

import threading

from app.core.jobs import job_registry
from app.workflows.job_processor import notify_lark_error, process_job, _notify_lark


_worker_started = False
_worker_lock = threading.Lock()


def ensure_job_worker() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        worker = threading.Thread(target=_worker_loop, name="project-echo-job-worker", daemon=True)
        worker.start()
        _worker_started = True


def _worker_loop() -> None:
    while True:
        job = job_registry.pop(timeout_seconds=0.5)
        if job is None:
            continue
        try:
            result = process_job(job)
        except Exception as exc:  # pragma: no cover - defensive worker boundary
            job_registry.fail(job.job_id, str(exc))
            notify_lark_error(job, str(exc))
            continue
        job_registry.complete(job.job_id, result)
        _notify_lark(job, result)
