"""Watcher Phase 2 — persistent watch state.

A "watch" tells the scheduler daemon to periodically scan a community for
new replies and (when found) spawn a Codex turn that decides whether to
draft a follow-up. The draft still goes through review_store / HIL — watches
do **not** bypass the operator approval gate.

State file: customers/<customer_id>/data/watches.json
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.core.audit import append_audit_event
from app.storage.paths import watches_state_path

_lock = threading.Lock()


@dataclass
class Watch:
    watch_id: str
    customer_id: str
    community_id: str
    started_at_epoch: float
    end_at_epoch: float
    initiator_chat_id: str | None  # Lark chat_id to push notifications back to
    cooldown_seconds: int = 300  # min seconds between auto-drafts for the same watch
    poll_interval_seconds: int = 60
    last_check_epoch: float | None = None
    last_seen_signature: str | None = None
    last_draft_epoch: float | None = None
    status: str = "active"  # active | expired | cancelled
    note: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _read_all(customer_id: str) -> list[dict[str, object]]:
    path = watches_state_path(customer_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "[]")
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _write_all(customer_id: str, items: list[dict[str, object]]) -> None:
    path = watches_state_path(customer_id)
    payload = json.dumps(items, ensure_ascii=False, indent=2)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def add_watch(
    customer_id: str,
    community_id: str,
    *,
    duration_minutes: int,
    initiator_chat_id: str | None = None,
    cooldown_seconds: int = 300,
    poll_interval_seconds: int = 60,
    note: str | None = None,
) -> dict[str, object]:
    if duration_minutes <= 0:
        raise ValueError("duration_minutes must be > 0")
    now = time.time()
    watch_id = f"watch-{int(now)}-{community_id}"
    record = Watch(
        watch_id=watch_id,
        customer_id=customer_id,
        community_id=community_id,
        started_at_epoch=now,
        end_at_epoch=now + duration_minutes * 60,
        initiator_chat_id=initiator_chat_id,
        cooldown_seconds=cooldown_seconds,
        poll_interval_seconds=poll_interval_seconds,
        note=note,
    )
    with _lock:
        items = _read_all(customer_id)
        # Cancel existing active watch on the same community (one watch per community).
        for entry in items:
            if entry.get("community_id") == community_id and entry.get("status") == "active":
                entry["status"] = "cancelled"
                entry["note"] = (entry.get("note") or "") + " [superseded]"
        items.append(record.to_dict())
        _write_all(customer_id, items)
    append_audit_event(
        customer_id,
        "watch_started",
        {
            "watch_id": watch_id,
            "community_id": community_id,
            "duration_minutes": duration_minutes,
            "end_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(record.end_at_epoch)),
            "initiator_chat_id": initiator_chat_id,
        },
    )
    return record.to_dict()


def stop_watch(customer_id: str, watch_id: str | None = None, community_id: str | None = None, *, reason: str = "operator_stopped") -> list[dict[str, object]]:
    stopped: list[dict[str, object]] = []
    with _lock:
        items = _read_all(customer_id)
        for entry in items:
            if entry.get("status") != "active":
                continue
            if watch_id and entry.get("watch_id") != watch_id:
                continue
            if community_id and entry.get("community_id") != community_id:
                continue
            entry["status"] = "cancelled"
            entry["note"] = (entry.get("note") or "") + f" [stopped:{reason}]"
            stopped.append(entry)
        _write_all(customer_id, items)
    for entry in stopped:
        append_audit_event(
            customer_id,
            "watch_stopped",
            {"watch_id": entry["watch_id"], "community_id": entry["community_id"], "reason": reason},
        )
    return stopped


def list_watches(customer_id: str, *, only_active: bool = False) -> list[dict[str, object]]:
    items = _read_all(customer_id)
    if only_active:
        items = [e for e in items if e.get("status") == "active"]
    return sorted(items, key=lambda e: float(e.get("started_at_epoch") or 0), reverse=True)


def list_active_watches_all_customers() -> list[dict[str, object]]:
    """Daemon entry point — scan every customer's watches.json for active ones."""

    from app.storage.config_loader import load_devices_config

    seen_customers: set[str] = set()
    for device in load_devices_config():
        seen_customers.add(device.customer_id)
    out: list[dict[str, object]] = []
    now = time.time()
    for customer_id in sorted(seen_customers):
        items = _read_all(customer_id)
        for entry in items:
            if entry.get("status") != "active":
                continue
            if float(entry.get("end_at_epoch") or 0) <= now:
                # Auto-expire stale watches as we encounter them.
                entry["status"] = "expired"
                entry["note"] = (entry.get("note") or "") + " [auto_expired]"
                continue
            out.append(entry)
        # Persist any auto-expirations we made.
        if any(e.get("status") == "expired" for e in items):
            _write_all(customer_id, items)
    return out


def update_watch_state(
    customer_id: str,
    watch_id: str,
    *,
    last_check_epoch: float | None = None,
    last_seen_signature: str | None = None,
    last_draft_epoch: float | None = None,
) -> None:
    with _lock:
        items = _read_all(customer_id)
        for entry in items:
            if entry.get("watch_id") == watch_id:
                if last_check_epoch is not None:
                    entry["last_check_epoch"] = last_check_epoch
                if last_seen_signature is not None:
                    entry["last_seen_signature"] = last_seen_signature
                if last_draft_epoch is not None:
                    entry["last_draft_epoch"] = last_draft_epoch
                break
        _write_all(customer_id, items)


def messages_signature(messages: list[dict]) -> str:
    """Stable hash of recent message texts — used to detect whether new content arrived."""

    payload = json.dumps(
        [str(m.get("text", "")).strip() for m in messages if str(m.get("text", "")).strip()],
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
