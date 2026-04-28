"""Send-pipeline metrics — operator-facing observability.

Reads `audit.jsonl` + review_store + scheduled_posts to answer:

- How many drafts has the system composed in the last N hours?
- Of those, how many got sent / ignored / are still pending?
- What's the breakdown by trigger source (operator request vs auto-watch vs scheduled)?
- What's the latency (compose → approve → physical send)?
- Recent auto-fires: when, in which community, what content, what outcome.

Output is structured dict the LLM brain can summarize back to the operator,
or a CLI can pretty-print.

Reads only — no side effects.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from typing import Iterator

from app.core.audit import read_all_audit_events
from app.core.reviews import review_store
from app.core.timezone import to_taipei_str
from app.storage.config_loader import load_all_communities


# Events relevant to the send pipeline.
DRAFT_EVENTS = {"mcp_compose_review_created", "scheduled_post_added"}
APPROVAL_EVENTS = {"human_approved_send_started"}
SEND_EVENTS = {"send_attempt"}
REVIEW_EVENTS = {"review_status_changed"}
WATCH_EVENTS = {"watch_started", "watch_stopped", "watch_tick_fired", "watch_tick_error"}


def get_send_metrics(
    customer_id: str,
    *,
    since_hours: float | None = 24.0,
    community_id: str | None = None,
) -> dict[str, object]:
    """Aggregate the last N hours of send-pipeline activity."""

    now = time.time()
    cutoff = (now - since_hours * 3600) if since_hours else 0.0

    events = list(_iter_events(customer_id, cutoff=cutoff))

    # Index reviews so we can resolve final status per review_id.
    reviews_by_id = {r.review_id: r for r in review_store.list_all()}

    by_community = _aggregate_by_community(events, reviews_by_id, community_id_filter=community_id)
    auto_fires = _collect_auto_fires(events, reviews_by_id)
    totals = _sum_totals(by_community)

    from datetime import datetime, timezone

    return {
        "status": "ok",
        "window": {
            "since_hours": since_hours,
            "since_taipei": to_taipei_str(datetime.fromtimestamp(cutoff, timezone.utc)) if since_hours else "all",
            "until_taipei": to_taipei_str(datetime.fromtimestamp(now, timezone.utc)),
        },
        "totals": totals,
        "by_community": by_community,
        "auto_fires": auto_fires,
    }


def _iter_events(customer_id: str, *, cutoff: float = 0.0) -> Iterator[dict[str, object]]:
    for event in read_all_audit_events(customer_id):
        ts = _event_epoch(event)
        if ts is None or ts < cutoff:
            continue
        yield event


def _event_epoch(event: dict[str, object]) -> float | None:
    raw = event.get("timestamp")
    if not isinstance(raw, str):
        return None
    try:
        from datetime import datetime, timezone

        text = raw.replace("Z", "+00:00")
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _aggregate_by_community(
    events: list[dict[str, object]],
    reviews_by_id: dict,
    *,
    community_id_filter: str | None,
) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = defaultdict(lambda: {
        "drafts_created": 0,
        "by_source": defaultdict(int),
        "sent": 0,
        "ignored": 0,
        "review_pending": 0,
        "send_attempts": [],
        "compose_to_send_seconds": [],
    })
    # Map review_id → compose epoch for latency.
    compose_epoch_by_id: dict[str, float] = {}

    for event in events:
        et = event.get("event_type")
        payload = event.get("payload") or {}
        cid = payload.get("community_id")
        if community_id_filter and cid != community_id_filter:
            continue
        if not isinstance(cid, str):
            continue
        ts = _event_epoch(event) or 0.0
        bucket = out[cid]

        if et == "mcp_compose_review_created":
            bucket["drafts_created"] += 1
            source = str(payload.get("source") or "operator")
            bucket["by_source"][source] += 1
            rid = payload.get("review_id")
            if isinstance(rid, str):
                compose_epoch_by_id[rid] = ts
        elif et == "scheduled_post_added":
            bucket["drafts_created"] += 1
            bucket["by_source"]["scheduled_post"] += 1
        elif et == "send_attempt":
            status = str(payload.get("status") or "")
            bucket["send_attempts"].append({
                "ts_taipei": to_taipei_str(event.get("timestamp")),
                "status": status,
                "delay_seconds": payload.get("delay_seconds"),
            })
            if status == "sent":
                bucket["sent"] += 1
        elif et == "review_status_changed":
            new_status = payload.get("status")
            rid = payload.get("review_id")
            if new_status == "ignored":
                bucket["ignored"] += 1
            if isinstance(rid, str) and rid in compose_epoch_by_id and new_status == "sent":
                bucket["compose_to_send_seconds"].append(ts - compose_epoch_by_id[rid])

    # Count current pending reviews per community.
    for rid, record in reviews_by_id.items():
        if community_id_filter and record.community_id != community_id_filter:
            continue
        if record.status in ("pending", "edit_required", "pending_reapproval"):
            bucket = out[record.community_id]
            bucket["review_pending"] += 1

    # Decorate with display name + averages, finalize defaultdicts to plain dicts.
    name_lookup = {c.community_id: c.display_name for c in load_all_communities()}
    finalized: dict[str, dict[str, object]] = {}
    for cid, bucket in out.items():
        latencies = bucket["compose_to_send_seconds"]
        avg = sum(latencies) / len(latencies) if latencies else None
        finalized[cid] = {
            "community_name": name_lookup.get(cid, cid),
            "drafts_created": bucket["drafts_created"],
            "by_source": dict(bucket["by_source"]),
            "sent": bucket["sent"],
            "ignored": bucket["ignored"],
            "review_pending": bucket["review_pending"],
            "send_attempts": bucket["send_attempts"][-10:],  # most recent 10
            "avg_compose_to_send_seconds": round(avg, 1) if avg is not None else None,
            "compose_to_send_count": len(latencies),
        }
    return finalized


def _collect_auto_fires(
    events: list[dict[str, object]],
    reviews_by_id: dict,
) -> list[dict[str, object]]:
    fires: list[dict[str, object]] = []
    name_lookup = {c.community_id: c.display_name for c in load_all_communities()}
    for event in events:
        if event.get("event_type") != "watch_tick_fired":
            continue
        payload = event.get("payload") or {}
        cid = payload.get("community_id")
        watch_id = payload.get("watch_id")
        summary = str(payload.get("codex_summary") or "")[:200]
        ts_taipei = to_taipei_str(event.get("timestamp"))
        # Try to find a corresponding review created shortly after.
        related = _find_related_review(events, event, reviews_by_id)
        fires.append({
            "fired_at_taipei": ts_taipei,
            "community_id": cid,
            "community_name": name_lookup.get(cid, cid),
            "watch_id": watch_id,
            "codex_summary": summary,
            "related_review": related,
        })
    return fires[-30:]  # most recent 30


def _find_related_review(
    events: list[dict[str, object]],
    fire_event: dict[str, object],
    reviews_by_id: dict,
) -> dict[str, object] | None:
    """Heuristic: find an mcp_compose_review_created within 10 seconds of the fire event."""

    fire_ts = _event_epoch(fire_event) or 0
    fire_payload = fire_event.get("payload") or {}
    fire_cid = fire_payload.get("community_id")

    for event in events:
        if event.get("event_type") != "mcp_compose_review_created":
            continue
        payload = event.get("payload") or {}
        if payload.get("community_id") != fire_cid:
            continue
        ts = _event_epoch(event) or 0
        if abs(ts - fire_ts) > 10:
            continue
        rid = payload.get("review_id")
        record = reviews_by_id.get(rid) if isinstance(rid, str) else None
        return {
            "review_id": rid,
            "text_preview": payload.get("text_preview"),
            "current_status": record.status if record else None,
        }
    return None


def _sum_totals(by_community: dict[str, dict[str, object]]) -> dict[str, object]:
    totals = {
        "drafts_created": 0,
        "sent": 0,
        "ignored": 0,
        "review_pending": 0,
        "by_source": defaultdict(int),
    }
    for bucket in by_community.values():
        totals["drafts_created"] += bucket.get("drafts_created", 0)
        totals["sent"] += bucket.get("sent", 0)
        totals["ignored"] += bucket.get("ignored", 0)
        totals["review_pending"] += bucket.get("review_pending", 0)
        for src, count in (bucket.get("by_source") or {}).items():
            totals["by_source"][src] += count
    totals["by_source"] = dict(totals["by_source"])
    return totals
