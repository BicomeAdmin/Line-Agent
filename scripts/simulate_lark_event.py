from __future__ import annotations

import argparse
import json
import time
from uuid import uuid4

import _bootstrap  # noqa: F401

from app.core.jobs import job_registry
from app.lark.events import enqueue_lark_event
from app.workflows.job_runner import ensure_job_worker


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("text")
    parser.add_argument("--chat-id", default="oc_demo_chat")
    parser.add_argument("--event-id", default=f"evt-demo-{uuid4().hex[:12]}")
    parser.add_argument("--wait-seconds", type=float, default=3.0)
    args = parser.parse_args()

    ensure_job_worker()
    payload = {
        "type": "event_callback",
        "header": {"event_id": args.event_id},
        "event": {
            "message": {
                "chat_id": args.chat_id,
                "content": json.dumps({"text": args.text}, ensure_ascii=False),
            }
        },
    }
    response = enqueue_lark_event(payload)
    job_id = response.get("job_id")
    if not isinstance(job_id, str):
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return 1

    deadline = time.time() + args.wait_seconds
    while time.time() < deadline:
        job = job_registry.get(job_id)
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
