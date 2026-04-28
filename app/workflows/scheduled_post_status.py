from __future__ import annotations

import time

from app.workflows.scheduled_posts import (
    ACTIVE_STATUSES,
    list_all_scheduled_posts,
    list_scheduled_posts,
)


def get_scheduled_post_status(
    customer_id: str | None = None,
    community_id: str | None = None,
    *,
    horizon_seconds: int | None = 7 * 24 * 3600,
) -> dict[str, object]:
    if customer_id and community_id:
        items = list_scheduled_posts(customer_id, community_id)
    else:
        items = list_all_scheduled_posts()
        if customer_id:
            items = [p for p in items if p.get("customer_id") == customer_id]

    counts: dict[str, int] = {}
    for entry in items:
        status = str(entry.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1

    now = time.time()
    upcoming = sorted(
        [p for p in items if p.get("status") in ACTIVE_STATUSES and float(p.get("send_at_epoch") or 0) >= now],
        key=lambda p: float(p.get("send_at_epoch") or 0),
    )
    if horizon_seconds is not None:
        cutoff = now + horizon_seconds
        upcoming = [p for p in upcoming if float(p.get("send_at_epoch") or 0) <= cutoff]

    recent = sorted(
        [p for p in items if p.get("status") in {"sent", "skipped", "cancelled"}],
        key=lambda p: float(p.get("updated_at_epoch") or 0),
        reverse=True,
    )

    return {
        "status": "ok",
        "counts": counts,
        "active_count": sum(counts.get(s, 0) for s in ACTIVE_STATUSES),
        "total_count": len(items),
        "upcoming": upcoming[:10],
        "recent": recent[:10],
    }
