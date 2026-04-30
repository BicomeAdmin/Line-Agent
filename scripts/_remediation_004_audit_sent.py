"""Followup: 004 had 6 sent + 3 ignored reviews built on poisoned
operator_nickname='妍'. Pending count was 0 by the time invalidation ran
(daemon had already cycled them into sent/ignored). Sent reviews are
already on LINE — can't unsend. Write per-review audit entries marking
them as potentially-poisoned for historical traceability."""
from __future__ import annotations

import time

import _bootstrap  # noqa: F401

from app.core.reviews import ReviewStore
from app.core.audit import append_audit_event


def main() -> int:
    store = ReviewStore()
    all_reviews = store.list_all()

    sent_004 = [r for r in all_reviews if r.community_id == "openchat_004" and r.status == "sent"]
    ignored_004 = [r for r in all_reviews if r.community_id == "openchat_004" and r.status == "ignored"]

    print(f"sent={len(sent_004)} ignored={len(ignored_004)}")

    # Reuse incident_id format — generate a tracking id for this followup pass
    tracking_id = f"operator_nickname_correction_followup_{int(time.time())}"
    append_audit_event(
        "customer_a",
        "incident_followup_audit_sent_reviews",
        {
            "tracking_id": tracking_id,
            "community_id": "openchat_004",
            "purpose": "tag historical sent/ignored reviews as built on poisoned operator_nickname='妍'",
            "sent_count": len(sent_004),
            "ignored_count": len(ignored_004),
            "note": "sent messages are already in LINE community — cannot unsend. Operator should review them retrospectively for any drafts that referenced operator's own past words.",
        },
    )

    for r in sent_004:
        append_audit_event(
            "customer_a",
            "review_potentially_poisoned_already_sent",
            {
                "tracking_id": tracking_id,
                "review_id": r.review_id,
                "community_id": r.community_id,
                "draft_text_preview": (r.draft_text or "")[:120],
                "sent_at_epoch": r.updated_at,
                "note": "selector/fingerprint built on operator_nickname='妍'; verify post-hoc whether draft echoed operator's own past chat",
            },
        )
    for r in ignored_004:
        append_audit_event(
            "customer_a",
            "review_ignored_under_poisoned_state",
            {
                "tracking_id": tracking_id,
                "review_id": r.review_id,
                "community_id": r.community_id,
                "note": "no action needed; left as ignored",
            },
        )
    print(f"audited {len(sent_004)} sent + {len(ignored_004)} ignored under tracking_id={tracking_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
