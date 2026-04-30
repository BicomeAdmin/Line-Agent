"""Audit-event redaction for external sharing.

The audit log contains member message content (draft text, target
messages, recent_lines for fingerprint mirror). That's appropriate
for operator's own debugging on their machine, but **the moment the
log leaves the operator's machine** — pasted into a chat for support,
attached to a bug report, sent to a colleague — the members of those
LINE communities haven't consented to having their text shared.

This module produces a redacted copy of audit events suitable for
external sharing while preserving enough structure (event_type,
counts, community_id, status codes) for diagnostic value.

Two redaction levels:

  - **default**: strip member-content fields (draft_text, target_message,
    recent_lines, etc.). Keep community_id (operator's own data) and
    metadata (counts, status, error codes).

  - **minimal**: also strip community_id + sender names — useful for
    sharing with someone outside the operator's organization where
    even the community identity is sensitive.

Usage:
    from app.core.audit_redact import redact_event
    safe = redact_event(raw_event, level="default")

The redacted shape preserves event_type and timestamp 1:1; only
content fields are scrubbed.
"""

from __future__ import annotations

from typing import Any


# Field names whose values typically contain member or operator content.
# Anywhere these appear in payload (top level OR nested dicts), the
# value is replaced with a length marker.
_CONTENT_FIELDS = frozenset({
    "draft_text",
    "draft_preview",
    "text_preview",
    "target_message",
    "matched_text",
    "matched_text_preview",
    "expected_preview",
    "rationale",
    "raw_text",
    "current_title",
    "expected",
    "brief_preview",
    "samples",
    "observed_lines",
    "recent_lines",
    "text",
    "message_text",
    "content",
    "latest_text",
    "first_match",
    "matched",
})

# Field names that identify members by name (sender labels in chat
# context). Stripped in both default and minimal modes — member names
# are personal data even when their text is gone.
_SENDER_FIELDS = frozenset({
    "target_sender",
    "selector_target_sender",
    "sender",
    "matched_sender",
})

# Fields stripped only in minimal mode (community-identifying).
_COMMUNITY_FIELDS = frozenset({
    "community_id",
    "community_name",
    "current_title",
    "expected",
})


def redact_event(event: dict, *, level: str = "default") -> dict:
    """Return a deep-copied event with sensitive content fields redacted.

    `level` is "default" (strip content + sender) or "minimal" (also
    strip community identifiers).
    """

    if level not in {"default", "minimal"}:
        raise ValueError(f"unknown redaction level: {level}")

    return {
        "event_type": event.get("event_type"),
        "timestamp": event.get("timestamp"),
        "customer_id": "[redacted]" if level == "minimal" else event.get("customer_id"),
        "payload": _redact_value(event.get("payload"), level=level),
        "redacted": True,
        "redaction_level": level,
    }


def _redact_value(value: Any, *, level: str) -> Any:
    """Recursively redact dict/list/scalar values."""

    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if k in _CONTENT_FIELDS:
                out[k] = _length_marker(v)
            elif k in _SENDER_FIELDS:
                out[k] = "[sender]"
            elif level == "minimal" and k in _COMMUNITY_FIELDS:
                out[k] = "[community]"
            else:
                out[k] = _redact_value(v, level=level)
        return out
    if isinstance(value, list):
        return [_redact_value(item, level=level) for item in value]
    return value


def _length_marker(value: Any) -> str:
    """Replace a string with a length marker; non-strings → type tag.

    Empty/None → "[empty]"
    """

    if value is None:
        return "[empty]"
    if isinstance(value, str):
        n = len(value)
        if n == 0:
            return "[empty]"
        return f"[redacted {n} chars]"
    if isinstance(value, list):
        return f"[redacted list len={len(value)}]"
    return f"[redacted {type(value).__name__}]"


def redact_events(events: list[dict], *, level: str = "default") -> list[dict]:
    """Convenience: redact a batch."""

    return [redact_event(e, level=level) for e in events]
