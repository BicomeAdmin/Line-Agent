from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.storage.paths import reviews_state_path

ACTIVE_REVIEW_STATUSES = {"pending", "edit_required", "pending_reapproval"}
# "recalled" = operator approved (or was about to) and then regretted.
# Terminal because we can't actually un-send a LINE message via API; the
# status is an audit-trail marker, not a guarantee the message is gone.
TERMINAL_REVIEW_STATUSES = {"sent", "ignored", "recalled"}


@dataclass
class ReviewRecord:
    review_id: str
    source_job_id: str
    customer_id: str
    customer_name: str
    community_id: str
    community_name: str
    device_id: str
    draft_text: str
    reason: str | None = None
    confidence: float | None = None
    status: str = "pending"
    updated_from_action: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class ReviewStore:
    def __init__(self, state_path: Path | None = None, persist: bool = True) -> None:
        self._state_path = state_path or reviews_state_path()
        self._persist_enabled = persist
        self._lock = threading.Lock()
        self._reviews: dict[str, ReviewRecord] = {}
        # Track the last-loaded mtime so we can detect cross-process writes
        # (bridge / scheduler_daemon / MCP each have their own singleton).
        # Without this, an ignore from bridge wouldn't be visible to MCP and
        # codex's approve_review would silently send the stale draft.
        self._loaded_mtime: float = 0.0
        if self._persist_enabled:
            self._load()

    def _refresh_if_stale(self) -> None:
        """Re-read state from disk if another process has appended since
        we last loaded. Cheap mtime check; full reload only on change."""

        if not self._persist_enabled or not self._state_path.exists():
            return
        try:
            mtime = self._state_path.stat().st_mtime
        except OSError:
            return
        if mtime > self._loaded_mtime:
            self._reviews = {}
            self._load()

    def upsert(self, record: ReviewRecord) -> ReviewRecord:
        with self._lock:
            self._refresh_if_stale()
            existing = self._reviews.get(record.review_id)
            if existing is not None:
                record.created_at = existing.created_at
            record.updated_at = time.time()
            self._reviews[record.review_id] = record
            self._persist(record)
            return record

    def get(self, review_id: str) -> ReviewRecord | None:
        with self._lock:
            self._refresh_if_stale()
            return self._reviews.get(review_id)

    def list_all(self) -> list[ReviewRecord]:
        with self._lock:
            self._refresh_if_stale()
            return sorted(self._reviews.values(), key=lambda item: item.updated_at, reverse=True)

    def list_pending(self) -> list[ReviewRecord]:
        with self._lock:
            self._refresh_if_stale()
            return [
                item
                for item in sorted(self._reviews.values(), key=lambda review: review.updated_at, reverse=True)
                if item.status in ACTIVE_REVIEW_STATUSES
            ]

    def update_status(
        self,
        review_id: str,
        status: str,
        updated_from_action: str,
        draft_text: str | None = None,
    ) -> ReviewRecord | None:
        with self._lock:
            self._refresh_if_stale()
            review = self._reviews.get(review_id)
            if review is None:
                return None
            review.status = normalize_review_status(status)
            review.updated_from_action = updated_from_action
            if draft_text is not None:
                review.draft_text = draft_text
            review.updated_at = time.time()
            self._persist(review)
            return review

    def update_draft_text(
        self,
        review_id: str,
        new_draft_text: str,
        updated_from_action: str = "operator_revision",
    ) -> ReviewRecord | None:
        """Replace the draft_text of an existing review without changing
        status. Used after meta-feedback discussion produces a new agreed
        version of the draft — codex calls this before approve_review so
        the SENT text matches the DISCUSSED text, not the original."""

        with self._lock:
            self._refresh_if_stale()
            review = self._reviews.get(review_id)
            if review is None:
                return None
            review.draft_text = new_draft_text
            review.updated_from_action = updated_from_action
            review.updated_at = time.time()
            self._persist(review)
            return review

    def _persist(self, record: ReviewRecord) -> None:
        if not self._persist_enabled:
            return
        with self._state_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    def _load(self) -> None:
        if not self._state_path.exists():
            return
        latest: dict[str, ReviewRecord] = {}
        for raw_line in self._state_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            payload = json.loads(line)
            record = ReviewRecord(
                review_id=str(payload["review_id"]),
                source_job_id=str(payload["source_job_id"]),
                customer_id=str(payload["customer_id"]),
                customer_name=str(payload["customer_name"]),
                community_id=str(payload["community_id"]),
                community_name=str(payload["community_name"]),
                device_id=str(payload["device_id"]),
                draft_text=str(payload["draft_text"]),
                reason=str(payload["reason"]) if isinstance(payload.get("reason"), str) else None,
                confidence=float(payload["confidence"]) if isinstance(payload.get("confidence"), (int, float)) else None,
                status=normalize_review_status(str(payload.get("status", "pending"))),
                updated_from_action=str(payload["updated_from_action"]) if isinstance(payload.get("updated_from_action"), str) else None,
                created_at=float(payload.get("created_at", time.time())),
                updated_at=float(payload.get("updated_at", time.time())),
            )
            latest[record.review_id] = record
        self._reviews = latest
        try:
            self._loaded_mtime = self._state_path.stat().st_mtime
        except OSError:
            self._loaded_mtime = 0.0


def normalize_review_status(status: str) -> str:
    if status == "edited":
        return "pending_reapproval"
    return status


def review_status_label(status: str) -> str:
    labels = {
        "pending": "待審核",
        "edit_required": "待人工修改",
        "pending_reapproval": "待二次審核",
        "sent": "已送出",
        "ignored": "已忽略",
        "recalled": "已撤回",
    }
    return labels.get(status, status)


review_store = ReviewStore()
