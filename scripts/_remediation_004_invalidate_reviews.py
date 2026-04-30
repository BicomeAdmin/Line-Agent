"""One-shot remediation: invalidate 004 pending reviews built on poisoned
operator_nickname='妍'. Writes an incident audit summary + per-review
invalidation events. See change-log entry for 2026-04-30."""
from __future__ import annotations

import time

import _bootstrap  # noqa: F401

from app.core.reviews import ReviewStore
from app.core.audit import append_audit_event


def main() -> int:
    store = ReviewStore()
    all_reviews = store.list_all()

    pending_004 = [
        r for r in all_reviews
        if r.community_id == "openchat_004" and r.status == "pending"
    ]
    sent_004 = [
        r for r in all_reviews
        if r.community_id == "openchat_004" and r.status == "sent"
    ]
    ignored_004 = [
        r for r in all_reviews
        if r.community_id == "openchat_004" and r.status == "ignored"
    ]

    print(
        f"openchat_004 review counts: "
        f"pending={len(pending_004)} sent={len(sent_004)} ignored={len(ignored_004)}"
    )

    incident_id = f"operator_nickname_correction_{int(time.time())}"
    append_audit_event(
        "customer_a",
        "incident_operator_nickname_correction",
        {
            "incident_id": incident_id,
            "community_id": "openchat_004",
            "old_nickname": "妍",
            "correct_nickname": "翊",
            "discovered_via": "operator dashboard inspection 2026-04-30",
            "verified_via": "LINE UI screenshot of group cover (showed 翊)",
            "blast_radius": {
                "fingerprints": "operator listed as ordinary member; distinct_senders=251 includes 翊 + 翊加入聊天",
                "lifecycle": "翊 listed as ordinary member at L726",
                "kpi_snapshots": "operator_nickname stored as 妍 (stale)",
                "review_store_pending": len(pending_004),
                "review_store_sent": len(sent_004),
                "review_store_ignored": len(ignored_004),
                "live_watch_path": "unaffected (uses is_self coordinate detection, not nickname)",
            },
            "remediation": (
                "pending reviews invalidated; derived data "
                "(fingerprints/lifecycle/kpi/relationship_graph) will be "
                "recomputed; sent reviews left in history with audit reference"
            ),
        },
    )
    print(f"audit incident logged: {incident_id}")

    n_invalidated = 0
    for r in pending_004:
        updated = store.update_status(
            r.review_id,
            status="ignored",
            updated_from_action="incident_operator_nickname_correction",
        )
        if updated:
            n_invalidated += 1
            append_audit_event(
                "customer_a",
                "review_invalidated_by_incident",
                {
                    "incident_id": incident_id,
                    "review_id": r.review_id,
                    "community_id": r.community_id,
                    "previous_status": "pending",
                    "new_status": "ignored",
                    "reason": (
                        "selector/fingerprint built on operator_nickname='妍' "
                        "which was wrong; operator's own past messages were "
                        "treated as KOC-member candidates"
                    ),
                },
            )
    print(f"invalidated {n_invalidated} pending reviews")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
