"""Daemon-startup orphan-state recovery.

Goal: when scheduler_daemon restarts after a crash / hard kill, the
state files may contain entries that were mid-flight when the process
died. Without recovery, these silently rot:

  - scheduled_posts in `due` status (we marked it due, then crashed
    before the job processor pushed a review). Stays `due` forever
    because find_due_posts only picks `scheduled`.
  - scheduled_posts in `reviewing` status with no matching ReviewRecord
    (processor crashed mid-handler, after marking but before upsert).
  - reviews in `pending` status with no recent operator activity for
    so long that they're effectively abandoned (Lark push failed, op
    never saw them, etc.).

This module is read-mostly; it edits state only when an orphan is
clearly stale (well past any reasonable in-flight duration) AND the
fix is reversible (post → back to `scheduled`, review → audited but
not auto-resolved).

Called once at scheduler_daemon startup, before the main loop.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.core.audit import append_audit_event
from app.core.reviews import review_store
from app.workflows.scheduled_posts import (
    _update_post,
    list_all_scheduled_posts,
)


# Posts in `due` for longer than this are very likely orphaned (a
# normal due → reviewing/sent transition takes seconds, not minutes).
DUE_ORPHAN_GRACE_SECONDS = 5 * 60          # 5 minutes
# Posts in `reviewing` for longer than this with no matching review
# record are clearly orphaned.
REVIEWING_ORPHAN_GRACE_SECONDS = 30 * 60   # 30 minutes
# Reviews in `pending` for this long get audited as stale (operator
# may have missed the Lark push). We do NOT auto-skip — operator
# might still come back to them.
STALE_PENDING_REVIEW_HOURS = 24


@dataclass
class RecoverySummary:
    due_orphans_reset: int = 0
    reviewing_orphans_marked: int = 0
    stale_pending_reviews: int = 0
    errors: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []

    def to_dict(self) -> dict:
        return {
            "due_orphans_reset": self.due_orphans_reset,
            "reviewing_orphans_marked": self.reviewing_orphans_marked,
            "stale_pending_reviews": self.stale_pending_reviews,
            "errors": self.errors,
        }


def recover_orphan_state(*, now: float | None = None) -> RecoverySummary:
    """Scan and best-effort fix orphaned state. Idempotent — safe to
    call repeatedly. Each fix writes its own audit event so operators
    can see what was reset.
    """

    summary = RecoverySummary()
    current = now if now is not None else time.time()

    try:
        all_posts = list_all_scheduled_posts()
    except Exception as exc:  # noqa: BLE001
        summary.errors.append(f"list_scheduled_posts_failed:{exc}")
        return summary

    for post in all_posts:
        status = str(post.get("status") or "")
        post_id = str(post.get("post_id") or "")
        customer_id = str(post.get("customer_id") or "")
        community_id = str(post.get("community_id") or "")
        if not (post_id and customer_id and community_id):
            continue
        updated_at = float(post.get("updated_at_epoch") or 0)
        age = current - updated_at if updated_at > 0 else 0

        if status == "due" and age > DUE_ORPHAN_GRACE_SECONDS:
            try:
                _reset_due_to_scheduled(customer_id, community_id, post_id)
                append_audit_event(
                    customer_id,
                    "orphan_recovery_post_reset_to_scheduled",
                    {
                        "post_id": post_id,
                        "community_id": community_id,
                        "stale_seconds": int(age),
                    },
                )
                summary.due_orphans_reset += 1
            except Exception as exc:  # noqa: BLE001
                summary.errors.append(f"reset_due_failed:{post_id}:{exc}")

        elif status == "reviewing" and age > REVIEWING_ORPHAN_GRACE_SECONDS:
            review_id = post.get("review_id")
            review_present = (
                isinstance(review_id, str)
                and review_id
                and review_store.get(review_id) is not None
            )
            if not review_present:
                try:
                    _mark_reviewing_orphan_skipped(customer_id, community_id, post_id)
                    append_audit_event(
                        customer_id,
                        "orphan_recovery_post_reviewing_marked_skipped",
                        {
                            "post_id": post_id,
                            "community_id": community_id,
                            "stale_seconds": int(age),
                            "missing_review_id": review_id,
                        },
                    )
                    summary.reviewing_orphans_marked += 1
                except Exception as exc:  # noqa: BLE001
                    summary.errors.append(f"mark_reviewing_orphan_failed:{post_id}:{exc}")

    # Stale pending reviews — audit-only signal, not auto-resolve.
    cutoff = current - STALE_PENDING_REVIEW_HOURS * 3600
    try:
        all_reviews = review_store.list_all()
    except Exception as exc:  # noqa: BLE001
        summary.errors.append(f"list_reviews_failed:{exc}")
        all_reviews = []

    for record in all_reviews:
        if record.status != "pending":
            continue
        if record.updated_at <= 0 or record.updated_at >= cutoff:
            continue
        append_audit_event(
            record.customer_id,
            "orphan_recovery_stale_pending_review",
            {
                "review_id": record.review_id,
                "community_id": record.community_id,
                "age_hours": round((current - record.updated_at) / 3600, 1),
            },
        )
        summary.stale_pending_reviews += 1

    return summary


def _reset_due_to_scheduled(customer_id: str, community_id: str, post_id: str) -> None:
    def _apply(entry: dict) -> None:
        if entry.get("status") == "due":
            entry["status"] = "scheduled"
            entry["job_id"] = None  # the original job is gone
    _update_post(customer_id, community_id, post_id, _apply)


def _mark_reviewing_orphan_skipped(customer_id: str, community_id: str, post_id: str) -> None:
    def _apply(entry: dict) -> None:
        if entry.get("status") == "reviewing":
            entry["status"] = "skipped"
            entry["skip_reason"] = "orphaned_no_review_record"
    _update_post(customer_id, community_id, post_id, _apply)
