from __future__ import annotations

import argparse
import json
import time

import _bootstrap  # noqa: F401

from app.core.jobs import job_registry
from app.core.scheduler_state import scheduler_state
from app.workflows.scheduler import enqueue_due_patrols
from app.workflows.job_runner import ensure_job_worker


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait-seconds", type=float, default=3.0)
    args = parser.parse_args()

    ensure_job_worker()
    result = enqueue_due_patrols()
    job_ids = [item["job_id"] for item in result.get("enqueued", []) if isinstance(item, dict) and isinstance(item.get("job_id"), str)]
    if job_ids:
        deadline = time.time() + args.wait_seconds
        completed: list[dict[str, object]] = []
        while time.time() < deadline:
            completed = []
            for job_id in job_ids:
                job = job_registry.get(job_id)
                if job is None or job.status not in {"completed", "failed"}:
                    break
                completed.append({"job_id": job_id, "status": job.status, "result": job.result, "error": job.error})
            else:
                result["processed_jobs"] = completed
                result["scheduler_state"] = scheduler_state.snapshot()
                break
            time.sleep(0.1)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
