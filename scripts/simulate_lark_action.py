from __future__ import annotations

import argparse
import json
import time

import _bootstrap  # noqa: F401

from app.core.jobs import job_registry
from app.lark.events import enqueue_lark_action
from app.workflows.job_runner import ensure_job_worker


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    parser.add_argument("action")
    parser.add_argument("--draft-text", default=None)
    parser.add_argument("--wait-seconds", type=float, default=3.0)
    args = parser.parse_args()

    ensure_job_worker()
    value = {"job_id": args.job_id, "action": args.action}
    if args.draft_text:
        value["edited_draft_text"] = args.draft_text
    payload = {"action": {"value": value}}
    response = enqueue_lark_action(payload)
    action_job_id = response.get("job_id")
    if not isinstance(action_job_id, str):
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return 1

    deadline = time.time() + args.wait_seconds
    while time.time() < deadline:
        job = job_registry.get(action_job_id)
        if job is not None and job.status in {"completed", "failed"}:
            print(
                json.dumps(
                    {
                        "enqueue_response": response,
                        "job_status": job.status,
                        "result": job.result,
                        "error": job.error,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0 if job.status == "completed" else 2
        time.sleep(0.1)

    print(json.dumps({"enqueue_response": response, "job_status": "timeout"}, ensure_ascii=False, indent=2))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
