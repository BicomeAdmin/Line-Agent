from __future__ import annotations

import json
from datetime import datetime, timezone

from app.storage.paths import audit_log_path, ensure_customer_directories


def append_audit_event(customer_id: str, event_type: str, payload: dict[str, object]) -> None:
    ensure_customer_directories(customer_id)
    target = audit_log_path(customer_id)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "payload": payload,
    }
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_recent_audit_events(customer_id: str, limit: int = 20) -> list[dict[str, object]]:
    target = audit_log_path(customer_id)
    if not target.exists():
        return []
    return _parse_audit_lines(target.read_text(encoding="utf-8").splitlines()[-limit:])


def read_all_audit_events(customer_id: str) -> list[dict[str, object]]:
    target = audit_log_path(customer_id)
    if not target.exists():
        return []
    return _parse_audit_lines(target.read_text(encoding="utf-8").splitlines())


def _parse_audit_lines(lines: list[str]) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for line in lines:
        if not line.strip():
            continue
        events.append(json.loads(line))
    return events
