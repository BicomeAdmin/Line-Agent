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
    draft_text = _require_string(payload.get("draft_text"), "draft_text")
    pre_approved = bool(payload.get("pre_approved"))

    customer = load_customer_config(customer_id)
    community = next(
        c
        for c in load_communities_for_device(device_id)
        if c.community_id == community_id and c.customer_id == customer_id
    )

    job_id_for_review = payload.get("job_id") or post_id

    # Auto-send path: only when operator explicitly pre-approved AND global human-approval is off.
    if pre_approved and not settings.require_human_approval:
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
    review_card = build_review_card(
        customer_name=customer.display_name,
        community_name=community.display_name,
        draft=draft_text,
        job_id=str(job_id_for_review),
        customer_id=customer_id,
        community_id=community_id,
        device_id=device_id,
        reason="scheduled_post",
        confidence=None,
        draft_title="排程稿件待審核",
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
            "reason": "scheduled_post",
            "confidence": None,
            "draft": draft_text,
            "should_send": False,
            "source": "scheduled_post",
        },
        "review_card": review_card,
    }


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

    send_result = send_draft(customer_id, community_id, device_id, draft_text)
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
