"""Member lifecycle tagging — new / active / silent / churned.

Inspired by github.com/openscrm/api-server's 4-stage tag schema,
adapted to LINE OpenChat data and Paul《私域流量》's 用戶營運金字塔.
The reply target selector (Tier 2 follow-up) will use these tags to:

  - Skip churned members (won't see the reply anyway)
  - Bias toward active members (highest-leverage relationship)
  - Surface silent members to operator (re-engagement opportunity)

Definitions (relative to "now" — operator's local time):

  new       : first message ≤ 7 days ago
              → introduction / welcome opportunity
  active    : ≥ 1 message in last 7 days, total ≥ 3 messages
              → engagement target (Paul's 活躍 stage)
  silent    : last message 7-30 days ago
              → re-engagement opportunity (Paul's 留存 stage drift)
  churned   : last message > 30 days ago
              → likely lost; deprioritize, don't bot-prod

Storage: customers/<id>/data/member_lifecycle/<community_id>.json
  - Snapshot of the tag distribution + per-member detail
  - Used by selector + dashboard

Source: imported chat exports (same data layer as everything else).
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from app.core.audit import append_audit_event
from app.core.timezone import TAIPEI
from app.storage.config_loader import load_community_config
from app.storage.paths import customer_data_root
from app.workflows.chat_export_import import ChatMessage, parse_line_export
from app.workflows.member_fingerprint import latest_export_path
from app.workflows.relationship_graph import _is_system_sender


NEW_DAYS = 7
ACTIVE_DAYS = 7
SILENT_DAYS = 30
ACTIVE_MIN_MSGS = 3


def lifecycle_path(customer_id: str, community_id: str) -> Path:
    return customer_data_root(customer_id) / "member_lifecycle" / f"{community_id}.json"


def compute_lifecycle_tags(
    customer_id: str,
    community_id: str,
    *,
    reference_date: datetime | None = None,
) -> dict[str, object]:
    """Tag every sender with a lifecycle stage based on their message
    history relative to `reference_date` (defaults to now in Taipei)."""

    try:
        community = load_community_config(customer_id, community_id)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": f"community_lookup_failed:{exc}"}

    src = latest_export_path(customer_id, community_id)
    if src is None:
        return {"status": "error", "reason": "no_export_available"}

    try:
        messages = parse_line_export(src)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": f"parse_failed:{exc}"}

    now = (reference_date or datetime.now(TAIPEI)).date()
    operator_nick = (community.operator_nickname or "").strip()

    # Per-sender: first_seen_date, last_seen_date, message_count
    by_sender: dict[str, dict[str, object]] = defaultdict(
        lambda: {"first_seen": None, "last_seen": None, "count": 0}
    )

    for m in messages:
        if _is_system_sender(m.sender):
            continue
        if not m.date:
            continue
        try:
            d = datetime.strptime(m.date, "%Y-%m-%d").date()
        except ValueError:
            continue
        rec = by_sender[m.sender]
        rec["count"] = (rec["count"] or 0) + 1
        if rec["first_seen"] is None or d < rec["first_seen"]:
            rec["first_seen"] = d
        if rec["last_seen"] is None or d > rec["last_seen"]:
            rec["last_seen"] = d

    members: list[dict[str, object]] = []
    for sender, rec in by_sender.items():
        first_seen = rec["first_seen"]
        last_seen = rec["last_seen"]
        count = rec["count"]
        days_since_first = (now - first_seen).days if first_seen else None
        days_since_last = (now - last_seen).days if last_seen else None

        # Stage classification
        if sender == operator_nick or sender == "__operator__":
            stage = "operator"  # special category
        elif days_since_last is None:
            stage = "unknown"
        elif days_since_first is not None and days_since_first <= NEW_DAYS:
            stage = "new"
        elif days_since_last <= ACTIVE_DAYS and count >= ACTIVE_MIN_MSGS:
            stage = "active"
        elif days_since_last <= SILENT_DAYS:
            stage = "silent"
        else:
            stage = "churned"

        members.append({
            "sender": sender,
            "stage": stage,
            "message_count": count,
            "first_seen": str(first_seen) if first_seen else None,
            "last_seen": str(last_seen) if last_seen else None,
            "days_since_first": days_since_first,
            "days_since_last": days_since_last,
        })

    members.sort(key=lambda m: (m["stage"], -(m["message_count"] or 0)))

    distribution = Counter(m["stage"] for m in members)

    snapshot = {
        "community_id": community_id,
        "community_name": community.display_name,
        "computed_at_taipei": datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M:%S"),
        "reference_date": str(now),
        "source_file": str(src),
        "total_distinct_members": len(members),
        "distribution": dict(distribution),
        "members": members,
    }

    out_path = lifecycle_path(customer_id, community_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    append_audit_event(
        customer_id,
        "lifecycle_tags_computed",
        {
            "community_id": community_id,
            "total_members": len(members),
            "distribution": dict(distribution),
        },
    )

    snapshot["status"] = "ok"
    snapshot["stored_at"] = str(out_path)
    return snapshot


def load_lifecycle_tags(
    customer_id: str,
    community_id: str,
) -> dict[str, object] | None:
    """Read cached lifecycle snapshot."""

    path = lifecycle_path(customer_id, community_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def get_member_stage(
    customer_id: str,
    community_id: str,
    sender: str,
) -> str | None:
    """Quick lookup: what's this member's current lifecycle stage?
    Returns None if cache is missing or sender unknown."""

    snap = load_lifecycle_tags(customer_id, community_id)
    if not snap:
        return None
    for m in snap.get("members") or []:
        if isinstance(m, dict) and m.get("sender") == sender:
            return m.get("stage")
    return None
