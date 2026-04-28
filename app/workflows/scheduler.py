from __future__ import annotations

import time

from app.core.audit import append_audit_event
from app.core.jobs import job_registry
from app.core.scheduler_state import scheduler_state
from app.storage.config_loader import load_all_communities, load_community_config
from app.workflows.scheduled_posts import find_due_posts, mark_post_due


def enqueue_due_patrols(now: float | None = None) -> dict[str, object]:
    current_time = now or time.time()
    enqueued: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []

    for community in load_all_communities():
        interval_seconds = community.patrol_interval_minutes * 60
        community_key = f"{community.customer_id}:{community.community_id}"
        last_enqueued = scheduler_state.last_enqueued(community_key)
        if last_enqueued is not None and current_time - last_enqueued < interval_seconds:
            skipped.append(
                {
                    "community_id": community.community_id,
                    "customer_id": community.customer_id,
                    "reason": "interval_not_reached",
                }
            )
            continue

        job = job_registry.enqueue(
            "scheduled_patrol",
            {
                "customer_id": community.customer_id,
                "community_id": community.community_id,
                "device_id": community.device_id,
            },
        )
        scheduler_state.mark_enqueued(community_key, at=current_time)
        item = {
            "job_id": job.job_id,
            "community_id": community.community_id,
            "customer_id": community.customer_id,
            "device_id": community.device_id,
        }
        enqueued.append(item)
        append_audit_event(community.customer_id, "scheduled_patrol_enqueued", item)

    return {
        "status": "ok",
        "enqueued_count": len(enqueued),
        "skipped_count": len(skipped),
        "enqueued": enqueued,
        "skipped": skipped,
        "scheduler_state": scheduler_state.snapshot(),
    }


def enqueue_due_scheduled_posts(now: float | None = None) -> dict[str, object]:
    """Pick up any scheduled posts whose send_at has passed and queue them as jobs.

    Each due post becomes a `scheduled_post` job. Marking the post as `due` on
    enqueue ensures we don't re-queue it on the next tick.
    """

    current_time = now if now is not None else time.time()
    enqueued: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []

    for post in find_due_posts(now=current_time):
        customer_id = str(post.get("customer_id") or "")
        community_id = str(post.get("community_id") or "")
        post_id = str(post.get("post_id") or "")
        if not (customer_id and community_id and post_id):
            skipped.append({"post_id": post_id, "reason": "missing_identifiers"})
            continue
        try:
            community = load_community_config(customer_id, community_id)
        except Exception as exc:  # noqa: BLE001
            skipped.append({"post_id": post_id, "reason": f"community_lookup_failed:{exc}"})
            continue

        job = job_registry.enqueue(
            "scheduled_post",
            {
                "customer_id": customer_id,
                "community_id": community_id,
                "device_id": community.device_id,
                "post_id": post_id,
                "draft_text": post.get("text"),
                "pre_approved": bool(post.get("pre_approved")),
            },
        )
        # Stamp the assigned job_id back into the payload so the processor builds the
        # review card / marks the post under the same review_id that _sync_review_state uses.
        job.payload["job_id"] = job.job_id
        mark_post_due(customer_id, community_id, post_id, job_id=job.job_id)
        item = {
            "job_id": job.job_id,
            "post_id": post_id,
            "customer_id": customer_id,
            "community_id": community_id,
            "device_id": community.device_id,
            "send_at": post.get("send_at_iso"),
        }
        enqueued.append(item)
        append_audit_event(customer_id, "scheduled_post_enqueued", item)

    return {
        "status": "ok",
        "enqueued_count": len(enqueued),
        "skipped_count": len(skipped),
        "enqueued": enqueued,
        "skipped": skipped,
    }


def tick_watches(now: float | None = None) -> dict[str, object]:
    """Watcher Phase 2 — invoked once per scheduler cycle by scheduler_daemon.

    Cheap to call when no watches are active; expensive (spawns codex) only
    when a watch's poll interval has elapsed AND new content is detected.
    """

    from app.workflows.watch_tick import tick_all_watches

    try:
        return tick_all_watches()
    except Exception as exc:  # noqa: BLE001 — never let the daemon die from a watch error
        return {"status": "error", "detail": repr(exc), "fired": [], "skipped": []}
