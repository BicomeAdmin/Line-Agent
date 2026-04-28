from __future__ import annotations

from collections import Counter

from app.core.reviews import review_status_label, review_store


def get_review_status() -> dict[str, object]:
    reviews = review_store.list_all()
    pending = review_store.list_pending()
    return {
        "status": "ok",
        "total": len(reviews),
        "by_status": dict(Counter(review.status for review in reviews)),
        "pending_count": len(pending),
        "pending_items": [
            {
                "review_id": review.review_id,
                "source_job_id": review.source_job_id,
                "customer_id": review.customer_id,
                "customer_name": review.customer_name,
                "community_id": review.community_id,
                "community_name": review.community_name,
                "device_id": review.device_id,
                "draft_text": review.draft_text,
                "status": review.status,
                "status_label": review_status_label(review.status),
                "updated_at": review.updated_at,
            }
            for review in pending[:20]
        ],
        "pending_breakdown": {
            "fresh_pending": sum(1 for review in pending if review.status == "pending"),
            "needs_edit": sum(1 for review in pending if review.status == "edit_required"),
            "waiting_reapproval": sum(1 for review in pending if review.status == "pending_reapproval"),
        },
    }
