"""Operator-initiated unapprove / recall of a review.

Two cases the operator may want to undo:

1. **Pre-send regret (active review)** — operator hit 通過 then realized the
   draft is wrong, OR an `edit_required` / `pending_reapproval` was edited but
   shouldn't have been queued at all. The send hasn't happened yet (or the
   approve job hasn't run); cancelling is straightforward — mark the review
   `recalled` and the queued job (if any) will see the terminal status and
   bail out.

2. **Post-send regret (sent)** — operator hit 通過, message went out, then
   they wished it hadn't. We CANNOT actually un-send via LINE API. What we
   CAN do is leave an unambiguous audit trail (`review_unapproved` with
   `previous_status="sent"`) so the operator's regret is recorded — and
   surface the recalled draft in dashboards so they remember to follow up
   with a correction in the room (which is a manual UI action, not
   automated).

Both paths produce a `recalled` terminal status. There is no "revert to
pending" path: if the operator wants to retry composing, they should run
compose_and_send fresh — recalling the old review and immediately re-queuing
the same draft gains nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.audit import append_audit_event
from app.core.reviews import (
    ACTIVE_REVIEW_STATUSES,
    TERMINAL_REVIEW_STATUSES,
    ReviewRecord,
    review_store,
)


class UnapproveError(RuntimeError):
    """Operator action could not be applied to the given review."""


@dataclass(frozen=True)
class UnapproveResult:
    review_id: str
    previous_status: str
    new_status: str
    sent_message_irreversible: bool
    reason: str | None


def unapprove_review(
    review_id: str,
    *,
    reason: str | None = None,
    store=review_store,
) -> UnapproveResult:
    review: ReviewRecord | None = store.get(review_id)
    if review is None:
        raise UnapproveError(f"review not found: {review_id}")

    previous = review.status
    if previous == "recalled":
        raise UnapproveError(f"review already recalled: {review_id}")
    if previous in TERMINAL_REVIEW_STATUSES and previous not in {"sent"}:
        # ignored is also terminal; nothing to undo, no draft was sent
        raise UnapproveError(
            f"review in terminal state {previous!r}, nothing to recall: {review_id}"
        )
    if previous not in ACTIVE_REVIEW_STATUSES and previous != "sent":
        raise UnapproveError(f"unexpected status {previous!r} for review {review_id}")

    sent_irreversible = previous == "sent"
    store.update_status(review_id, "recalled", "operator_unapprove")
    append_audit_event(
        review.customer_id,
        "review_unapproved",
        {
            "review_id": review_id,
            "community_id": review.community_id,
            "previous_status": previous,
            "new_status": "recalled",
            "sent_message_irreversible": sent_irreversible,
            "reason": reason,
            "draft_text_preview": review.draft_text[:120],
        },
    )

    return UnapproveResult(
        review_id=review_id,
        previous_status=previous,
        new_status="recalled",
        sent_message_irreversible=sent_irreversible,
        reason=reason,
    )
