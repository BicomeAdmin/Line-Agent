"""Scheduled-post storage + lifecycle.

Per-community JSON state file at `customers/{customer_id}/data/scheduled_posts/{community_id}.json`.
All mutations also append an audit event so the action history survives anywhere
the audit log goes.

Status transitions:
    scheduled  -> due        (picked up by scheduler tick)
    scheduled  -> cancelled  (operator cancellation, terminal)
    due        -> reviewing  (queued through review/Lark, terminal for the post itself
                              since the review record now drives final send/ignore)
    due        -> sent       (auto-sent path when pre_approved + human approval disabled)
    due        -> skipped    (community disabled, missing device, etc.)
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.core.audit import append_audit_event
from app.storage.paths import scheduled_posts_path
from app.workflows.scheduled_post_recurrence import (
    RecurrenceError,
    bump_fired,
    next_occurrence,
    normalize_recurrence,
)

ACTIVE_STATUSES = {"scheduled", "due", "reviewing"}
TERMINAL_STATUSES = {"sent", "cancelled", "skipped"}

# Default 4 hours of compose-lead window — operator gets the LLM draft up
# to 4h before the scheduled send time, plenty of room to review and edit.
DEFAULT_COMPOSE_LEAD_SECONDS = 4 * 3600


@dataclass
class ScheduledPost:
    post_id: str
    customer_id: str
    community_id: str
    send_at_epoch: float
    send_at_iso: str
    text: str
    status: str = "scheduled"
    pre_approved: bool = False
    notes: str | None = None
    created_at_epoch: float = field(default_factory=time.time)
    updated_at_epoch: float = field(default_factory=time.time)
    review_id: str | None = None
    job_id: str | None = None
    sent_at_epoch: float | None = None
    skip_reason: str | None = None
    # LLM compose-mode fields (Goal 1 ↔ Goal 2 bridge):
    #   - compose_mode=true → at trigger time codex_compose runs against
    #     `brief`, draft is staged into review_store. `text` starts empty
    #     and is populated by the composer.
    #   - compose_lead_seconds shifts the daemon's pickup time earlier
    #     than send_at_epoch, giving the operator a review window.
    brief: str | None = None
    compose_mode: bool = False
    compose_lead_seconds: int = 0
    # Recurrence dict (see scheduled_post_recurrence.py for schema).
    # When present, mark_post_sent spawns the next occurrence as a new
    # ScheduledPost so the chain self-perpetuates.
    recurrence: dict | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


_lock = threading.Lock()


def _read_file(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "[]")
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _write_file(path: Path, posts: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(posts, ensure_ascii=False, indent=2)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def _parse_send_at(send_at: str | float | int) -> tuple[float, str]:
    if isinstance(send_at, (int, float)):
        ts = float(send_at)
        return ts, datetime.fromtimestamp(ts, timezone.utc).isoformat()
    text = str(send_at).strip()
    try:
        # Accept both "2026-04-28T20:00:00+08:00" and "2026-04-28T20:00:00Z" forms.
        normalized = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"send_at must be ISO 8601 (e.g. 2026-04-28T20:00:00+08:00): {text!r}") from exc
    if dt.tzinfo is None:
        raise ValueError(f"send_at must include timezone offset: {text!r}")
    return dt.timestamp(), dt.isoformat()


def add_scheduled_post(
    customer_id: str,
    community_id: str,
    send_at: str | float | int,
    text: str | None,
    *,
    pre_approved: bool = False,
    notes: str | None = None,
    brief: str | None = None,
    compose_mode: bool = False,
    compose_lead_seconds: int | None = None,
    recurrence: dict | None = None,
) -> dict[str, object]:
    """Create a scheduled post. Two modes:

    - **Direct text** (default): caller supplies fully-written `text`.
      Daemon fires at send_at, pushes the text into review pipeline.
    - **Compose mode** (`compose_mode=True`): caller supplies a `brief`
      describing the topic; daemon fires `compose_lead_seconds` BEFORE
      send_at, runs codex_compose against the community's voice_profile,
      and pushes the LLM draft for operator review. `text` is ignored
      at creation and filled in by the composer at fire time.
    """

    if compose_mode:
        brief_clean = (brief or "").strip()
        if not brief_clean:
            raise ValueError("compose_mode=true requires a non-empty brief.")
        text_value = ""  # filled by composer at fire time
        notes = notes  # passthrough
    else:
        text_value = (text or "").strip()
        if not text_value:
            raise ValueError("Scheduled post text cannot be empty.")
        brief_clean = None

    send_at_epoch, send_at_iso = _parse_send_at(send_at)
    if send_at_epoch <= time.time():
        raise ValueError("send_at must be in the future.")

    lead_value = compose_lead_seconds if compose_lead_seconds is not None else (
        DEFAULT_COMPOSE_LEAD_SECONDS if compose_mode else 0
    )
    if lead_value < 0:
        raise ValueError("compose_lead_seconds must be >= 0.")

    try:
        recurrence_norm = normalize_recurrence(recurrence)
    except RecurrenceError as exc:
        raise ValueError(f"recurrence: {exc}") from exc

    post_id = f"post-{secrets.token_hex(6)}"
    record = ScheduledPost(
        post_id=post_id,
        customer_id=customer_id,
        community_id=community_id,
        send_at_epoch=send_at_epoch,
        send_at_iso=send_at_iso,
        text=text_value,
        pre_approved=pre_approved,
        notes=notes,
        brief=brief_clean,
        compose_mode=compose_mode,
        compose_lead_seconds=int(lead_value),
        recurrence=recurrence_norm,
    )
    path = scheduled_posts_path(customer_id, community_id)
    with _lock:
        posts = _read_file(path)
        posts.append(record.to_dict())
        _write_file(path, posts)
    append_audit_event(
        customer_id,
        "scheduled_post_added",
        {
            "post_id": post_id,
            "community_id": community_id,
            "send_at": send_at_iso,
            "pre_approved": pre_approved,
            "compose_mode": compose_mode,
            "brief_preview": (brief_clean or "")[:60] if compose_mode else None,
            "text_preview": text_value[:60] if not compose_mode else None,
            "recurrence_kind": (recurrence_norm or {}).get("kind"),
        },
    )
    return record.to_dict()


def list_scheduled_posts(
    customer_id: str,
    community_id: str,
    *,
    statuses: set[str] | None = None,
) -> list[dict[str, object]]:
    path = scheduled_posts_path(customer_id, community_id)
    with _lock:
        posts = _read_file(path)
    if statuses is not None:
        posts = [p for p in posts if p.get("status") in statuses]
    return sorted(posts, key=lambda p: float(p.get("send_at_epoch") or 0))


def list_all_scheduled_posts(*, statuses: set[str] | None = None) -> list[dict[str, object]]:
    from app.storage.config_loader import load_all_communities

    items: list[dict[str, object]] = []
    for community in load_all_communities():
        items.extend(
            list_scheduled_posts(
                community.customer_id,
                community.community_id,
                statuses=statuses,
            )
        )
    return sorted(items, key=lambda p: float(p.get("send_at_epoch") or 0))


def _update_post(
    customer_id: str,
    community_id: str,
    post_id: str,
    updater,
) -> dict[str, object] | None:
    path = scheduled_posts_path(customer_id, community_id)
    with _lock:
        posts = _read_file(path)
        for entry in posts:
            if entry.get("post_id") == post_id:
                updater(entry)
                entry["updated_at_epoch"] = time.time()
                _write_file(path, posts)
                return entry
    return None


def cancel_scheduled_post(
    customer_id: str,
    community_id: str,
    post_id: str,
    reason: str = "operator_cancelled",
) -> dict[str, object] | None:
    def _apply(entry: dict[str, object]) -> None:
        if entry.get("status") in TERMINAL_STATUSES:
            return
        entry["status"] = "cancelled"
        entry["skip_reason"] = reason

    updated = _update_post(customer_id, community_id, post_id, _apply)
    if updated is not None:
        append_audit_event(
            customer_id,
            "scheduled_post_cancelled",
            {"post_id": post_id, "community_id": community_id, "reason": reason},
        )
    return updated


def post_effective_trigger_epoch(post: dict[str, object]) -> float:
    """Return the timestamp at which the scheduler should pick this post up.

    Compose-mode posts trigger `compose_lead_seconds` before `send_at_epoch`
    so the composer has time to produce a draft and the operator has time
    to review it. Direct-text posts trigger exactly at `send_at_epoch`.
    """

    send_at = float(post.get("send_at_epoch") or 0)
    if not bool(post.get("compose_mode")):
        return send_at
    lead = int(post.get("compose_lead_seconds") or 0)
    return send_at - lead


def find_due_posts(now: float | None = None) -> list[dict[str, object]]:
    current = now if now is not None else time.time()
    return [
        post
        for post in list_all_scheduled_posts(statuses={"scheduled"})
        if post_effective_trigger_epoch(post) <= current
    ]


def mark_post_due(customer_id: str, community_id: str, post_id: str, *, job_id: str | None = None) -> dict[str, object] | None:
    def _apply(entry: dict[str, object]) -> None:
        if entry.get("status") == "scheduled":
            entry["status"] = "due"
        if job_id:
            entry["job_id"] = job_id

    return _update_post(customer_id, community_id, post_id, _apply)


def mark_post_reviewing(
    customer_id: str,
    community_id: str,
    post_id: str,
    *,
    review_id: str,
) -> dict[str, object] | None:
    def _apply(entry: dict[str, object]) -> None:
        entry["status"] = "reviewing"
        entry["review_id"] = review_id

    updated = _update_post(customer_id, community_id, post_id, _apply)
    if updated is not None:
        append_audit_event(
            customer_id,
            "scheduled_post_reviewing",
            {"post_id": post_id, "community_id": community_id, "review_id": review_id},
        )
    return updated


def mark_post_sent(
    customer_id: str,
    community_id: str,
    post_id: str,
    *,
    send_result: dict[str, object] | None = None,
) -> dict[str, object] | None:
    def _apply(entry: dict[str, object]) -> None:
        entry["status"] = "sent"
        entry["sent_at_epoch"] = time.time()

    updated = _update_post(customer_id, community_id, post_id, _apply)
    if updated is not None:
        append_audit_event(
            customer_id,
            "scheduled_post_sent",
            {
                "post_id": post_id,
                "community_id": community_id,
                "send_result_status": (send_result or {}).get("status"),
            },
        )
        _spawn_next_occurrence_if_recurring(updated)
    return updated


def _spawn_next_occurrence_if_recurring(parent: dict[str, object]) -> dict[str, object] | None:
    """If the post has a recurrence, create the next occurrence as a fresh post.

    Called from mark_post_sent so the chain only advances when a send
    completes. Cancellation / skip do NOT advance the chain (operator
    decides whether to keep the series).
    """

    recurrence = parent.get("recurrence")
    if not isinstance(recurrence, dict):
        return None
    next_at = next_occurrence(recurrence, after_epoch=float(parent.get("send_at_epoch") or 0))
    if next_at is None:
        append_audit_event(
            str(parent.get("customer_id") or ""),
            "scheduled_post_recurrence_exhausted",
            {
                "parent_post_id": parent.get("post_id"),
                "community_id": parent.get("community_id"),
                "recurrence_kind": recurrence.get("kind"),
            },
        )
        return None
    next_epoch, next_iso = next_at
    next_recurrence = bump_fired(recurrence)
    customer_id = str(parent.get("customer_id") or "")
    community_id = str(parent.get("community_id") or "")
    try:
        spawned = add_scheduled_post(
            customer_id,
            community_id,
            next_iso,
            parent.get("text") if not parent.get("compose_mode") else None,
            pre_approved=bool(parent.get("pre_approved")),
            notes=parent.get("notes"),
            brief=parent.get("brief") if parent.get("compose_mode") else None,
            compose_mode=bool(parent.get("compose_mode")),
            compose_lead_seconds=int(parent.get("compose_lead_seconds") or 0) or None,
            recurrence=next_recurrence,
        )
    except ValueError as exc:
        append_audit_event(
            customer_id,
            "scheduled_post_recurrence_failed",
            {
                "parent_post_id": parent.get("post_id"),
                "community_id": community_id,
                "error": str(exc),
            },
        )
        return None
    append_audit_event(
        customer_id,
        "scheduled_post_recurrence_spawned",
        {
            "parent_post_id": parent.get("post_id"),
            "spawned_post_id": spawned.get("post_id"),
            "community_id": community_id,
            "next_send_at": next_iso,
            "recurrence_kind": recurrence.get("kind"),
            "occurrences_fired": (next_recurrence or {}).get("occurrences_fired"),
        },
    )
    return spawned


def mark_post_skipped(
    customer_id: str,
    community_id: str,
    post_id: str,
    *,
    reason: str,
) -> dict[str, object] | None:
    def _apply(entry: dict[str, object]) -> None:
        entry["status"] = "skipped"
        entry["skip_reason"] = reason

    updated = _update_post(customer_id, community_id, post_id, _apply)
    if updated is not None:
        append_audit_event(
            customer_id,
            "scheduled_post_skipped",
            {"post_id": post_id, "community_id": community_id, "reason": reason},
        )
    return updated


def get_post(customer_id: str, community_id: str, post_id: str) -> dict[str, object] | None:
    for entry in list_scheduled_posts(customer_id, community_id):
        if entry.get("post_id") == post_id:
            return entry
    return None
