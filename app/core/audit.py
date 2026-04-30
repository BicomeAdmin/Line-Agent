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


# Soft warning threshold for audit log size — at this point operator
# should consider archiving or running export_audit_redacted before
# the file becomes unwieldy to read in editors / share for debug.
AUDIT_LOG_WARN_BYTES = 50 * 1024 * 1024     # 50 MB
# Hard suggestion threshold — system still works, but read paths get
# slow and recovery-on-restart times balloon.
AUDIT_LOG_CRITICAL_BYTES = 200 * 1024 * 1024  # 200 MB


def audit_log_stats(customer_id: str) -> dict[str, object]:
    """Diagnostic snapshot of the customer's audit log: size in bytes,
    line count, oldest/newest timestamps. Cheap (one file stat + one
    line scan); used by daemon startup and dashboard health panel.

    Returns dict with: path, exists, size_bytes, size_human, line_count,
    oldest_ts, newest_ts, severity ("ok" | "warn" | "critical").
    """

    target = audit_log_path(customer_id)
    out: dict[str, object] = {
        "path": str(target),
        "exists": target.exists(),
        "size_bytes": 0,
        "size_human": "0 B",
        "line_count": 0,
        "oldest_ts": None,
        "newest_ts": None,
        "severity": "ok",
    }
    if not target.exists():
        return out
    size = target.stat().st_size
    out["size_bytes"] = size
    out["size_human"] = _format_bytes(size)
    if size >= AUDIT_LOG_CRITICAL_BYTES:
        out["severity"] = "critical"
    elif size >= AUDIT_LOG_WARN_BYTES:
        out["severity"] = "warn"

    # Cheap-ish: read first + last line only for timestamps. For very
    # large files we still want this to stay fast, so we sample the
    # head and tail rather than counting all lines.
    try:
        with target.open("rb") as handle:
            first_line = handle.readline().decode("utf-8", errors="replace")
            handle.seek(max(0, size - 8192))
            tail = handle.read().decode("utf-8", errors="replace")
        if first_line.strip():
            try:
                out["oldest_ts"] = json.loads(first_line).get("timestamp")
            except (ValueError, json.JSONDecodeError):
                pass
        last_line = tail.rstrip().rsplit("\n", 1)[-1] if tail.strip() else ""
        if last_line.strip():
            try:
                out["newest_ts"] = json.loads(last_line).get("timestamp")
            except (ValueError, json.JSONDecodeError):
                pass
    except OSError:
        pass

    # Line count is an honest count when file is small; for >10MB we
    # estimate from byte size to avoid loading the whole file twice.
    if size < 10 * 1024 * 1024:
        try:
            with target.open("r", encoding="utf-8") as handle:
                out["line_count"] = sum(1 for line in handle if line.strip())
        except OSError:
            pass
    else:
        # Use first 1MB to estimate avg line size, extrapolate.
        try:
            with target.open("rb") as handle:
                sample = handle.read(1024 * 1024).decode("utf-8", errors="replace")
            sample_lines = [line for line in sample.split("\n") if line.strip()]
            if sample_lines:
                avg_len = len(sample) / len(sample_lines)
                out["line_count"] = int(size / avg_len)  # estimated
        except OSError:
            pass
    return out


def _format_bytes(n: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    size = float(n)
    for unit in units:
        if size < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"
