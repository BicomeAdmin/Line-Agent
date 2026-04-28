from __future__ import annotations

from collections import Counter

from app.core.audit import read_recent_audit_events
from app.core.jobs import job_registry
from app.core.reviews import review_status_label, review_store
from app.storage.config_loader import load_all_communities, load_devices_config
from app.workflows.readiness_status import get_readiness_status
from app.workflows.system_status import get_system_status


def get_dashboard_status(audit_limit_per_customer: int = 20) -> dict[str, object]:
    system_status = get_system_status()
    readiness = get_readiness_status()
    devices = system_status.get("devices", []) if isinstance(system_status, dict) else []
    jobs = [job for job in job_registry.list_jobs() if _is_operational_job(job.job_type, job.payload)]
    audits = _collect_recent_audits(audit_limit_per_customer)
    all_reviews = review_store.list_all()
    pending_reviews = [_serialize_review(review) for review in review_store.list_pending()]
    review_status_counts = Counter(review.status for review in all_reviews)

    job_status_counts = Counter(job.status for job in jobs)
    job_type_counts = Counter(job.job_type for job in jobs)
    recent_send_attempts = [event for event in audits if event.get("event_type") == "send_attempt"]
    recent_patrol_events = [event for event in audits if "patrol" in str(event.get("event_type", ""))]
    recent_action_events = [event for event in audits if event.get("event_type") == "action_received"]
    active_devices = [device for device in devices if isinstance(device, dict) and device.get("enabled")]
    line_ready_devices = [
        device
        for device in active_devices
        if device.get("line_installed") and device.get("line_active")
    ]
    review_queue = {
        "count": len(pending_reviews),
        "needs_edit_count": sum(1 for review in pending_reviews if review["status"] == "edit_required"),
        "waiting_reapproval_count": sum(1 for review in pending_reviews if review["status"] == "pending_reapproval"),
        "fresh_pending_count": sum(1 for review in pending_reviews if review["status"] == "pending"),
        "items": pending_reviews[:10],
    }
    community_operations = _build_community_operations(audits)

    return {
        "status": "ok",
        "system": system_status,
        "readiness": {
            "summary": readiness.get("summary", {}),
            "next_actions": readiness.get("next_actions", []),
        },
        "operations": {
            "enabled_device_count": len(active_devices),
            "line_ready_device_count": len(line_ready_devices),
            "jobs": {
                "total": len(jobs),
                "by_status": dict(job_status_counts),
                "by_type": dict(job_type_counts),
            },
            "reviews": {
                "total": len(all_reviews),
                "open_count": review_queue["count"],
                "by_status": dict(review_status_counts),
            },
            "communities": community_operations["summary"],
        },
        "review_queue": review_queue,
        "community_operations": community_operations["items"],
        "history": {
            "send_attempts": recent_send_attempts[:10],
            "patrol_events": recent_patrol_events[:10],
            "action_events": recent_action_events[:10],
        },
    }


def _collect_recent_audits(limit_per_customer: int) -> list[dict[str, object]]:
    customer_ids = sorted({device.customer_id for device in load_devices_config()})
    events: list[dict[str, object]] = []
    for customer_id in customer_ids:
        for event in read_recent_audit_events(customer_id, limit=limit_per_customer):
            enriched = dict(event)
            enriched["customer_id"] = customer_id
            events.append(enriched)
    return sorted(events, key=lambda item: str(item.get("timestamp", "")), reverse=True)


def _is_operational_job(job_type: str, payload: dict[str, object]) -> bool:
    if job_type == "lark_command":
        return isinstance(payload.get("command"), dict)
    if job_type == "lark_action":
        return isinstance(payload.get("job_id"), str) and isinstance(payload.get("action"), str)
    if job_type == "scheduled_patrol":
        return all(isinstance(payload.get(key), str) and payload.get(key) for key in ("customer_id", "community_id", "device_id"))
    return False


def _serialize_review(review: object) -> dict[str, object]:
    return {
        "review_id": review.review_id,
        "source_job_id": review.source_job_id,
        "customer_id": review.customer_id,
        "customer_name": review.customer_name,
        "community_id": review.community_id,
        "community_name": review.community_name,
        "device_id": review.device_id,
        "draft_text": review.draft_text,
        "reason": review.reason,
        "confidence": review.confidence,
        "status": review.status,
        "status_label": review_status_label(review.status),
        "updated_at": review.updated_at,
    }


def _build_community_operations(audits: list[dict[str, object]]) -> dict[str, object]:
    communities = load_all_communities()
    items = []
    for community in communities:
        last_patrol = _latest_event_for_community(
            audits,
            community.customer_id,
            community.community_id,
            {"scheduled_patrol_processed", "community_patrol_skipped"},
        )
        last_send = _latest_event_for_community(audits, community.customer_id, community.community_id, {"send_attempt"})
        items.append(
            {
                "customer_id": community.customer_id,
                "community_id": community.community_id,
                "community_name": community.display_name,
                "device_id": community.device_id,
                "patrol_interval_minutes": community.patrol_interval_minutes,
                "enabled": community.enabled,
                "coordinates_ready": None not in (community.input_x, community.input_y, community.send_x, community.send_y),
                "coordinate_source": community.coordinate_source,
                "last_patrol_status": _status_from_event(last_patrol),
                "last_patrol_at": last_patrol.get("timestamp") if last_patrol else None,
                "last_send_status": _status_from_event(last_send),
                "last_send_at": last_send.get("timestamp") if last_send else None,
            }
        )

    return {
        "summary": {
            "total": len(items),
            "active_count": sum(1 for item in items if item["enabled"]),
            "recently_patrolled_count": sum(1 for item in items if item["last_patrol_at"]),
            "calibrated_count": sum(1 for item in items if item["coordinates_ready"]),
        },
        "items": items[:20],
    }


def _latest_event_for_community(
    audits: list[dict[str, object]],
    customer_id: str,
    community_id: str,
    event_types: set[str],
) -> dict[str, object] | None:
    for event in audits:
        if event.get("event_type") not in event_types:
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        if event.get("customer_id") != customer_id:
            continue
        if payload.get("community_id") != community_id:
            continue
        return event
    return None


def _status_from_event(event: dict[str, object] | None) -> str | None:
    if not event:
        return None
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    status = payload.get("status")
    return str(status) if isinstance(status, str) else None
