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

ACTIVE_STATUSES = {"scheduled", "due", "reviewing"}
TERMINAL_STATUSES = {"sent", "cancelled", "skipped"}


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
    text: str,
    *,
    pre_approved: bool = False,
    notes: str | None = None,
) -> dict[str, object]:
    text = (text or "").strip()
    if not text:
        raise ValueError("Scheduled post text cannot be empty.")
    send_at_epoch, send_at_iso = _parse_send_at(send_at)
    if send_at_epoch <= time.time():
        raise ValueError("send_at must be in the future.")
    post_id = f"post-{secrets.token_hex(6)}"
    record = ScheduledPost(
        post_id=post_id,
        customer_id=customer_id,
        community_id=community_id,
        send_at_epoch=send_at_epoch,
        send_at_iso=send_at_iso,
        text=text,
        pre_approved=pre_approved,
        notes=notes,
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
            "text_preview": text[:60],
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


def find_due_posts(now: float | None = None) -> list[dict[str, object]]:
    current = now if now is not None else time.time()
    return [
        post
        for post in list_all_scheduled_posts(statuses={"scheduled"})
        if float(post.get("send_at_epoch") or 0) <= current
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
    return updated


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
