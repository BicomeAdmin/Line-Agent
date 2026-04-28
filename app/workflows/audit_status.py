from __future__ import annotations

from app.core.audit import read_recent_audit_events


def get_audit_status(customer_id: str, limit: int = 20) -> dict[str, object]:
    events = read_recent_audit_events(customer_id, limit=limit)
    return {
        "status": "ok",
        "customer_id": customer_id,
        "event_count": len(events),
        "events": events,
    }
