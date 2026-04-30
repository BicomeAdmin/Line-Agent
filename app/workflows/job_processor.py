from __future__ import annotations

from app.adb.client import AdbClient
from app.adb.line_app import open_line
from app.config import settings
from app.core.audit import append_audit_event
from app.core.jobs import JobRecord, job_registry
from app.core.reviews import ReviewRecord, review_store
from app.lark.cards import build_review_card
from app.lark.client import LarkClient, LarkClientError
from app.lark.result_cards import build_job_error_card, build_job_result_card
from app.lark.status_cards import build_readiness_status_card, build_system_status_card
from app.storage.config_loader import get_device_config, load_communities_for_device, load_customer_config
from app.storage.paths import default_raw_xml_path
from app.workflows.action_queue import get_action_queue
from app.workflows.acceptance_status import get_acceptance_status
from app.workflows.calibration_status import get_calibration_status
from app.workflows.community_status import get_community_status
from app.workflows.device_status import get_device_status
from app.workflows.draft_reply import draft_reply_for_device
from app.workflows.line_apk_status import get_line_apk_status
from app.workflows.milestone_status import get_milestone_status
from app.workflows.device_recovery import ensure_device_ready
from app.workflows.line_install import install_line_app
from app.workflows.openchat_validation import validate_openchat_session
from app.workflows.patrol import patrol_community, patrol_device
from app.workflows.prepare_line_session import prepare_line_session
from app.workflows.project_snapshot import get_project_snapshot
from app.workflows.read_chat import read_recent_chat
from app.workflows.readiness_status import get_readiness_status
from app.workflows.send_reply import send_draft
from app.workflows.scheduled_posts import (
    get_post as get_scheduled_post,
    mark_post_reviewing,
    mark_post_sent,
    mark_post_skipped,
)
from app.workflows.system_status import get_system_status


def process_job(job: JobRecord) -> dict[str, object]:
    if job.job_type == "lark_command":
        result = _process_lark_command(job.payload)
    elif job.job_type == "lark_action":
        result = _process_lark_action(job.payload)
    elif job.job_type == "scheduled_patrol":
        result = _process_scheduled_patrol(job.payload)
    elif job.job_type == "scheduled_post":
        result = _process_scheduled_post(job.payload)
    else:
        raise RuntimeError(f"Unsupported job type: {job.job_type}")
    _sync_review_state(job, result)
    return result


def _process_lark_command(payload: dict[str, object]) -> dict[str, object]:
    command = payload.get("command")
    if not isinstance(command, dict):
        raise RuntimeError("Missing command payload.")

    action = command.get("action")
    customer_id = _customer_id_for_command(command)
    if action == "system_status":
        result = get_system_status()
    elif action == "readiness_status":
        result = get_readiness_status()
    elif action == "calibration_status":
        result = get_calibration_status()
    elif action == "community_status":
        result = get_community_status(customer_id=customer_id, community_id=command.get("community_id") if isinstance(command.get("community_id"), str) else None)
    elif action == "acceptance_status":
        result = get_acceptance_status(customer_id=customer_id, community_id=command.get("community_id") if isinstance(command.get("community_id"), str) else None)
    elif action == "prepare_line_session":
        device_id = _require_string(command.get("device_id"), "device_id")
        result = prepare_line_session(device_id)
    elif action == "ensure_device_ready":
        device_id = _require_string(command.get("device_id"), "device_id")
        result = ensure_device_ready(device_id)
    elif action == "install_line_app":
        device_id = _require_string(command.get("device_id"), "device_id")
        result = install_line_app(device_id)
    elif action == "line_apk_status":
        result = get_line_apk_status()
    elif action == "project_snapshot":
        result = get_project_snapshot(
            customer_id=customer_id,
            community_id=command.get("community_id") if isinstance(command.get("community_id"), str) else None,
        )
    elif action == "action_queue":
        result = get_action_queue(
            customer_id=customer_id,
            community_id=command.get("community_id") if isinstance(command.get("community_id"), str) else None,
        )
    elif action == "milestone_status":
        result = get_milestone_status(
            customer_id=customer_id,
            community_id=command.get("community_id") if isinstance(command.get("community_id"), str) else None,
        )
    elif action == "openchat_validation":
        result = validate_openchat_session(
            customer_id=customer_id,
            community_id=command.get("community_id") if isinstance(command.get("community_id"), str) else None,
        )
    elif action == "device_status":
        device_id = _require_string(command.get("device_id"), "device_id")
        result = get_device_status(device_id)
    elif action == "read_chat":
        device_id = _require_string(command.get("device_id"), "device_id")
        limit = int(command.get("limit", 10))
        messages = read_recent_chat(AdbClient(device_id=device_id), default_raw_xml_path(customer_id), limit=limit)
        result = {"status": "ok", "device_id": device_id, "messages": messages, "message_count": len(messages)}
    elif action == "open_line":
        device_id = _require_string(command.get("device_id"), "device_id")
        open_line(AdbClient(device_id=device_id))
        result = {"status": "ok", "device_id": device_id, "opened_package": "jp.naver.line.android"}
    elif action == "draft_reply":
        device_id = _require_string(command.get("device_id"), "device_id")
        community_id = command.get("community_id") if isinstance(command.get("community_id"), str) else None
        result = draft_reply_for_device(
            device_id,
            limit=int(command.get("limit", 20)),
            community_id=community_id,
        )
    elif action == "patrol_device":
        device_id = _require_string(command.get("device_id"), "device_id")
        result = patrol_device(device_id)
    elif action == "suggest":
        result = {
            "status": "pending_llm",
            "action": "suggest",
            "category": command.get("category", "general"),
            "note": "LLM drafting module not connected yet.",
        }
    else:
        raise RuntimeError(f"Unsupported action: {action}")

    append_audit_event(customer_id, "job_completed", {"action": action, "result": result})
    return result


def _process_lark_action(payload: dict[str, object]) -> dict[str, object]:
    action = _require_string(payload.get("action"), "action")
    job_id = _require_string(payload.get("job_id"), "job_id")
    source_job = job_registry.get(job_id)
    result = {"status": "acknowledged", "job_id": job_id, "action": action}
    if source_job is not None and source_job.result is not None:
        result["source_status"] = source_job.status
        if isinstance(source_job.result.get("decision"), dict):
            result["decision"] = source_job.result["decision"]
    if action == "send":
        result = _approve_send(job_id, source_job.result if source_job is not None else None, payload)
        # Capture approve as positive feedback signal (edit_feedback loop).
        _record_outcome_safe(job_id, action="approve")
    elif action == "ignore":
        result["status"] = "ignored"
        _update_review_from_action(job_id, payload, "ignored")
        # Capture ignore as negative signal — without this the system is
        # blind to bad drafts that operator silently dismissed (fixed
        # 2026-04-29 after selector mis-fire on openchat_002).
        _record_outcome_safe(job_id, action="ignore")
    elif action == "edit":
        edited_text = payload.get("edited_draft_text")
        if isinstance(edited_text, str) and edited_text.strip():
            edited = edited_text.strip()
            # Capture (original, edited) pair for the feedback loop —
            # Paul's AI Step 4「實時回饋優化」 in CLAUDE.md §0.5.5.
            try:
                from app.workflows.edit_feedback import record_edit
                review = review_store.get(job_id)
                if review is not None:
                    record_edit(
                        review.customer_id,
                        review.community_id,
                        job_id,
                        review.draft_text,  # the original BEFORE we overwrite below
                        edited,
                    )
            except Exception:  # noqa: BLE001 — feedback recording must never break edit flow
                pass
            _update_review_from_action(job_id, payload, "pending_reapproval", draft_text=edited)
            result = _prepare_edited_review(job_id, source_job.result if source_job is not None else None, payload, edited)
        else:
            _update_review_from_action(job_id, payload, "edit_required")
            result["status"] = "edit_required"
            result["draft_text"] = _draft_text_for_action(source_job.result if source_job is not None else None, payload)
    customer_id = _customer_id_for_action(payload, source_job.result if source_job is not None else None)
    append_audit_event(customer_id, "action_received", result)
    return result


def _process_scheduled_post(payload: dict[str, object]) -> dict[str, object]:
    customer_id = _require_string(payload.get("customer_id"), "customer_id")
    community_id = _require_string(payload.get("community_id"), "community_id")
    device_id = _require_string(payload.get("device_id"), "device_id")
    post_id = _require_string(payload.get("post_id"), "post_id")
    pre_approved = bool(payload.get("pre_approved"))
    compose_mode = bool(payload.get("compose_mode"))
    brief = (payload.get("brief") or "") if compose_mode else None
    send_at_iso = payload.get("send_at_iso")

    customer = load_customer_config(customer_id)
    community = next(
        c
        for c in load_communities_for_device(device_id)
        if c.community_id == community_id and c.customer_id == customer_id
    )

    job_id_for_review = payload.get("job_id") or post_id
    composer_source = "operator_text"
    composer_rationale = ""
    composer_confidence: float | None = None

    raw_draft = payload.get("draft_text")
    draft_text = (raw_draft or "").strip() if isinstance(raw_draft, str) else ""

    composer_off_limits_hash = ""
    if compose_mode and not draft_text:
        compose_result = _compose_brand_draft(
            customer_id=customer_id,
            community_id=community_id,
            community_display=community.display_name,
            brief=str(brief or ""),
            post_id=post_id,
        )
        if compose_result.get("status") != "composed":
            return compose_result
        draft_text = str(compose_result.get("draft") or "")
        composer_source = "codex_brand"
        composer_rationale = str(compose_result.get("rationale") or "")
        composer_confidence = compose_result.get("confidence")  # type: ignore[assignment]
        composer_off_limits_hash = str(compose_result.get("off_limits_hash") or "")

    if not draft_text:
        # Defensive: should be impossible after the above branches.
        mark_post_skipped(customer_id, community_id, post_id, reason="empty_draft")
        return {
            "status": "skipped",
            "scheduled_post_id": post_id,
            "customer_id": customer_id,
            "community_id": community_id,
            "device_id": device_id,
            "reason": "empty_draft",
        }

    # Auto-send path: only when operator explicitly pre-approved AND global human-approval is off.
    # Compose-mode drafts NEVER auto-send — even if pre_approved=true the LLM output should be
    # eyeballed before going live.
    if pre_approved and not settings.require_human_approval and not compose_mode:
        send_result = send_draft(customer_id, community_id, device_id, draft_text)
        if send_result.get("status") == "sent":
            mark_post_sent(customer_id, community_id, post_id, send_result=send_result)
        else:
            mark_post_skipped(customer_id, community_id, post_id, reason=f"send_status:{send_result.get('status')}")
        return {
            "status": send_result.get("status", "unknown"),
            "scheduled_post_id": post_id,
            "auto_sent": True,
            "send_result": send_result,
            "customer_id": customer_id,
            "customer_name": customer.display_name,
            "community_id": community_id,
            "community_name": community.display_name,
            "device_id": device_id,
        }

    # Default path: turn the scheduled draft into a review pending operator approval.
    if compose_mode and send_at_iso:
        from app.core.timezone import to_taipei_str
        title = f"🤖 LLM 排程擬稿（原訂送出 {to_taipei_str(str(send_at_iso))}）— 待審核"
    elif compose_mode:
        title = "🤖 LLM 排程擬稿 — 待審核"
    else:
        title = "排程稿件待審核"

    card_reason = (
        f"scheduled_compose: {composer_rationale}"[:200]
        if compose_mode and composer_rationale
        else "scheduled_post"
    )
    review_card = build_review_card(
        customer_name=customer.display_name,
        community_name=community.display_name,
        draft=draft_text,
        job_id=str(job_id_for_review),
        customer_id=customer_id,
        community_id=community_id,
        device_id=device_id,
        reason=card_reason,
        confidence=composer_confidence,
        draft_title=title,
    )
    mark_post_reviewing(customer_id, community_id, post_id, review_id=str(job_id_for_review))

    return {
        "status": "review_pending",
        "scheduled_post_id": post_id,
        "customer_id": customer_id,
        "customer_name": customer.display_name,
        "community_id": community_id,
        "community_name": community.display_name,
        "device_id": device_id,
        "decision": {
            "action": "draft_reply",
            "reason": card_reason,
            "confidence": composer_confidence,
            "draft": draft_text,
            "should_send": False,
            "source": composer_source if compose_mode else "scheduled_post",
            "off_limits_hash": composer_off_limits_hash,
        },
        "review_card": review_card,
    }


def _compose_brand_draft(
    *,
    customer_id: str,
    community_id: str,
    community_display: str,
    brief: str,
    post_id: str,
) -> dict[str, object]:
    """Run codex brand-compose for a scheduled post. Returns either:

    - {"status": "composed", "draft": ..., "rationale": ..., "confidence": ...}
    - {"status": "skipped", ...} (already wrote audit + marked post)
    """

    from app.ai.codex_compose import (
        ComposerUnavailable,
        compose_brand_post_via_codex,
        is_enabled as codex_enabled,
    )
    from app.ai.voice_profile_v2 import parse_voice_profile
    from app.storage.config_loader import load_community_config
    from app.storage.paths import voice_profile_path
    from app.workflows.persona_context import get_persona_context

    community = load_community_config(customer_id, community_id)

    if not codex_enabled():
        mark_post_skipped(customer_id, community_id, post_id, reason="codex_backend_disabled")
        append_audit_event(
            customer_id,
            "scheduled_post_compose_skipped",
            {"post_id": post_id, "community_id": community_id, "reason": "codex_backend_disabled"},
        )
        return {
            "status": "skipped",
            "scheduled_post_id": post_id,
            "customer_id": customer_id,
            "community_id": community_id,
            "reason": "codex_backend_disabled",
        }

    if not getattr(community, "llm_compose_enabled", False):
        mark_post_skipped(customer_id, community_id, post_id, reason="community_llm_compose_disabled")
        append_audit_event(
            customer_id,
            "scheduled_post_compose_skipped",
            {"post_id": post_id, "community_id": community_id, "reason": "community_llm_compose_disabled"},
        )
        return {
            "status": "skipped",
            "scheduled_post_id": post_id,
            "customer_id": customer_id,
            "community_id": community_id,
            "reason": "community_llm_compose_disabled",
        }

    # Bot-pattern guard — same block-at-10/day rule as watcher path.
    from app.workflows.bot_pattern_guard import assess_bot_pattern_risk
    bot_risk = assess_bot_pattern_risk(customer_id, community_id)
    if bot_risk.risk == "block":
        reason = f"bot_pattern_block:{bot_risk.daily_draft_count}_drafts_24h"
        mark_post_skipped(customer_id, community_id, post_id, reason=reason)
        append_audit_event(
            customer_id,
            "scheduled_post_compose_skipped",
            {
                "post_id": post_id,
                "community_id": community_id,
                "reason": reason,
                "bot_pattern": bot_risk.to_dict(),
            },
        )
        return {
            "status": "skipped",
            "scheduled_post_id": post_id,
            "customer_id": customer_id,
            "community_id": community_id,
            "reason": reason,
        }
    if bot_risk.risk == "warn":
        append_audit_event(
            customer_id,
            "scheduled_post_compose_bot_pattern_warning",
            {"post_id": post_id, "community_id": community_id, "verdict": bot_risk.to_dict()},
        )

    vp = parse_voice_profile(customer_id, community_id, voice_profile_path(customer_id, community_id))
    persona = get_persona_context(customer_id, community_id)
    recent_self_posts = [
        str(p.get("text") or "")
        for p in (persona.get("recent_self_posts") or [])
        if isinstance(p, dict)
    ]

    # Layer 3: read community recent chat at compose time so the LLM
    # can refuse a tone-deaf brand post if the group is currently hot
    # on an unrelated topic, or judge a quiet group as good seed time.
    # Best-effort — if navigation fails (device asleep, LINE crashed,
    # network timeout), fall back to empty thread + audit the gap.
    thread_excerpt = _read_thread_for_brand(customer_id, community_id, post_id)

    try:
        import time as _time
        output = compose_brand_post_via_codex(
            voice_profile=vp,
            community_name=community_display,
            brief=brief,
            thread_excerpt=thread_excerpt,
            recent_self_posts=recent_self_posts,
            now_epoch=_time.time(),
        )
    except ComposerUnavailable as exc:
        reason = f"composer_unavailable:{str(exc)[:120]}"
        mark_post_skipped(customer_id, community_id, post_id, reason=reason)
        append_audit_event(
            customer_id,
            "scheduled_post_compose_skipped",
            {"post_id": post_id, "community_id": community_id, "reason": reason},
        )
        return {
            "status": "skipped",
            "scheduled_post_id": post_id,
            "customer_id": customer_id,
            "community_id": community_id,
            "reason": reason,
        }

    if not output.should_engage:
        reason = f"composer_should_not_engage:{(output.rationale or '')[:120]}"
        mark_post_skipped(customer_id, community_id, post_id, reason=reason)
        append_audit_event(
            customer_id,
            "scheduled_post_compose_skipped",
            {
                "post_id": post_id,
                "community_id": community_id,
                "reason": reason,
                "off_limits_hit": output.off_limits_hit,
            },
        )
        return {
            "status": "skipped",
            "scheduled_post_id": post_id,
            "customer_id": customer_id,
            "community_id": community_id,
            "reason": reason,
        }

    # Race guard: codex compose can take 60-90s. Operator may have
    # cancelled the post via CLI / MCP / Lark in that window. If the
    # post is no longer in the `due` state, we drop the draft on the
    # floor — pushing it to review_store would create a ghost card
    # for a post the operator already retired.
    from app.workflows.scheduled_posts import get_post as _get_post
    current_post = _get_post(customer_id, community_id, post_id)
    current_status = (current_post or {}).get("status")
    if current_status not in {"due"}:
        append_audit_event(
            customer_id,
            "scheduled_post_compose_dropped_after_cancel",
            {
                "post_id": post_id,
                "community_id": community_id,
                "current_status": current_status,
                "draft_preview": (output.draft or "")[:80],
            },
        )
        return {
            "status": "skipped",
            "scheduled_post_id": post_id,
            "customer_id": customer_id,
            "community_id": community_id,
            "reason": f"post_status_changed_during_compose:{current_status}",
        }

    from app.core.reviews import hash_off_limits
    off_limits_hash = hash_off_limits(vp.off_limits)

    append_audit_event(
        customer_id,
        "scheduled_post_compose_succeeded",
        {
            "post_id": post_id,
            "community_id": community_id,
            "draft_preview": (output.draft or "")[:80],
            "rationale": (output.rationale or "")[:200],
            "confidence": output.confidence,
        },
    )
    return {
        "status": "composed",
        "draft": output.draft,
        "rationale": output.rationale,
        "confidence": output.confidence,
        "off_limits_hash": off_limits_hash,
    }


def _check_pre_send_drift(
    *,
    customer_id: str,
    community_id: str,
    device_id: str,
    existing_review,
) -> dict | None:
    """Verify the group context hasn't drifted since the review was composed.

    Returns a drift dict (with reason + diagnostics) if the send should
    be aborted, or None if it's safe to proceed. Failures of the chat
    read are non-fatal — when we can't see the chat we err on the side
    of letting the send through (operator already audited the draft;
    we don't want to block on best-effort signals).

    Two drift scenarios trigger abort:
    1. Review is >30min old AND chat is now hot on (apparently) other
       content — measured by community_temperature reading 熱絡.
    2. Review is >180min old AND chat has had non-self activity since
       the review was created (the original anchor is gone).
    """

    if existing_review is None:
        return None
    import time as _time
    from app.adb.client import AdbClient
    from app.ai.codex_compose import _community_temperature
    from app.storage.paths import default_raw_xml_path
    from app.workflows.read_chat import read_recent_chat

    review_created = float(getattr(existing_review, "created_at", 0) or 0)
    if review_created <= 0:
        return None
    age_seconds = _time.time() - review_created
    age_minutes = age_seconds / 60.0
    # Fresh reviews have no drift to check — operator just composed and
    # approved within the same coffee.
    if age_minutes <= 30:
        return None

    try:
        msgs = read_recent_chat(
            AdbClient(device_id=device_id),
            default_raw_xml_path(customer_id),
            limit=10,
        )
    except Exception as exc:  # noqa: BLE001 — drift read is best-effort
        append_audit_event(
            customer_id,
            "approve_send_drift_read_failed",
            {"community_id": community_id, "reason": str(exc)[:200]},
        )
        return None

    if not msgs:
        return None

    now = _time.time()
    temperature = _community_temperature(msgs, now)

    # Scenario 1: stale review + group now hot
    if age_minutes > 30 and temperature.startswith("熱絡"):
        return {
            "review_age_minutes": int(age_minutes),
            "reason": "stale_review_group_now_hot",
            "current_temperature": temperature,
        }

    # Scenario 2: very stale review + group has moved on since
    if age_minutes > 180:
        non_self_after_review = [
            m for m in msgs
            if not m.get("is_self")
            and isinstance(m.get("ts_epoch"), (int, float))
            and float(m["ts_epoch"]) > review_created
        ]
        if non_self_after_review:
            return {
                "review_age_minutes": int(age_minutes),
                "reason": "very_stale_review_chat_advanced",
                "current_temperature": temperature,
                "non_self_msgs_since_review": len(non_self_after_review),
            }

    return None


def _read_thread_for_brand(customer_id: str, community_id: str, post_id: str) -> list[dict]:
    """Best-effort read of the community's current chat tail so the
    brand-mode composer can assess temperature.

    Failures are non-fatal: navigation can fail (device asleep, LINE
    not foregrounded, etc.). When that happens we audit the gap and
    return an empty list — the prompt's '未知（無時間資訊）' branch
    instructs the LLM to be conservative.
    """

    try:
        from app.adb.client import AdbClient
        from app.storage.config_loader import load_community_config
        from app.storage.paths import default_raw_xml_path
        from app.workflows.openchat_navigate import navigate_to_openchat
        from app.workflows.read_chat import read_recent_chat

        community = load_community_config(customer_id, community_id)
        nav = navigate_to_openchat(customer_id, community_id, overall_timeout_seconds=20.0)
        if (nav or {}).get("status") != "ok":
            append_audit_event(
                customer_id,
                "scheduled_post_temperature_read_failed",
                {
                    "post_id": post_id,
                    "community_id": community_id,
                    "stage": "navigate",
                    "reason": (nav or {}).get("reason") or "unknown",
                },
            )
            return []
        # Cross-community guard before reading.
        from app.workflows.openchat_verify import verify_chat_title
        title_check = verify_chat_title(
            AdbClient(device_id=community.device_id),
            default_raw_xml_path(customer_id),
            community.display_name,
        )
        if not title_check.ok:
            append_audit_event(
                customer_id,
                "scheduled_post_temperature_read_failed",
                {
                    "post_id": post_id,
                    "community_id": community_id,
                    "stage": "title_verify",
                    "reason": f"chat_title_mismatch:{title_check.reason}",
                    "current_title": title_check.current_title,
                },
            )
            return []
        return read_recent_chat(
            AdbClient(device_id=community.device_id),
            default_raw_xml_path(customer_id),
            limit=10,
        )
    except Exception as exc:  # noqa: BLE001 — temperature read is best-effort
        append_audit_event(
            customer_id,
            "scheduled_post_temperature_read_failed",
            {
                "post_id": post_id,
                "community_id": community_id,
                "stage": "read",
                "reason": str(exc)[:200],
            },
        )
        return []


def _process_scheduled_patrol(payload: dict[str, object]) -> dict[str, object]:
    customer_id = _require_string(payload.get("customer_id"), "customer_id")
    community_id = _require_string(payload.get("community_id"), "community_id")
    device_id = _require_string(payload.get("device_id"), "device_id")
    community = next(
        community
        for community in load_communities_for_device(device_id)
        if community.community_id == community_id and community.customer_id == customer_id
    )
    result = patrol_community(community)
    append_audit_event(customer_id, "scheduled_patrol_processed", {"community_id": community_id, "device_id": device_id, "status": result.get("status")})
    return result


def _notify_lark(job: JobRecord, result: dict[str, object]) -> None:
    reply_target = job.payload.get("reply_target")
    if not isinstance(reply_target, dict):
        return
    receive_id = reply_target.get("receive_id")
    receive_id_type = reply_target.get("receive_id_type", "chat_id")
    if not isinstance(receive_id, str):
        return

    try:
        client = LarkClient()
        if isinstance(result.get("review_card"), dict):
            card = dict(result["review_card"])
            _inject_job_id(card, job.job_id)
            client.send_card(receive_id, card, receive_id_type=receive_id_type)
        elif isinstance(result.get("edited_review_card"), dict):
            card = dict(result["edited_review_card"])
            _inject_job_id(card, job.payload.get("job_id", job.job_id))
            client.send_card(receive_id, card, receive_id_type=receive_id_type)
        elif isinstance(result.get("reviews"), list) and result["reviews"]:
            review = result["reviews"][0]
            card = dict(review["review_card"])
            _inject_job_id(card, job.job_id)
            client.send_card(receive_id, card, receive_id_type=receive_id_type)
        elif isinstance(result.get("summary"), dict) and isinstance(result.get("global_checks"), list):
            client.send_card(receive_id, build_readiness_status_card(result), receive_id_type=receive_id_type)
        elif result.get("device_count") is not None:
            client.send_card(receive_id, build_system_status_card(result), receive_id_type=receive_id_type)
        else:
            client.send_card(receive_id, build_job_result_card(job.job_id, result), receive_id_type=receive_id_type)
    except LarkClientError as exc:
        append_audit_event(_customer_id_for_payload(job.payload), "lark_notify_failed", {"job_id": job.job_id, "error": str(exc)})


def notify_lark_error(job: JobRecord, error_message: str) -> None:
    reply_target = job.payload.get("reply_target")
    if not isinstance(reply_target, dict):
        return
    receive_id = reply_target.get("receive_id")
    receive_id_type = reply_target.get("receive_id_type", "chat_id")
    if not isinstance(receive_id, str):
        return

    try:
        LarkClient().send_card(receive_id, build_job_error_card(job.job_id, error_message), receive_id_type=receive_id_type)
    except LarkClientError:
        return


def _require_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"Missing required field: {field_name}")
    return value


def _customer_id_for_command(command: dict[str, object]) -> str:
    device_id = command.get("device_id")
    if isinstance(device_id, str) and device_id:
        return get_device_config(device_id).customer_id
    return "customer_a"


def _customer_id_for_payload(payload: dict[str, object]) -> str:
    command = payload.get("command")
    if isinstance(command, dict):
        return _customer_id_for_command(command)
    return "customer_a"


def _inject_job_id(card: dict[str, object], job_id: str) -> None:
    elements = card.get("elements")
    if not isinstance(elements, list):
        return
    for element in elements:
        if not isinstance(element, dict) or element.get("tag") != "action":
            continue
        actions = element.get("actions")
        if not isinstance(actions, list):
            continue
        for button in actions:
            if not isinstance(button, dict):
                continue
            value = button.get("value")
            if isinstance(value, dict):
                value["job_id"] = job_id


def _approve_send(job_id: str, source_result: dict[str, object] | None, action_payload: dict[str, object]) -> dict[str, object]:
    draft_text = _draft_text_for_action(source_result, action_payload)
    customer_id = _resolve_action_value("customer_id", source_result, action_payload)
    community_id = _resolve_action_value("community_id", source_result, action_payload)
    device_id = _resolve_action_value("device_id", source_result, action_payload)

    # Recall guard: if the operator unapproved this review between approve
    # and the job firing, the review is now `recalled`. Bail out before
    # navigate / send_draft so we don't push a draft the operator already
    # rejected. Race window is small (sub-second) but real.
    # Note: `review_id == job_id` per _update_review_from_action.
    from app.core.reviews import review_store
    existing = review_store.get(job_id)
    if existing is not None and existing.status == "recalled":
        append_audit_event(
            customer_id,
            "approve_send_aborted_recalled",
            {"job_id": job_id, "review_id": job_id, "community_id": community_id},
        )
        return {
            "status": "aborted",
            "job_id": job_id,
            "action": "send",
            "community_id": community_id,
            "device_id": device_id,
            "reason": "review_recalled",
        }

    # Preflight: navigate to the target chat before sending. Approval can land
    # minutes/hours after the original draft was prepared, so we don't trust
    # that LINE is still on the right room. Cheap insurance: re-navigate.
    from app.workflows.openchat_navigate import navigate_to_openchat

    nav_result = navigate_to_openchat(customer_id, community_id, overall_timeout_seconds=20.0)
    if nav_result.get("status") != "ok":
        append_audit_event(
            customer_id,
            "approve_send_navigate_blocked",
            {
                "job_id": job_id,
                "community_id": community_id,
                "nav_reason": nav_result.get("reason"),
            },
        )
        return {
            "status": "blocked",
            "job_id": job_id,
            "action": "send",
            "community_id": community_id,
            "device_id": device_id,
            "reason": "navigate_failed",
            "navigate_result": {k: nav_result.get(k) for k in ("status", "reason", "matched_title")},
        }

    # Cross-community contamination guard: post-navigate, pre-send.
    # If LINE foregrounded a different room between navigate and now,
    # typing the draft would land in the wrong community. Refuse.
    from app.adb.client import AdbClient
    from app.storage.config_loader import load_community_config as _load_comm
    from app.storage.paths import default_raw_xml_path
    from app.workflows.openchat_verify import verify_chat_title
    expected_community = _load_comm(customer_id, community_id)
    title_check = verify_chat_title(
        AdbClient(device_id=device_id),
        default_raw_xml_path(customer_id),
        expected_community.display_name,
    )
    if not title_check.ok:
        append_audit_event(
            customer_id,
            "approve_send_chat_title_mismatch",
            {
                "job_id": job_id,
                "review_id": job_id,
                "community_id": community_id,
                "expected": title_check.expected,
                "current_title": title_check.current_title,
                "reason": title_check.reason,
            },
        )
        return {
            "status": "blocked",
            "job_id": job_id,
            "action": "send",
            "community_id": community_id,
            "device_id": device_id,
            "reason": f"chat_title_mismatch:{title_check.reason}",
            "title_check": title_check.to_dict(),
        }

    # Off-limits drift warning: if operator edited voice_profile.off_limits
    # between compose and approve, the original safety budget the
    # composer was trained against may no longer match the current
    # rules. Audit-only (not block) — the operator already reviewed
    # the draft text; drift is a hint that they may want to re-check
    # whether the new rule applies. False positives are tolerable;
    # missed drift is the bigger risk.
    if existing is not None and existing.off_limits_hash:
        try:
            from app.ai.voice_profile_v2 import parse_voice_profile
            from app.core.reviews import hash_off_limits as _hash_off_limits
            from app.storage.paths import voice_profile_path as _vp_path
            current_vp = parse_voice_profile(
                customer_id, community_id, _vp_path(customer_id, community_id),
            )
            current_hash = _hash_off_limits(current_vp.off_limits)
            if current_hash and current_hash != existing.off_limits_hash:
                append_audit_event(
                    customer_id,
                    "approve_send_off_limits_drift",
                    {
                        "job_id": job_id,
                        "review_id": job_id,
                        "community_id": community_id,
                        "stored_hash": existing.off_limits_hash,
                        "current_hash": current_hash,
                    },
                )
        except Exception as exc:  # noqa: BLE001 — drift check is best-effort
            append_audit_event(
                customer_id,
                "approve_send_off_limits_drift_check_failed",
                {"job_id": job_id, "community_id": community_id, "error": str(exc)[:200]},
            )

    # Last-mile draft safety lint — even after operator review, certain
    # patterns (URLs, phone numbers, emails, payment refs) are blocked
    # outright. Operator's eye can miss subtle injections; this is the
    # belt before send.
    from app.ai.send_safety import audit_draft_for_send
    safety = audit_draft_for_send(draft_text or "")
    if safety.has_blocks:
        append_audit_event(
            customer_id,
            "send_safety_blocked",
            {
                "job_id": job_id,
                "review_id": job_id,
                "community_id": community_id,
                "verdict": safety.to_dict(),
                "draft_preview": (draft_text or "")[:80],
            },
        )
        return {
            "status": "blocked",
            "job_id": job_id,
            "action": "send",
            "community_id": community_id,
            "device_id": device_id,
            "reason": "send_safety_blocked",
            "safety_verdict": safety.to_dict(),
        }
    if safety.has_warns:
        append_audit_event(
            customer_id,
            "send_safety_warned",
            {
                "job_id": job_id,
                "review_id": job_id,
                "community_id": community_id,
                "verdict": safety.to_dict(),
                "draft_preview": (draft_text or "")[:80],
            },
        )

    # Temporal drift guard: if operator approves a stale review, the
    # group context may have shifted since the draft was composed.
    # Drift check: if review was created >30min ago AND a fresh read
    # of the chat shows the group has flipped state (was quiet, now
    # hot on something unrelated; or was a thread we joined, but it
    # has now moved on past our reply window), abort.
    drift = _check_pre_send_drift(
        customer_id=customer_id,
        community_id=community_id,
        device_id=device_id,
        existing_review=existing,
    )
    if drift is not None:
        append_audit_event(
            customer_id,
            "approve_send_aborted_temporal_drift",
            {
                "job_id": job_id,
                "review_id": job_id,
                "community_id": community_id,
                "review_age_minutes": drift["review_age_minutes"],
                "reason": drift["reason"],
                "current_temperature": drift.get("current_temperature"),
            },
        )
        return {
            "status": "aborted",
            "job_id": job_id,
            "action": "send",
            "community_id": community_id,
            "device_id": device_id,
            "reason": f"temporal_drift:{drift['reason']}",
            "drift_detail": drift,
        }

    send_result = send_draft(customer_id, community_id, device_id, draft_text)

    # Post-send verification: send_draft returning "sent" reflects the
    # ADB tap_type_send call's view of success, NOT a confirmed bubble
    # in the LINE chat. Re-read the chat and verify the operator's
    # most-recent self-bubble matches what we just typed. On mismatch,
    # surface a clear audit signal so the operator can investigate /
    # re-send manually — silently calling it "sent" when the message
    # never actually landed would be the worst outcome.
    if send_result.get("status") == "sent":
        from app.workflows.send_verification import verify_send
        from app.storage.paths import default_raw_xml_path
        try:
            send_verification = verify_send(
                AdbClient(device_id=device_id),
                default_raw_xml_path(customer_id),
                draft_text,
            )
        except Exception as exc:  # noqa: BLE001 — verification is best-effort
            send_verification = None
            append_audit_event(
                customer_id,
                "send_verification_error",
                {"job_id": job_id, "community_id": community_id, "error": str(exc)[:200]},
            )
        if send_verification is not None:
            if send_verification.ok:
                append_audit_event(
                    customer_id,
                    "send_verified",
                    {
                        "job_id": job_id,
                        "review_id": job_id,
                        "community_id": community_id,
                        "matched_text_preview": send_verification.matched_text[:60],
                    },
                )
            else:
                append_audit_event(
                    customer_id,
                    "send_verification_failed",
                    {
                        "job_id": job_id,
                        "review_id": job_id,
                        "community_id": community_id,
                        "verdict": send_verification.to_dict(),
                    },
                )
                # Demote status so callers / Lark cards reflect the unconfirmed state.
                send_result = dict(send_result)
                send_result["status"] = "sent_unconfirmed"
                send_result["verification"] = send_verification.to_dict()

    _update_review_from_action(job_id, action_payload, "sent", draft_text=draft_text)

    # If the original review came from a scheduled_post, close that post too.
    scheduled_post_id = None
    if isinstance(source_result, dict):
        scheduled_post_id = source_result.get("scheduled_post_id")
    if isinstance(scheduled_post_id, str) and scheduled_post_id:
        if send_result.get("status") == "sent":
            mark_post_sent(customer_id, community_id, scheduled_post_id, send_result=send_result)
        else:
            mark_post_skipped(
                customer_id,
                community_id,
                scheduled_post_id,
                reason=f"send_status:{send_result.get('status')}",
            )

    return {
        "status": "sent" if send_result.get("status") == "sent" else send_result.get("status"),
        "job_id": job_id,
        "action": "send",
        "community_id": community_id,
        "device_id": device_id,
        "send_result": send_result,
        "scheduled_post_id": scheduled_post_id,
    }


def _prepare_edited_review(
    job_id: str,
    source_result: dict[str, object] | None,
    action_payload: dict[str, object],
    edited_text: str,
) -> dict[str, object]:
    customer_id = _resolve_action_value("customer_id", source_result, action_payload)
    community_id = _resolve_action_value("community_id", source_result, action_payload)
    device_id = _resolve_action_value("device_id", source_result, action_payload)
    community_name = _resolve_optional_action_value("community_name", source_result, action_payload) or community_id
    customer_name = _resolve_optional_action_value("customer_name", source_result, action_payload) or customer_id
    decision = source_result.get("decision") if isinstance(source_result, dict) else None
    reason = decision.get("reason") if isinstance(decision, dict) else None
    confidence = decision.get("confidence") if isinstance(decision, dict) else None
    return {
        "status": "pending_reapproval",
        "job_id": job_id,
        "action": "edit",
        "edited_draft_text": edited_text,
        "edited_review_card": build_review_card(
            customer_name=customer_name,
            community_name=community_name,
            draft=edited_text,
            job_id=job_id,
            customer_id=customer_id,
            community_id=community_id,
            device_id=device_id,
            reason=reason if isinstance(reason, str) else None,
            confidence=float(confidence) if isinstance(confidence, (float, int)) else None,
            draft_title="人工修改後待二次審核",
        ),
    }


def _draft_text_for_action(source_result: dict[str, object] | None, action_payload: dict[str, object]) -> str:
    edited = action_payload.get("edited_draft_text")
    if isinstance(edited, str) and edited.strip():
        return edited.strip()
    draft = action_payload.get("draft_text")
    if isinstance(draft, str) and draft.strip():
        return draft.strip()
    if isinstance(source_result, dict):
        decision = source_result.get("decision")
        if isinstance(decision, dict):
            return _require_string(decision.get("draft"), "decision.draft")
    raise RuntimeError("Missing draft text for action.")


def _resolve_action_value(field_name: str, source_result: dict[str, object] | None, action_payload: dict[str, object]) -> str:
    direct = action_payload.get(field_name)
    if isinstance(direct, str) and direct:
        return direct
    if isinstance(source_result, dict):
        value = source_result.get(field_name)
        if isinstance(value, str) and value:
            return value
    raise RuntimeError(f"Missing required action field: {field_name}")


def _resolve_optional_action_value(field_name: str, source_result: dict[str, object] | None, action_payload: dict[str, object]) -> str | None:
    direct = action_payload.get(field_name)
    if isinstance(direct, str) and direct:
        return direct
    if isinstance(source_result, dict):
        value = source_result.get(field_name)
        if isinstance(value, str) and value:
            return value
    return None


def _customer_id_for_action(payload: dict[str, object], source_result: dict[str, object] | None) -> str:
    direct = payload.get("customer_id")
    if isinstance(direct, str) and direct:
        return direct
    if isinstance(source_result, dict):
        value = source_result.get("customer_id")
        if isinstance(value, str) and value:
            return value
    return "customer_a"


def _sync_review_state(job: JobRecord, result: dict[str, object]) -> None:
    if isinstance(result.get("review_card"), dict):
        record = _review_record_from_result(job.job_id, result)
        if record is not None:
            is_new = review_store.get(record.review_id) is None
            review_store.upsert(record)
            if is_new:
                append_audit_event(
                    record.customer_id,
                    "review_created",
                    {
                        "review_id": record.review_id,
                        "community_id": record.community_id,
                        "device_id": record.device_id,
                        "status": record.status,
                    },
                )
                _push_review_card_to_operator(record)
        return

    reviews = result.get("reviews")
    if isinstance(reviews, list):
        for item in reviews:
            if not isinstance(item, dict):
                continue
            record = _review_record_from_result(job.job_id, item)
            if record is not None:
                is_new = review_store.get(record.review_id) is None
                review_store.upsert(record)
                if is_new:
                    append_audit_event(
                        record.customer_id,
                        "review_created",
                        {
                            "review_id": record.review_id,
                            "community_id": record.community_id,
                            "device_id": record.device_id,
                            "status": record.status,
                        },
                    )
                    _push_review_card_to_operator(record)


def _push_review_card_to_operator(record: ReviewRecord) -> None:
    """Push the interactive review card to the operator's Lark chat.
    Lazy-imported so test code that mocks job_processor doesn't have to
    pull the Lark client transitively. Errors are swallowed inside the
    notifier — review still lives in store, operator can still act via
    CLI / dashboard."""

    try:
        from app.lark.notifier import notify_operator_of_new_review
    except ImportError:
        return
    notify_operator_of_new_review(record)


def _review_record_from_result(job_id: str, result: dict[str, object]) -> ReviewRecord | None:
    decision = result.get("decision")
    if not isinstance(decision, dict):
        return None
    draft_text = decision.get("draft")
    customer_id = result.get("customer_id")
    customer_name = result.get("customer_name")
    community_id = result.get("community_id")
    community_name = result.get("community_name")
    device_id = result.get("device_id")
    if not all(isinstance(value, str) and value for value in (draft_text, customer_id, customer_name, community_id, community_name, device_id)):
        return None
    return ReviewRecord(
        review_id=job_id,
        source_job_id=job_id,
        customer_id=customer_id,
        customer_name=customer_name,
        community_id=community_id,
        community_name=community_name,
        device_id=device_id,
        draft_text=draft_text,
        reason=decision.get("reason") if isinstance(decision.get("reason"), str) else None,
        confidence=float(decision["confidence"]) if isinstance(decision.get("confidence"), (int, float)) else None,
        status="pending",
        off_limits_hash=str(decision.get("off_limits_hash") or ""),
    )


def _record_outcome_safe(job_id: str, *, action: str) -> None:
    """Best-effort write of an approve/ignore signal to edit_feedback.
    Lazy-imports to match record_edit pattern + keeps the action flow
    insulated from feedback recording errors."""

    try:
        from app.workflows.edit_feedback import record_review_outcome
        review = review_store.get(job_id)
        if review is None or not review.draft_text:
            return
        record_review_outcome(
            review.customer_id,
            review.community_id,
            job_id,
            action=action,
            original_draft=review.draft_text,
        )
    except Exception:  # noqa: BLE001 — feedback recording must never break action flow
        pass


def _update_review_from_action(job_id: str, action_payload: dict[str, object], status: str, draft_text: str | None = None) -> None:
    review_id = job_id
    updated = review_store.update_status(
        review_id,
        status=status,
        updated_from_action=_require_string(action_payload.get("action"), "action"),
        draft_text=draft_text,
    )
    if updated is not None:
        append_audit_event(
            updated.customer_id,
            "review_status_changed",
            {
                "review_id": updated.review_id,
                "community_id": updated.community_id,
                "device_id": updated.device_id,
                "status": updated.status,
                "updated_from_action": updated.updated_from_action,
            },
        )
