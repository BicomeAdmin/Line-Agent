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

    # Load community first so we can honor its per-community activity-window
    # override before any ADB / navigate work. YAML read is cheap; navigate
    # to a chat we're not allowed to act on would be wasted I/O.
    try:
        community = load_community_config(customer_id, community_id)
    except Exception as exc:  # noqa: BLE001
        return {"acted": False, "reason": f"community_lookup_failed:{exc}"}

    from app.core.risk_control import community_is_in_activity_window, default_risk_control
    if not community_is_in_activity_window(community):
        start = community.activity_start_hour_tpe
        end = community.activity_end_hour_tpe
        if start is not None and end is not None:
            window_label = f"{start:02d}:00-{end:02d}:00 Asia/Taipei (community override)"
        else:
            window_label = f"{default_risk_control.activity_start.strftime('%H:%M')}-{default_risk_control.activity_end.strftime('%H:%M')} Asia/Taipei"
        return {
            "acted": False,
            "reason": "outside_activity_hours",
            "activity_window": window_label,
        }

    nav = navigate_to_openchat(customer_id, community_id, overall_timeout_seconds=20.0)
    if nav.get("status") != "ok":
        return {"acted": False, "reason": f"navigate_failed:{nav.get('reason')}"}

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

    # Compose a draft. Two backends:
    #   1. LLM (codex) — only when env ECHO_COMPOSE_BACKEND=codex AND
    #      community.llm_compose_enabled=true AND voice_profile is
    #      complete (frontmatter VCPVC + nickname + personality + off_limits).
    #      Otherwise falls through to rule-based.
    #   2. Rule-based — `app.ai.decision.decide_reply` (LLM-aware via
    #      Anthropic API path, kept dormant). Default.
    composer_source = "rule"
    composer_rationale = ""
    composer_off_limits_hit: str | None = None
    composer_lint: dict | None = None

    use_codex = False
    try:
        from app.ai.codex_compose import is_enabled as codex_enabled
        if codex_enabled() and getattr(community, "llm_compose_enabled", False):
            use_codex = True
    except Exception:  # noqa: BLE001 — never let composer-availability check kill the tick
        use_codex = False

    if use_codex:
        from app.ai.codex_compose import compose_via_codex, ComposerUnavailable
        from app.ai.voice_profile_v2 import parse_voice_profile
        from app.storage.paths import voice_profile_path

        vp = parse_voice_profile(customer_id, community_id, voice_profile_path(customer_id, community_id))
        target_fp_dict = None
        try:
            from app.workflows.member_fingerprint import get_member_fingerprint
            target_fp_dict = get_member_fingerprint(customer_id, community_id, str(target.get("sender") or ""))
        except Exception:  # noqa: BLE001 — fingerprint missing is OK, prompt has fallback
            target_fp_dict = None

        recent_self_posts = [
            str(p.get("text") or "")
            for p in (persona.get("recent_self_posts") or [])
            if isinstance(p, dict)
        ]

        try:
            output = compose_via_codex(
                voice_profile=vp,
                community_name=community.display_name,
                target_sender=str(target.get("sender") or ""),
                target_message=str(target.get("text") or ""),
                target_score=float(target.get("score") or 0.0),
                target_threshold=float(decision_dict.get("threshold") or 2.0),
                target_reasons=list(target.get("reasons") or []),
                target_fingerprint=target_fp_dict,
                thread_excerpt=messages[-8:],
                recent_self_posts=recent_self_posts,
            )
        except ComposerUnavailable as exc:
            append_audit_event(
                customer_id,
                "composer_codex_unavailable",
                {"community_id": community_id, "reason": str(exc)[:200]},
            )
            return {
                "acted": False,
                "reason": f"composer_unavailable:{str(exc)[:120]}",
                "new_signature": new_sig,
                "selector_top_score": target.get("score"),
            }

        composer_source = "codex"
        composer_rationale = output.rationale
        composer_off_limits_hit = output.off_limits_hit

        if not output.should_engage:
            append_audit_event(
                customer_id,
                "composer_codex_skipped",
                {
                    "community_id": community_id,
                    "rationale": output.rationale[:200],
                    "off_limits_hit": output.off_limits_hit,
                    "selector_target_sender": target.get("sender"),
                    "selector_score": target.get("score"),
                },
            )
            return {
                "acted": False,
                "reason": f"composer_skipped:llm_should_engage_false",
                "rationale": output.rationale,
                "new_signature": new_sig,
                "selector_top_score": target.get("score"),
            }

        # (lint gate runs uniformly below for both codex + rule-based)

        # Synthesize a DraftDecision-shaped object so downstream code is unchanged.
        from app.ai.decision import DraftDecision
        draft = DraftDecision(
            action="draft_reply",
            reason="codex_compose",
            confidence=output.confidence,
            draft=output.draft,
            source="codex",
        )
    else:
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

    # Universal lint gate — applies to BOTH codex and rule-based drafts.
    # Rule-based templates are known-stiff (advisor SOP register), so this
    # effectively pushes operators toward codex. The codex branch already
    # ran a lint above; we re-run here so that composer_lint is set on
    # both paths and the audit trail is uniform.
    from app.ai.draft_linter import score_draft as _lint
    final_lint = _lint(draft.draft)
    composer_lint = final_lint.to_dict()
    if final_lint.score < 60:
        append_audit_event(
            customer_id,
            "composer_lint_rejected",
            {
                "community_id": community_id,
                "score": final_lint.score,
                "verdict": final_lint.verdict,
                "issues": list(final_lint.issues),
                "draft_preview": (draft.draft or "")[:80],
                "composer_source": draft.source,
            },
        )
        return {
            "acted": False,
            "reason": f"composer_skipped:lint_low_score({final_lint.score}/{final_lint.verdict})",
            "lint": composer_lint,
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
            "composer_rationale": composer_rationale[:200] if composer_rationale else None,
            "composer_off_limits_hit": composer_off_limits_hit,
            "composer_lint_score": (composer_lint or {}).get("score"),
            "composer_lint_verdict": (composer_lint or {}).get("verdict"),
        },
    )

    # Push Lark review card (best-effort).
    initiator_chat_id = watch.get("initiator_chat_id")
    if isinstance(initiator_chat_id, str) and initiator_chat_id:
        try:
            from app.lark.cards import build_review_card
            card_reason = (
                f"codex: {composer_rationale}"[:200]
                if composer_source == "codex" and composer_rationale
                else "auto_watch_inproc"
            )
            card_title = (
                "🤖 LLM 擬稿（codex）— 待審核"
                if composer_source == "codex"
                else "🛎 自動追蹤（in-process）— 待審核"
            )
            card = build_review_card(
                customer_name=customer.display_name,
                community_name=community.display_name,
                draft=draft.draft,
                job_id=review_id,
                customer_id=customer_id,
                community_id=community_id,
                device_id=community.device_id,
                reason=card_reason,
                confidence=draft.confidence,
                draft_title=card_title,
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
