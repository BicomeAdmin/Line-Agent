"""Export redacted audit log for external sharing (debug / support / docs).

Standard audit.jsonl contains member chat content; this script writes
a sanitized copy with content fields replaced by length markers.

Examples:
    # Default: strip draft text + member names; keep community_id
    python3 scripts/export_audit_redacted.py customer_a > redacted.jsonl

    # Minimal: also strip community_id (anonymous operator-side debug)
    python3 scripts/export_audit_redacted.py customer_a --level minimal > redacted.jsonl

    # Last 24h only
    python3 scripts/export_audit_redacted.py customer_a --since-hours 24 > recent.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime

import _bootstrap  # noqa: F401

from app.core.audit import read_recent_audit_events
from app.core.audit_redact import redact_event


def _event_epoch(event: dict) -> float | None:
    raw = event.get("timestamp")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("customer_id")
    parser.add_argument(
        "--level", choices=["default", "minimal"], default="default",
        help="Redaction level. default=strip content+sender; minimal=also strip community identifiers.",
    )
    parser.add_argument(
        "--since-hours", type=float, default=None,
        help="Only include events newer than N hours. Default: full log.",
    )
    parser.add_argument(
        "--limit", type=int, default=10_000,
        help="Max events to read. Defaults to 10000.",
    )
    args = parser.parse_args()

    events = read_recent_audit_events(args.customer_id, limit=args.limit) or []
    if args.since_hours is not None:
        cutoff = time.time() - args.since_hours * 3600
        events = [e for e in events if (_event_epoch(e) or 0) >= cutoff]

    for ev in events:
        redacted = redact_event(ev, level=args.level)
        sys.stdout.write(json.dumps(redacted, ensure_ascii=False) + "\n")

    print(
        f"\n[exported {len(events)} events at level={args.level}, customer={args.customer_id}]",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
