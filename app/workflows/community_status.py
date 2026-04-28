from __future__ import annotations

from app.ai.context_bundle import load_context_bundle
from app.core.audit import read_recent_audit_events
from app.core.reviews import review_store
from app.storage.config_loader import get_device_config, load_all_communities, load_customer_config


def get_community_status(customer_id: str | None = None, community_id: str | None = None) -> dict[str, object]:
    items = []
    for community in load_all_communities():
        if customer_id and community.customer_id != customer_id:
            continue
        if community_id and community.community_id != community_id:
            continue
        items.append(_build_community_status_item(community))

    return {
        "status": "ok",
        "count": len(items),
        "items": items,
    }


def _build_community_status_item(community: object) -> dict[str, object]:
    customer = load_customer_config(community.customer_id)
    device = get_device_config(community.device_id)
    bundle = load_context_bundle(community.customer_id, community.community_id)
    audits = read_recent_audit_events(community.customer_id, limit=50)
    last_patrol = _latest_event(
        audits,
        community.community_id,
        {"scheduled_patrol_processed", "community_patrol_review_ready", "community_patrol_skipped"},
    )
    last_send = _latest_event(audits, community.community_id, {"send_attempt"})
    last_openchat_validation = _latest_event(audits, community.community_id, {"openchat_validation_checked"})
    open_reviews = [
        review
        for review in review_store.list_pending()
        if review.customer_id == community.customer_id and review.community_id == community.community_id
    ]

    return {
        "customer_id": community.customer_id,
        "customer_name": customer.display_name,
        "community_id": community.community_id,
        "community_name": community.display_name,
        "device_id": community.device_id,
        "device_label": device.label,
        "enabled": community.enabled,
        "patrol_interval_minutes": community.patrol_interval_minutes,
        "persona_name": bundle.persona_name,
        "persona_loaded": bool(bundle.persona_text.strip()),
        "playbook_loaded": bool(bundle.playbook_text.strip()),
        "coordinates_ready": None not in (community.input_x, community.input_y, community.send_x, community.send_y),
        "coordinate_source": community.coordinate_source,
        "last_patrol_status": _status_from_event(last_patrol),
        "last_patrol_at": last_patrol.get("timestamp") if last_patrol else None,
        "last_send_status": _status_from_event(last_send),
        "last_send_at": last_send.get("timestamp") if last_send else None,
        "last_openchat_validation_status": _status_from_event(last_openchat_validation),
        "last_openchat_validation_reason": _reason_from_event(last_openchat_validation),
        "last_openchat_validation_at": last_openchat_validation.get("timestamp") if last_openchat_validation else None,
        "open_review_count": len(open_reviews),
        "open_review_statuses": [review.status for review in open_reviews[:10]],
    }


def _latest_event(
    audits: list[dict[str, object]],
    community_id: str,
    event_types: set[str],
) -> dict[str, object] | None:
    for event in reversed(audits):
        if event.get("event_type") not in event_types:
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        if payload.get("community_id") != community_id:
            continue
        return event
    return None


def _status_from_event(event: dict[str, object] | None) -> str | None:
    if event is None:
        return None
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    status = payload.get("status")
    if isinstance(status, str):
        return status
    if event.get("event_type") == "community_patrol_review_ready":
        return "review_ready"
    return None


def _reason_from_event(event: dict[str, object] | None) -> str | None:
    if event is None:
        return None
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    reason = payload.get("reason")
    return reason if isinstance(reason, str) else None
