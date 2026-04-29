"""In-process watch tick — replaces the codex/MCP spawn path.

Why this exists: the codex spawn path was failing with "Transport closed"
because each codex invocation forks a fresh MCP server that has to
cold-load BGE + Chinese-Emotion (~22 s) before answering the first tool
call. codex's MCP client kills the transport before the load completes.

This path stays in-process inside the scheduler_daemon — models are
warmed up once at daemon boot (see app.workflows.model_warmup) and reused
forever. Side benefits:
  - watch ticks are debuggable (single Python stack, no subprocess)
  - no per-tick codex overhead
  - drafts go through the same review_store / Lark card path so the
    operator sees identical UX

Trade-off: composition is currently rule-based (app.ai.decision.decide_reply)
since LLM is dormant. When ECHO_LLM_ENABLED=true, decide_reply auto-routes
through the LLM. So this path doesn't bake in rule-only behavior — it
respects the same env switch the rest of the system does.

HIL gate unchanged. All drafts still land as pending reviews.
"""

from __future__ import annotations

import time
from pathlib import Path

from app.adb.client import AdbClient
from app.core.audit import append_audit_event
from app.core.reviews import ReviewRecord, review_store
from app.lark.client import LarkClient, LarkClientError
from app.storage.config_loader import load_community_config, load_customer_config
from app.storage.paths import default_raw_xml_path
from app.workflows.openchat_navigate import navigate_to_openchat
from app.workflows.read_chat import read_recent_chat
from app.workflows.reply_target_selector import select_reply_target as select_reply_target_workflow


def tick_one_inprocess(watch: dict[str, object]) -> dict[str, object]:
    customer_id = str(watch.get("customer_id"))
    community_id = str(watch.get("community_id"))
    watch_id = str(watch.get("watch_id"))
    cooldown = int(watch.get("cooldown_seconds") or 300)
    last_draft = float(watch.get("last_draft_epoch") or 0)
    last_signature = watch.get("last_seen_signature") or ""

    from app.core.risk_control import default_risk_control
    if not default_risk_control.is_activity_time():
        return {
            "acted": False,
            "reason": "outside_activity_hours",
            "activity_window": f"{default_risk_control.activity_start.strftime('%H:%M')}-{default_risk_control.activity_end.strftime('%H:%M')} Asia/Taipei",
        }

    nav = navigate_to_openchat(customer_id, community_id, overall_timeout_seconds=20.0)
    if nav.get("status") != "ok":
        return {"acted": False, "reason": f"navigate_failed:{nav.get('reason')}"}

    try:
        community = load_community_config(customer_id, community_id)
    except Exception as exc:  # noqa: BLE001
        return {"acted": False, "reason": f"community_lookup_failed:{exc}"}

    try:
        messages = read_recent_chat(
            AdbClient(device_id=community.device_id),
            default_raw_xml_path(customer_id),
            limit=20,
        )
    except RuntimeError as exc:
        return {"acted": False, "reason": f"read_failed:{exc}"}

    from app.storage.watches import messages_signature
    new_sig = messages_signature(messages)
    if new_sig == last_signature:
        return {"acted": False, "reason": "no_new_content", "new_signature": new_sig}

    if last_draft and (time.time() - last_draft) < cooldown:
        return {"acted": False, "reason": "cooldown", "new_signature": new_sig}

    # Dedup: if the previous auto_watch draft is still pending, don't stack.
    pending_id, _ = _find_recent_auto_watch_review(customer_id, community_id)
    if pending_id is not None:
        return {
            "acted": False,
            "reason": f"prior_auto_watch_pending:{pending_id}",
            "new_signature": new_sig,
        }

    # Persona + fingerprints for the selector.
    try:
        from app.workflows.persona_context import get_persona_context
        from app.workflows.member_fingerprint import load_member_fingerprints
        persona = get_persona_context(customer_id, community_id)
        fingerprints = load_member_fingerprints(customer_id, community_id)
    except Exception as exc:  # noqa: BLE001
        return {"acted": False, "reason": f"context_load_failed:{exc}", "new_signature": new_sig}

    decision = select_reply_target_workflow(
        messages,
        operator_persona=persona,
        member_fingerprints=fingerprints,
    )
    decision_dict = decision.to_dict() if hasattr(decision, "to_dict") else dict(decision)
    target = decision_dict.get("target")
    if not target or not target.get("actionable"):
        return {
            "acted": False,
            "reason": decision_dict.get("skip_reason") or "no_actionable_target",
            "new_signature": new_sig,
            "selector_top_score": (target or {}).get("score"),
        }

    # Compose a draft via the rule-based decision module (LLM-aware: when
    # ECHO_LLM_ENABLED=true, decide_reply auto-routes through LLM).
    from app.ai.decision import decide_reply
    persona_text = (persona.get("voice_profile") or {}).get("personality_zh") or ""
    draft = decide_reply(
        messages=messages,
        persona_text=persona_text,
        community_name=community.display_name,
    )
    if draft.action != "draft_reply" or not draft.draft.strip():
        return {
            "acted": False,
            "reason": f"composer_skipped:{draft.reason}",
            "new_signature": new_sig,
            "selector_top_score": target.get("score"),
        }

    # Stage as pending review (mirror tool_compose_and_send shape).
    customer = load_customer_config(customer_id)
    review_id = f"watch-inproc-{int(time.time())}-{community_id}"
    record = ReviewRecord(
        review_id=review_id,
        source_job_id=review_id,
        customer_id=customer_id,
        customer_name=customer.display_name,
        community_id=community_id,
        community_name=community.display_name,
        device_id=community.device_id,
        draft_text=draft.draft,
        reason="mcp_compose:auto_watch",
        confidence=draft.confidence,
        status="pending",
    )
    review_store.upsert(record)
    append_audit_event(
        customer_id,
        "mcp_compose_review_created",
        {
            "review_id": review_id,
            "community_id": community_id,
            "text_preview": draft.draft[:60],
            "source": "auto_watch_inproc",
            "selector_score": target.get("score"),
            "selector_target_sender": target.get("sender"),
            "composer_source": draft.source,
        },
    )

    # Push Lark review card (best-effort).
    initiator_chat_id = watch.get("initiator_chat_id")
    if isinstance(initiator_chat_id, str) and initiator_chat_id:
        try:
            from app.lark.cards import build_review_card
            card = build_review_card(
                customer_name=customer.display_name,
                community_name=community.display_name,
                draft=draft.draft,
                job_id=review_id,
                customer_id=customer_id,
                community_id=community_id,
                device_id=community.device_id,
                reason="auto_watch_inproc",
                confidence=draft.confidence,
                draft_title="🛎 自動追蹤（in-process）— 待審核",
            )
            client = LarkClient()
            client.send_card(initiator_chat_id, card, receive_id_type="chat_id")
        except LarkClientError as exc:
            append_audit_event(
                customer_id,
                "watch_lark_notify_failed",
                {"watch_id": watch_id, "error": str(exc)[:200]},
            )

    append_audit_event(
        customer_id,
        "watch_tick_fired",
        {
            "watch_id": watch_id,
            "community_id": community_id,
            "codex_summary": f"in-process compose: {target.get('sender', '')} → score={target.get('score')}",
            "source": "inprocess",
        },
    )

    return {
        "acted": True,
        "new_signature": new_sig,
        "draft_epoch": time.time(),
        "codex_summary": f"in-process: composed for {target.get('sender', '')}",
    }


def _find_recent_auto_watch_review(customer_id: str, community_id: str) -> tuple[str | None, str | None]:
    """Match the existing helper in watch_tick.py — keep behavior parity."""

    from app.core.reviews import ACTIVE_REVIEW_STATUSES

    candidates = [
        r for r in review_store.list_all()
        if r.customer_id == customer_id
        and r.community_id == community_id
        and r.status in ACTIVE_REVIEW_STATUSES
        and (r.reason or "") in ("mcp_compose:auto_watch", "auto_watch_inproc", "mcp_compose")
    ]
    if not candidates:
        return None, None
    candidates.sort(key=lambda r: r.created_at, reverse=True)
    return candidates[0].review_id, candidates[0].draft_text
