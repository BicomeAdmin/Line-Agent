from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from queue import Empty, Queue

from app.storage.paths import jobs_state_path


@dataclass
class JobRecord:
    job_id: str
    job_type: str
    payload: dict[str, object]
    status: str = "queued"
    result: dict[str, object] | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class JobRegistry:
    def __init__(self, state_path: Path | None = None, persist: bool = True) -> None:
        self._queue: Queue[str] = Queue()
        self._jobs: dict[str, JobRecord] = {}
        self._seen_event_ids: set[str] = set()
        self._lock = threading.Lock()
        self._persist_enabled = persist
        self._state_path = state_path or jobs_state_path()
        if self._persist_enabled:
            self._load_existing_jobs()

    def enqueue(self, job_type: str, payload: dict[str, object], event_id: str | None = None) -> JobRecord:
        with self._lock:
            if event_id and event_id in self._seen_event_ids:
                existing = self.find_by_event_id(event_id)
                if existing is not None:
                    return existing
            job_id = f"job-{uuid.uuid4().hex[:12]}"
            job = JobRecord(job_id=job_id, job_type=job_type, payload=payload)
            self._jobs[job_id] = job
            if event_id:
                self._seen_event_ids.add(event_id)
                job.payload["event_id"] = event_id
            self._persist_job(job)
            self._queue.put(job_id)
            return job

    def find_by_event_id(self, event_id: str) -> JobRecord | None:
        for job in self._jobs.values():
            if job.payload.get("event_id") == event_id:
                return job
        return None

    def pop(self, timeout_seconds: float = 0.5) -> JobRecord | None:
        try:
            job_id = self._queue.get(timeout=timeout_seconds)
        except Empty:
            return None
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            job.status = "running"
            job.updated_at = time.time()
            self._persist_job(job)
            return job

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            return self._jobs.get(job_id)

    def complete(self, job_id: str, result: dict[str, object]) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "completed"
            job.result = result
            job.error = None
            job.updated_at = time.time()
            self._persist_job(job)

    def fail(self, job_id: str, error_message: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "failed"
            job.error = error_message
            job.updated_at = time.time()
            self._persist_job(job)

    def list_jobs(self) -> list[JobRecord]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda item: item.created_at, reverse=True)

    def _persist_job(self, job: JobRecord) -> None:
        if not self._persist_enabled:
            return
        target = self._state_path
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(job.to_dict(), ensure_ascii=False) + "\n")

    def _load_existing_jobs(self) -> None:
        target = self._state_path
        if not target.exists():
            return

        latest_by_job_id: dict[str, JobRecord] = {}
        for raw_line in target.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            payload = json.loads(line)
            job = JobRecord(
                job_id=str(payload["job_id"]),
                job_type=str(payload["job_type"]),
                payload=dict(payload.get("payload", {})),
                status=str(payload.get("status", "queued")),
                result=dict(payload["result"]) if isinstance(payload.get("result"), dict) else None,
                error=str(payload["error"]) if isinstance(payload.get("error"), str) else None,
                created_at=float(payload.get("created_at", time.time())),
                updated_at=float(payload.get("updated_at", time.time())),
            )
            latest_by_job_id[job.job_id] = job
            event_id = job.payload.get("event_id")
            if isinstance(event_id, str) and event_id:
                self._seen_event_ids.add(event_id)

        self._jobs = latest_by_job_id
        for job in self._jobs.values():
            if job.status in {"queued", "running"}:
                job.status = "queued"
                job.updated_at = time.time()
                self._queue.put(job.job_id)


job_registry = JobRegistry()
