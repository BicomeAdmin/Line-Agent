from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from app.storage.paths import audit_log_path, ensure_customer_directories


class AuditValidationError(ValueError):
    """Raised when an audit event is malformed.

    We fail loud on the way in rather than letting bad records into the log,
    because audit.jsonl is the authoritative incident-recovery source — silent
    coercion of None / wrong types would mask the very bugs audit exists to
    surface. If a workflow can't satisfy the schema, that's a workflow bug.
    """


_EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _validate(customer_id: object, event_type: object, payload: object) -> None:
    if not isinstance(customer_id, str) or not customer_id.strip():
        raise AuditValidationError(f"customer_id must be a non-empty str, got {customer_id!r}")
    if not isinstance(event_type, str) or not _EVENT_TYPE_RE.match(event_type):
        raise AuditValidationError(
            f"event_type must be snake_case ([a-z][a-z0-9_]*), got {event_type!r}"
        )
    if not isinstance(payload, dict):
        raise AuditValidationError(f"payload must be a dict, got {type(payload).__name__}")


def append_audit_event(customer_id: str, event_type: str, payload: dict[str, object]) -> None:
    _validate(customer_id, event_type, payload)
    ensure_customer_directories(customer_id)
    target = audit_log_path(customer_id)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "payload": payload,
    }
    # Probe JSON-serializability before opening the file so a bad payload
    # doesn't write a partial line that breaks _parse_audit_lines later.
    try:
        line = json.dumps(entry, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise AuditValidationError(f"payload not JSON-serializable: {exc}") from exc
    with target.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


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
