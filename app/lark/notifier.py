"""Centralized Lark notifier for operator-facing surfaces.

Single source of truth for "push something interactive to the operator's
main chat". Today: review cards (the inbox UI). Tomorrow: same hook can
fan out to scheduled-post status changes, daemon errors, etc.

Behavior:
  - Reads OPERATOR_DAILY_DIGEST_CHAT_ID from env. When unset, all calls
    here no-op cleanly so devs/CI can run without Lark wired.
  - Failures (LarkClientError) are logged via append_audit_event but
    swallowed — a notification miss must never block the underlying
    workflow (compose_and_send, edit_review, etc.).
  - Caller stays simple: pass the ReviewRecord, we figure out card
    layout / title / chat_id.
"""

from __future__ import annotations

import os
import sys
import traceback

from app.core.audit import append_audit_event
from app.core.reviews import ReviewRecord
from app.lark.cards import build_review_card


_TITLE_BY_REASON: dict[str, str] = {
    "mcp_compose:operator": "📝 操作員擬稿 — 待審核",
    "mcp_compose:auto_watch": "🛎 自動追蹤擬稿 — 待審核",
    "mcp_compose": "📝 LLM 擬稿 — 待審核",  # legacy, pre source-tagging
    "patrol": "🛰 巡邏擬稿 — 待審核",
    "scheduled_post": "🗓 排程擬稿 — 待審核",
    "edit_required": "✏️ 編輯後重審 — 待核准",
}


def _resolve_card_title(record: ReviewRecord) -> str:
    reason = record.reason or ""
    if reason in _TITLE_BY_REASON:
        return _TITLE_BY_REASON[reason]
    # Match by prefix for any future "<family>:<source>" pattern.
    for key, title in _TITLE_BY_REASON.items():
        if reason.startswith(key.split(":")[0]):
            return title
    return "🤖 待審核"


def operator_chat_id() -> str:
    return os.getenv("OPERATOR_DAILY_DIGEST_CHAT_ID", "").strip()


def notify_operator_of_new_review(record: ReviewRecord) -> dict[str, object]:
    """Push an interactive review card with [通過/修改/忽略] buttons.

    Returns a small dict the caller can log: status, chat_id, error.
    Never raises — failures are converted to {"status": "error", ...}.
    """

    chat_id = operator_chat_id()
    if not chat_id:
        return {"status": "skipped", "reason": "no_operator_chat_id"}

    try:
        from app.lark.client import LarkClient, LarkClientError
    except ImportError as exc:
        return {"status": "error", "reason": f"lark_import_failed:{exc}"}

    try:
        card = build_review_card(
            customer_name=record.customer_name,
            community_name=record.community_name,
            draft=record.draft_text,
            job_id=record.review_id,
            customer_id=record.customer_id,
            community_id=record.community_id,
            device_id=record.device_id,
            reason=record.reason,
            confidence=record.confidence,
            draft_title=_resolve_card_title(record),
        )
        client = LarkClient()
        client.send_card(chat_id, card, receive_id_type="chat_id")
        append_audit_event(
            record.customer_id,
            "operator_review_card_pushed",
            {
                "review_id": record.review_id,
                "community_id": record.community_id,
                "reason": record.reason,
                "chat_id_prefix": chat_id[:12],
            },
        )
        return {"status": "ok", "chat_id_prefix": chat_id[:12]}
    except LarkClientError as exc:
        # Operational miss — log and swallow. The review still lives in
        # review_store; operator can still approve via CLI / dashboard.
        append_audit_event(
            record.customer_id,
            "operator_review_card_failed",
            {
                "review_id": record.review_id,
                "error": str(exc),
            },
        )
        return {"status": "error", "reason": f"lark_error:{exc}"}
    except Exception as exc:  # noqa: BLE001 — never bubble up
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
        return {"status": "error", "reason": f"unexpected:{exc!r}"}
