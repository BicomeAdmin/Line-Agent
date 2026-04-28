"""Project Echo MCP server.

Exposes Project Echo's LINE-automation workflows as MCP tools so that an LLM
agent (OpenClaw / Claude / any MCP-aware client) can drive them.

All tools that produce side-effects on real user communities go through the
existing review pipeline; `require_human_approval=True` in
`configs/risk_control.yaml` is honored end-to-end. The LLM cannot bypass it.

Run via the standard MCP stdio transport:
    python3 scripts/project_echo_mcp_server.py
OpenClaw config:
    openclaw mcp set project_echo --json '{
        "command": "python3",
        "args": ["/Users/bicometech/Code/Line Agent/scripts/project_echo_mcp_server.py"]
    }'
"""

from __future__ import annotations

import json
import re
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from app.core.audit import append_audit_event
from app.core.jobs import job_registry
from app.core.reviews import ACTIVE_REVIEW_STATUSES, review_store
from app.lark.cards import build_review_card
from app.storage.config_loader import load_all_communities, load_community_config, load_customer_config
from app.workflows.acceptance_status import get_acceptance_status
from app.workflows.action_queue import get_action_queue
from app.workflows.analyze_chat import analyze_chat as analyze_chat_workflow
from app.workflows.community_onboarding import (
    add_community as add_community_workflow,
    refresh_community_title as refresh_community_title_workflow,
)
from app.workflows.style_harvest import harvest_style_samples as harvest_style_samples_workflow
from app.workflows.dashboard import collect_dashboard_data, format_text_report
from app.workflows.send_metrics import get_send_metrics
from app.storage.watches import (
    add_watch as watch_add,
    list_watches as watch_list,
    stop_watch as watch_stop,
)
from app.workflows.community_status import get_community_status
from app.workflows.openchat_navigate import navigate_to_openchat
from app.workflows.openchat_validation import validate_openchat_session
from app.workflows.project_snapshot import get_project_snapshot
from app.workflows.read_chat import read_recent_chat
from app.workflows.scheduled_posts import (
    add_scheduled_post,
    cancel_scheduled_post,
    list_all_scheduled_posts,
)
from app.workflows.scheduled_post_status import get_scheduled_post_status
from app.workflows.send_reply import send_draft
from app.storage.voice_profiles import (
    append_voice_sample as voice_append_sample,
    get_voice_profile as voice_get_profile,
    list_voice_profiles as voice_list_profiles,
    set_voice_profile as voice_set_profile,
)

# ----------------------------------------------------------------------------
# Tool implementations
# ----------------------------------------------------------------------------

INVITE_URL_RE = re.compile(r"https?://line\.me/ti/g2/([A-Za-z0-9_-]+)|line://ti/g2/([A-Za-z0-9_-]+)")


def _ok(payload: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", **payload}


def _error(reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": "error", "reason": reason, **extra}


def tool_list_communities() -> dict[str, Any]:
    items = []
    for c in load_all_communities():
        items.append(
            {
                "customer_id": c.customer_id,
                "community_id": c.community_id,
                "display_name": c.display_name,
                "device_id": c.device_id,
                "enabled": c.enabled,
                "patrol_interval_minutes": c.patrol_interval_minutes,
                "coordinates_ready": all(v is not None for v in (c.input_x, c.input_y, c.send_x, c.send_y)),
                "invite_url": c.invite_url,
                "group_id": c.group_id,
            }
        )
    return _ok({"count": len(items), "communities": items})


def tool_community_status(customer_id: str | None = None, community_id: str | None = None) -> dict[str, Any]:
    return get_community_status(customer_id=customer_id, community_id=community_id)


def tool_acceptance_status(customer_id: str | None = None, community_id: str | None = None) -> dict[str, Any]:
    return get_acceptance_status(customer_id=customer_id, community_id=community_id)


def tool_project_snapshot(customer_id: str | None = None, community_id: str | None = None) -> dict[str, Any]:
    return get_project_snapshot(customer_id=customer_id, community_id=community_id)


def tool_action_queue(customer_id: str | None = None, community_id: str | None = None) -> dict[str, Any]:
    return get_action_queue(customer_id=customer_id, community_id=community_id)


def tool_navigate_to_openchat(community_id: str, customer_id: str | None = None) -> dict[str, Any]:
    customer_id = customer_id or _default_customer_for_community(community_id)
    if customer_id is None:
        return _error("community_not_found", community_id=community_id)
    return navigate_to_openchat(customer_id, community_id)


def tool_read_recent_chat(community_id: str, customer_id: str | None = None, limit: int = 20) -> dict[str, Any]:
    customer_id = customer_id or _default_customer_for_community(community_id)
    if customer_id is None:
        return _error("community_not_found", community_id=community_id)
    community = load_community_config(customer_id, community_id)

    # Always navigate first so we read the right room.
    nav = navigate_to_openchat(customer_id, community_id, overall_timeout_seconds=20.0)
    if nav.get("status") != "ok":
        return _error("navigate_failed", nav_reason=nav.get("reason"))

    from app.adb.client import AdbClient
    from app.storage.paths import default_raw_xml_path

    try:
        messages = read_recent_chat(
            AdbClient(device_id=community.device_id),
            default_raw_xml_path(customer_id),
            limit=limit,
        )
    except RuntimeError as exc:
        return _error("read_failed", detail=str(exc))
    return _ok({"community_id": community_id, "message_count": len(messages), "messages": messages})


def tool_compose_and_send(
    community_id: str,
    text: str,
    customer_id: str | None = None,
    *,
    auto_approve: bool = False,
    note: str | None = None,
    source: str = "operator",
) -> dict[str, Any]:
    """Stage an LLM-composed message as a pending review.

    By default, this DOES NOT auto-send. The message lands in `review_store`
    as a pending review; the operator approves via Lark card OR `approve_review`
    tool. Setting `auto_approve=True` only takes effect when global
    `require_human_approval=False` (currently always False — sacred config).
    """

    text = (text or "").strip()
    if not text:
        return _error("empty_text")

    customer_id = customer_id or _default_customer_for_community(community_id)
    if customer_id is None:
        return _error("community_not_found", community_id=community_id)

    community = load_community_config(customer_id, community_id)
    customer = load_customer_config(customer_id)

    # Synthesize a job-style identity so the existing review_store / approval
    # pathway can pick this up. The "job_id" used as review_id is generated here
    # from the registry to keep the audit trail unified with other origin paths.
    # `source` distinguishes operator-initiated (default) from auto_watch (Phase 2)
    # so the metrics workflow can break stats down by trigger.
    job = job_registry.enqueue(
        "mcp_compose",
        {
            "customer_id": customer_id,
            "community_id": community_id,
            "device_id": community.device_id,
            "draft_text": text,
            "source": source,
            "note": note,
        },
    )
    job_id = job.job_id
    job.payload["job_id"] = job_id

    review_card = build_review_card(
        customer_name=customer.display_name,
        community_name=community.display_name,
        draft=text,
        job_id=job_id,
        customer_id=customer_id,
        community_id=community_id,
        device_id=community.device_id,
        reason=f"mcp_compose:{source}",
        confidence=None,
        draft_title="LLM 生成稿件待審核",
    )

    # Use the standard ReviewRecord shape so existing list/approve/edit/ignore
    # tooling treats it identically.
    from app.core.reviews import ReviewRecord

    record = ReviewRecord(
        review_id=job_id,
        source_job_id=job_id,
        customer_id=customer_id,
        customer_name=customer.display_name,
        community_id=community_id,
        community_name=community.display_name,
        device_id=community.device_id,
        draft_text=text,
        reason=f"mcp_compose:{source}",
        confidence=None,
        status="pending",
    )
    review_store.upsert(record)
    append_audit_event(
        customer_id,
        "mcp_compose_review_created",
        {
            "review_id": job_id,
            "community_id": community_id,
            "text_preview": text[:60],
            "note": note,
            "source": source,
        },
    )

    # Push an interactive review card to the operator's main Lark chat.
    # The card carries [通過/修改/忽略] buttons so the operator can act
    # without typing review_ids back. No-op when OPERATOR_DAILY_DIGEST_CHAT_ID
    # isn't set; for the auto_watch path, watch_tick already pushes its own
    # card to the watch's initiator_chat_id, so we skip there to avoid
    # double-firing on the same review.
    if source != "auto_watch":
        from app.lark.notifier import notify_operator_of_new_review
        notify_operator_of_new_review(record)

    # Mark the job as completed so when the operator approves, _approve_send
    # finds the source result on this job and routes correctly.
    job_registry.complete(
        job_id,
        result={
            "status": "review_pending",
            "customer_id": customer_id,
            "customer_name": customer.display_name,
            "community_id": community_id,
            "community_name": community.display_name,
            "device_id": community.device_id,
            "decision": {
                "action": "draft_reply",
                "reason": "mcp_compose",
                "confidence": None,
                "draft": text,
                "should_send": False,
                "source": "mcp",
            },
            "review_card": review_card,
        },
    )

    return _ok(
        {
            "review_id": job_id,
            "community_id": community_id,
            "community_name": community.display_name,
            "draft_preview": text[:80],
            "status_hint": "review_pending — operator must approve via approve_review or Lark card",
        }
    )


def tool_list_pending_reviews(community_id: str | None = None) -> dict[str, Any]:
    items = []
    for record in review_store.list_all():
        if record.status not in ACTIVE_REVIEW_STATUSES:
            continue
        if community_id and record.community_id != community_id:
            continue
        items.append(record.to_dict())
    items.sort(key=lambda r: r.get("created_at", 0))
    return _ok({"count": len(items), "reviews": items})


def tool_approve_review(review_id: str) -> dict[str, Any]:
    """Approve a pending review. Triggers pre-send navigate + real send_draft."""

    record = review_store.get(review_id)
    if record is None:
        return _error("review_not_found", review_id=review_id)
    if record.status not in ACTIVE_REVIEW_STATUSES:
        return _error("review_not_active", current_status=record.status)

    # Pre-send navigate insurance.
    nav = navigate_to_openchat(record.customer_id, record.community_id, overall_timeout_seconds=20.0)
    if nav.get("status") != "ok":
        return _error("navigate_failed", nav_reason=nav.get("reason"))

    send_result = send_draft(
        record.customer_id,
        record.community_id,
        record.device_id,
        record.draft_text,
    )
    if send_result.get("status") == "sent":
        review_store.update_status(review_id, status="sent", updated_from_action="mcp_approve")
        append_audit_event(
            record.customer_id,
            "review_status_changed",
            {
                "review_id": review_id,
                "community_id": record.community_id,
                "status": "sent",
                "updated_from_action": "mcp_approve",
            },
        )
        return _ok(
            {
                "review_id": review_id,
                "community_id": record.community_id,
                "send_status": send_result.get("status"),
                "delay_seconds": send_result.get("delay_seconds"),
            }
        )
    return _error("send_failed", send_result=send_result)


def tool_ignore_review(review_id: str, reason: str = "operator_ignored") -> dict[str, Any]:
    record = review_store.get(review_id)
    if record is None:
        return _error("review_not_found", review_id=review_id)
    review_store.update_status(review_id, status="ignored", updated_from_action="mcp_ignore")
    append_audit_event(
        record.customer_id,
        "review_status_changed",
        {
            "review_id": review_id,
            "community_id": record.community_id,
            "status": "ignored",
            "updated_from_action": "mcp_ignore",
            "reason": reason,
        },
    )
    return _ok({"review_id": review_id, "status": "ignored"})


def tool_resolve_invite_url(url: str) -> dict[str, Any]:
    """Map a Lark/LINE invite URL to a known Project Echo community_id.

    Critical for natural-language commands like:
        "去這個群幫我說一下早安 https://line.me/ti/g2/cRJp..."
    The LLM should call this first to find the matching community.
    """

    match = INVITE_URL_RE.search(url or "")
    if not match:
        return _error("not_a_line_invite_url", url=url)
    group_id = match.group(1) or match.group(2)
    for c in load_all_communities():
        if c.group_id and c.group_id == group_id:
            return _ok(
                {
                    "matched": True,
                    "customer_id": c.customer_id,
                    "community_id": c.community_id,
                    "display_name": c.display_name,
                    "group_id": group_id,
                }
            )
    return _ok({"matched": False, "group_id": group_id, "hint": "no community config has this group_id; add invite_url + group_id to a community yaml"})


def tool_list_scheduled_posts(community_id: str | None = None) -> dict[str, Any]:
    items = list_all_scheduled_posts()
    if community_id:
        items = [p for p in items if p.get("community_id") == community_id]
    return _ok({"count": len(items), "posts": items})


def tool_scheduled_post_status(community_id: str | None = None) -> dict[str, Any]:
    return get_scheduled_post_status(community_id=community_id)


def tool_add_scheduled_post(
    community_id: str,
    send_at: str,
    text: str,
    customer_id: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    customer_id = customer_id or _default_customer_for_community(community_id)
    if customer_id is None:
        return _error("community_not_found", community_id=community_id)
    try:
        return _ok({"post": add_scheduled_post(customer_id, community_id, send_at, text, notes=note)})
    except ValueError as exc:
        return _error("invalid_input", detail=str(exc))


def tool_cancel_scheduled_post(
    community_id: str,
    post_id: str,
    customer_id: str | None = None,
    reason: str = "operator_cancelled",
) -> dict[str, Any]:
    customer_id = customer_id or _default_customer_for_community(community_id)
    if customer_id is None:
        return _error("community_not_found", community_id=community_id)
    updated = cancel_scheduled_post(customer_id, community_id, post_id, reason=reason)
    if updated is None:
        return _error("post_not_found", post_id=post_id)
    return _ok({"post": updated})


def tool_validate_openchat(community_id: str | None = None) -> dict[str, Any]:
    return validate_openchat_session(community_id=community_id)


def tool_send_stats(
    customer_id: str | None = None,
    since_hours: float = 24.0,
    community_id: str | None = None,
) -> dict[str, Any]:
    cid = customer_id or "customer_a"
    return get_send_metrics(cid, since_hours=since_hours, community_id=community_id)


def tool_list_recent_auto_fires(
    customer_id: str | None = None,
    since_hours: float = 24.0,
) -> dict[str, Any]:
    cid = customer_id or "customer_a"
    metrics = get_send_metrics(cid, since_hours=since_hours)
    return _ok({
        "since_hours": since_hours,
        "auto_fires": metrics.get("auto_fires", []),
    })


def tool_start_watch(
    community_id: str,
    duration_minutes: int = 60,
    customer_id: str | None = None,
    initiator_chat_id: str | None = None,
    cooldown_seconds: int = 300,
    poll_interval_seconds: int = 60,
    note: str | None = None,
) -> dict[str, Any]:
    customer_id = customer_id or _default_customer_for_community(community_id)
    if customer_id is None:
        return _error("community_not_found", community_id=community_id)
    try:
        record = watch_add(
            customer_id,
            community_id,
            duration_minutes=duration_minutes,
            initiator_chat_id=initiator_chat_id,
            cooldown_seconds=cooldown_seconds,
            poll_interval_seconds=poll_interval_seconds,
            note=note,
        )
    except ValueError as exc:
        return _error("invalid_input", detail=str(exc))
    return _ok({"watch": record})


def tool_stop_watch(
    customer_id: str | None = None,
    watch_id: str | None = None,
    community_id: str | None = None,
    reason: str = "operator_stopped",
) -> dict[str, Any]:
    if customer_id is None and community_id is not None:
        customer_id = _default_customer_for_community(community_id)
    customer_id = customer_id or "customer_a"
    stopped = watch_stop(customer_id, watch_id=watch_id, community_id=community_id, reason=reason)
    return _ok({"stopped": stopped})


def tool_list_watches(customer_id: str | None = None, only_active: bool = True) -> dict[str, Any]:
    cid = customer_id or "customer_a"
    return _ok({"customer_id": cid, "watches": watch_list(cid, only_active=only_active)})


def tool_add_community(
    invite_url: str,
    customer_id: str | None = None,
    device_id: str | None = None,
    display_name: str | None = None,
    patrol_interval_minutes: int = 720,
    persona: str = "default",
) -> dict[str, Any]:
    return add_community_workflow(
        invite_url=invite_url,
        customer_id=customer_id or "customer_a",
        device_id=device_id,
        display_name=display_name,
        patrol_interval_minutes=patrol_interval_minutes,
        persona=persona,
    )


def tool_get_status_digest(
    customer_id: str | None = None,
    compact: bool = True,
) -> dict[str, Any]:
    cid = customer_id or "customer_a"
    data = collect_dashboard_data(cid)
    return _ok({
        "customer_id": cid,
        "text": format_text_report(data, compact=compact),
        "data": data,
    })


def tool_harvest_style_samples(
    community_id: str,
    customer_id: str | None = None,
    limit: int = 200,
    top_n: int = 30,
    skip_navigate: bool = False,
) -> dict[str, Any]:
    customer_id = customer_id or _default_customer_for_community(community_id) or "customer_a"
    return harvest_style_samples_workflow(
        customer_id,
        community_id,
        limit=limit,
        top_n=top_n,
        skip_navigate=skip_navigate,
    )


def tool_refresh_community_title(
    community_id: str,
    customer_id: str | None = None,
    display_name: str | None = None,
) -> dict[str, Any]:
    customer_id = customer_id or _default_customer_for_community(community_id) or "customer_a"
    return refresh_community_title_workflow(
        customer_id,
        community_id,
        display_name=display_name,
    )


def tool_analyze_chat(
    community_id: str,
    customer_id: str | None = None,
    limit: int = 20,
    skip_navigate: bool = False,
) -> dict[str, Any]:
    """Watcher Mode Phase 1: read + classify a community's recent chat for the LLM brain."""

    customer_id = customer_id or _default_customer_for_community(community_id)
    if customer_id is None:
        return _error("community_not_found", community_id=community_id)
    return analyze_chat_workflow(
        customer_id,
        community_id,
        limit=limit,
        skip_navigate=skip_navigate,
    )


def tool_get_voice_profile(community_id: str, customer_id: str | None = None) -> dict[str, Any]:
    customer_id = customer_id or _default_customer_for_community(community_id)
    if customer_id is None:
        return _error("community_not_found", community_id=community_id)
    return voice_get_profile(customer_id, community_id)


def tool_set_voice_profile(
    community_id: str,
    content: str,
    customer_id: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    customer_id = customer_id or _default_customer_for_community(community_id)
    if customer_id is None:
        return _error("community_not_found", community_id=community_id)
    return voice_set_profile(customer_id, community_id, content, note=note)


def tool_append_voice_sample(
    community_id: str,
    sample_text: str,
    customer_id: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    customer_id = customer_id or _default_customer_for_community(community_id)
    if customer_id is None:
        return _error("community_not_found", community_id=community_id)
    return voice_append_sample(customer_id, community_id, sample_text, note=note)


def tool_list_voice_profiles(customer_id: str | None = None) -> dict[str, Any]:
    cid = customer_id or "customer_a"
    return _ok({"customer_id": cid, "profiles": voice_list_profiles(cid)})


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------

def _default_customer_for_community(community_id: str) -> str | None:
    for c in load_all_communities():
        if c.community_id == community_id:
            return c.customer_id
    return None


# ----------------------------------------------------------------------------
# MCP server wiring
# ----------------------------------------------------------------------------

# Tool definitions (exposed to the LLM). Order matters for prompt readability —
# put high-frequency operations first.
TOOL_DEFINITIONS: list[tuple[str, str, dict[str, Any], Any]] = [
    (
        "list_communities",
        "List all configured Project Echo communities (LINE OpenChats) with their device, calibration status, and invite metadata.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        lambda **_: tool_list_communities(),
    ),
    (
        "resolve_invite_url",
        "Map a LINE invite URL (https://line.me/ti/g2/<id> or line://ti/g2/<id>) to a known community_id. Use this first when the user pastes a URL so you know which community to act on.",
        {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "The pasted LINE/Lark invite URL."}},
            "required": ["url"],
            "additionalProperties": False,
        },
        lambda url, **_: tool_resolve_invite_url(url),
    ),
    (
        "community_status",
        "Get a single community's runtime status (calibration, last patrol, last validation, last send).",
        {
            "type": "object",
            "properties": {
                "community_id": {"type": "string"},
                "customer_id": {"type": "string"},
            },
            "required": ["community_id"],
            "additionalProperties": False,
        },
        lambda community_id, customer_id=None, **_: tool_community_status(customer_id=customer_id, community_id=community_id),
    ),
    (
        "acceptance_status",
        "Run the acceptance checklist for a community and report stage (line_missing / line_not_openchat / chat_not_readable / send_not_calibrated / ready_for_hil).",
        {
            "type": "object",
            "properties": {
                "community_id": {"type": "string"},
                "customer_id": {"type": "string"},
            },
            "required": ["community_id"],
            "additionalProperties": False,
        },
        lambda community_id, customer_id=None, **_: tool_acceptance_status(customer_id=customer_id, community_id=community_id),
    ),
    (
        "project_snapshot",
        "Big-picture status: blockers, current phase, milestones, action queue, scheduled-post counts, recent audit highlights.",
        {
            "type": "object",
            "properties": {
                "community_id": {"type": "string"},
                "customer_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
        lambda community_id=None, customer_id=None, **_: tool_project_snapshot(customer_id=customer_id, community_id=community_id),
    ),
    (
        "navigate_to_openchat",
        "Navigate the connected LINE emulator into the target OpenChat. Uses deep link if invite_url/group_id is configured, else search by display_name.",
        {
            "type": "object",
            "properties": {
                "community_id": {"type": "string"},
                "customer_id": {"type": "string"},
            },
            "required": ["community_id"],
            "additionalProperties": False,
        },
        lambda community_id, customer_id=None, **_: tool_navigate_to_openchat(community_id=community_id, customer_id=customer_id),
    ),
    (
        "read_recent_chat",
        "Navigate into the community then read recent message UI text. Use this to understand current room context before composing.",
        {
            "type": "object",
            "properties": {
                "community_id": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
                "customer_id": {"type": "string"},
            },
            "required": ["community_id"],
            "additionalProperties": False,
        },
        lambda community_id, limit=20, customer_id=None, **_: tool_read_recent_chat(community_id=community_id, limit=limit, customer_id=customer_id),
    ),
    (
        "compose_and_send",
        "Stage a message for human review. The text lands as a pending review_card; the operator must call approve_review (or tap Lark Approve) before anything is sent. NEVER bypasses human approval. Pass `source` so metrics can break stats down: 'operator' (default — direct user request), 'auto_watch' (called from a Phase-2 watch tick), 'scheduled_post' (auto-fired from add_scheduled_post).",
        {
            "type": "object",
            "properties": {
                "community_id": {"type": "string"},
                "text": {"type": "string", "description": "The exact message to send. Use Traditional Chinese (zh-TW) for Taiwan communities."},
                "customer_id": {"type": "string"},
                "note": {"type": "string", "description": "Optional internal note for audit trail (not sent)."},
                "source": {"type": "string", "enum": ["operator", "auto_watch", "scheduled_post"], "default": "operator"},
            },
            "required": ["community_id", "text"],
            "additionalProperties": False,
        },
        lambda community_id, text, customer_id=None, note=None, source="operator", **_: tool_compose_and_send(community_id=community_id, text=text, customer_id=customer_id, note=note, source=source),
    ),
    (
        "list_pending_reviews",
        "List all reviews currently waiting on operator approval.",
        {
            "type": "object",
            "properties": {"community_id": {"type": "string"}},
            "additionalProperties": False,
        },
        lambda community_id=None, **_: tool_list_pending_reviews(community_id=community_id),
    ),
    (
        "approve_review",
        "Approve a pending review. Re-navigates to the target chat then sends the draft for real. Operator-authoritative tool — only call when the human explicitly says approve/通過/送出.",
        {
            "type": "object",
            "properties": {"review_id": {"type": "string"}},
            "required": ["review_id"],
            "additionalProperties": False,
        },
        lambda review_id, **_: tool_approve_review(review_id=review_id),
    ),
    (
        "ignore_review",
        "Dismiss a pending review without sending. Use when operator says ignore/駁回/忽略/跳過.",
        {
            "type": "object",
            "properties": {
                "review_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["review_id"],
            "additionalProperties": False,
        },
        lambda review_id, reason="operator_ignored", **_: tool_ignore_review(review_id=review_id, reason=reason),
    ),
    (
        "validate_openchat",
        "Verify LINE is currently focused on the target OpenChat by inspecting on-screen UI text. Useful as a sanity check before/after navigation.",
        {
            "type": "object",
            "properties": {"community_id": {"type": "string"}},
            "additionalProperties": False,
        },
        lambda community_id=None, **_: tool_validate_openchat(community_id=community_id),
    ),
    (
        "send_stats",
        "Aggregate send-pipeline metrics for the operator: drafts created / sent / ignored / pending, broken down by community AND by source (operator / auto_watch / scheduled_post). Includes recent send_attempts and avg compose-to-send latency. Use when operator says 「最近發了多少」「哪些是自動發的」「成功率多少」「stats」「統計一下」.",
        {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "since_hours": {"type": "number", "default": 24, "description": "Window. 24 = last day, 168 = last week."},
                "community_id": {"type": "string", "description": "Optional filter."},
            },
            "additionalProperties": False,
        },
        lambda customer_id=None, since_hours=24, community_id=None, **_: tool_send_stats(customer_id=customer_id, since_hours=since_hours, community_id=community_id),
    ),
    (
        "list_recent_auto_fires",
        "List recent Watcher Phase 2 auto-fires (when daemon spawned codex on its own to draft a reply). Shows fire time, community, codex's summary, and the linked review with current status. Use when operator says 「最近自動寫了什麼」「watcher 抓到什麼」「auto_watch 紀錄」.",
        {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "since_hours": {"type": "number", "default": 24},
            },
            "additionalProperties": False,
        },
        lambda customer_id=None, since_hours=24, **_: tool_list_recent_auto_fires(customer_id=customer_id, since_hours=since_hours),
    ),
    (
        "start_watch",
        "Watcher Phase 2: start a time-boxed auto-watch on a community. The scheduler daemon polls the community every poll_interval_seconds, and when new replies are detected a Codex turn fires that may auto-compose (still review-gated). Use when operator says 「幫我追蹤 X 群 / 盯一下 X 群 / 有人回覆再幫我接」. Pass `initiator_chat_id` (the operator's Lark chat_id) so notifications can be pushed back when drafts get composed.",
        {
            "type": "object",
            "properties": {
                "community_id": {"type": "string"},
                "duration_minutes": {"type": "integer", "default": 60},
                "customer_id": {"type": "string"},
                "initiator_chat_id": {"type": "string", "description": "Lark chat_id to push notification on draft."},
                "cooldown_seconds": {"type": "integer", "default": 300, "description": "Min seconds between auto-drafts to avoid spam."},
                "poll_interval_seconds": {"type": "integer", "default": 60},
                "note": {"type": "string"},
            },
            "required": ["community_id"],
            "additionalProperties": False,
        },
        lambda community_id, duration_minutes=60, customer_id=None, initiator_chat_id=None, cooldown_seconds=300, poll_interval_seconds=60, note=None, **_: tool_start_watch(
            community_id=community_id,
            duration_minutes=duration_minutes,
            customer_id=customer_id,
            initiator_chat_id=initiator_chat_id,
            cooldown_seconds=cooldown_seconds,
            poll_interval_seconds=poll_interval_seconds,
            note=note,
        ),
    ),
    (
        "stop_watch",
        "Cancel an active watch. Provide `watch_id` (preferred) or `community_id`.",
        {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "watch_id": {"type": "string"},
                "community_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "additionalProperties": False,
        },
        lambda customer_id=None, watch_id=None, community_id=None, reason="operator_stopped", **_: tool_stop_watch(customer_id=customer_id, watch_id=watch_id, community_id=community_id, reason=reason),
    ),
    (
        "list_watches",
        "List active watches (or all if only_active=false).",
        {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "only_active": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        },
        lambda customer_id=None, only_active=True, **_: tool_list_watches(customer_id=customer_id, only_active=only_active),
    ),
    (
        "add_community",
        "Onboard a new LINE OpenChat into Project Echo configs from an invite URL. Use when operator says 「幫我加這個群 / 把這個群也加進來 / 我已經在這個群了，幫我登錄」 plus a line.me/ti/g2/<id> link. Idempotent: if the group_id is already known, returns the existing community_id. Auto-detects display_name by deep-linking and reading the chat header. Bootstraps a default voice_profile.md the operator can refine later.",
        {
            "type": "object",
            "properties": {
                "invite_url": {"type": "string", "description": "Full LINE invite URL (https://line.me/ti/g2/<id>)."},
                "customer_id": {"type": "string"},
                "device_id": {"type": "string", "description": "Optional. Defaults to first enabled device for the customer."},
                "display_name": {"type": "string", "description": "Optional. If omitted, detected from the chat header after deep-link."},
                "patrol_interval_minutes": {"type": "integer", "default": 720},
                "persona": {"type": "string", "default": "default"},
            },
            "required": ["invite_url"],
            "additionalProperties": False,
        },
        lambda invite_url, customer_id=None, device_id=None, display_name=None, patrol_interval_minutes=720, persona="default", **_: tool_add_community(
            invite_url=invite_url,
            customer_id=customer_id,
            device_id=device_id,
            display_name=display_name,
            patrol_interval_minutes=patrol_interval_minutes,
            persona=persona,
        ),
    ),
    (
        "get_status_digest",
        "One-shot operational dashboard: system health (daemon/bridge), 24h send pipeline totals, per-community state (voice profile health, pending reviews, active watch), pending inbox with age, recent auto-fires, recent audit events. Use this whenever the operator asks 「狀態 / 盤點 / 看一下系統 / 給我一份摘要 / 現在怎樣」 or any general health-check question. Returns a `text` field — paste that text **verbatim** to the operator (don't paraphrase, don't strip lines), it's already formatted for Lark display. The `data` field is the structured snapshot for follow-up reasoning if needed.",
        {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "compact": {"type": "boolean", "default": True, "description": "Compact mode skips the recent_audit section (Lark-friendly)."},
            },
            "additionalProperties": False,
        },
        lambda customer_id=None, compact=True, **_: tool_get_status_digest(customer_id=customer_id, compact=compact),
    ),
    (
        "harvest_style_samples",
        "Read recent chat in a community, filter out announcements/links/system noise, score remaining lines for naturalness, and append the top N to that community's voice_profile.md under a managed `## Observed community lines` block. Use this when (a) a newly-onboarded community has only the bootstrap voice profile stub, or (b) the operator says 「幫 X 群抓一輪語氣樣本 / 補一下 X 群的真實語料 / X 群最近講話風格不太一樣」. Safe to re-run — the auto-managed block is replaced in place, the rest of the markdown (operator-edited Samples / Off-limits / personality) is preserved untouched.",
        {
            "type": "object",
            "properties": {
                "community_id": {"type": "string"},
                "customer_id": {"type": "string"},
                "limit": {"type": "integer", "default": 200, "description": "How many recent messages to scan."},
                "top_n": {"type": "integer", "default": 30, "description": "Max number of natural samples to keep."},
                "skip_navigate": {"type": "boolean", "default": False, "description": "Skip the deep-link navigate (use only when LINE is already on this room)."},
            },
            "required": ["community_id"],
            "additionalProperties": False,
        },
        lambda community_id, customer_id=None, limit=200, top_n=30, skip_navigate=False, **_: tool_harvest_style_samples(
            community_id=community_id,
            customer_id=customer_id,
            limit=limit,
            top_n=top_n,
            skip_navigate=skip_navigate,
        ),
    ),
    (
        "refresh_community_title",
        "Re-extract or override a community's display_name and rewrite its YAML. Use this when an existing community's display_name is wrong or fell back to a placeholder like 「未命名社群 (xxx…)」 — common when add_community's first deep-link landed on the chat list instead of the chat itself. Two modes: (a) auto — omit display_name and the workflow will navigate via deep link, dump UI, and read the chat header; (b) explicit — pass display_name=\"...\" to set it directly when the operator already knows the real name. Operator triggers: 「幫我把 openchat_004 名字補上」、「openchat_003 名字錯了，改成 ...」、「重新讀一下這群名字」.",
        {
            "type": "object",
            "properties": {
                "community_id": {"type": "string"},
                "customer_id": {"type": "string"},
                "display_name": {"type": "string", "description": "Optional explicit override. Omit to auto-detect via deep link + UI dump."},
            },
            "required": ["community_id"],
            "additionalProperties": False,
        },
        lambda community_id, customer_id=None, display_name=None, **_: tool_refresh_community_title(
            community_id=community_id,
            customer_id=customer_id,
            display_name=display_name,
        ),
    ),
    (
        "analyze_chat",
        "Watcher Mode Phase 1: navigate into a community, read recent messages, and return a curated signal — active state (cold_spell / active / moderate / trickle / quiet), last unanswered question, sensitivity hits against the voice profile's off-limits, and the last 12 messages. Use this when the operator says 「看一下 X 群最近怎麼樣」, 「X 群有沒有問題沒人回」, or 「X 群現在熱嗎」. After analyze_chat, you decide whether to draft (compose_and_send) or stay quiet — DO NOT auto-draft without checking unanswered_question, active_state, and sensitivity_hits.",
        {
            "type": "object",
            "properties": {
                "community_id": {"type": "string"},
                "customer_id": {"type": "string"},
                "limit": {"type": "integer", "default": 20, "description": "How many recent messages to scan."},
                "skip_navigate": {"type": "boolean", "default": False, "description": "Skip the deep-link navigate step (use only when LINE is already on this room)."},
            },
            "required": ["community_id"],
            "additionalProperties": False,
        },
        lambda community_id, customer_id=None, limit=20, skip_navigate=False, **_: tool_analyze_chat(community_id=community_id, customer_id=customer_id, limit=limit, skip_navigate=skip_navigate),
    ),
    (
        "get_voice_profile",
        "Read the operator's voice profile (markdown) for a community. **You MUST call this before compose_and_send** so the draft matches the operator's tone, audience, and off-limits topics. Returns `loaded=False` when no profile exists yet — fall back to short, casual 繁中 in that case.",
        {
            "type": "object",
            "properties": {
                "community_id": {"type": "string"},
                "customer_id": {"type": "string"},
            },
            "required": ["community_id"],
            "additionalProperties": False,
        },
        lambda community_id, customer_id=None, **_: tool_get_voice_profile(community_id=community_id, customer_id=customer_id),
    ),
    (
        "set_voice_profile",
        "Replace a community's voice profile with new markdown content. Use when operator says 「重新寫一份語氣設定 / 把語氣改成 ...」. The full markdown body should include Operator / Audience / Tone notes / Samples / Off-limits sections. The operator can also edit the file directly on disk.",
        {
            "type": "object",
            "properties": {
                "community_id": {"type": "string"},
                "content": {"type": "string", "description": "Full markdown body."},
                "customer_id": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["community_id", "content"],
            "additionalProperties": False,
        },
        lambda community_id, content, customer_id=None, note=None, **_: tool_set_voice_profile(community_id=community_id, content=content, customer_id=customer_id, note=note),
    ),
    (
        "append_voice_sample",
        "Append a single sample message to a community's voice profile. Use when operator says 「幫我記下這個語氣 / 這句話以後可以參考」. Bootstraps a profile if none exists. Prefer this over set_voice_profile for one-off additions.",
        {
            "type": "object",
            "properties": {
                "community_id": {"type": "string"},
                "sample_text": {"type": "string"},
                "customer_id": {"type": "string"},
                "note": {"type": "string", "description": "Optional 1-line context, e.g. 『這是冷場時用』."},
            },
            "required": ["community_id", "sample_text"],
            "additionalProperties": False,
        },
        lambda community_id, sample_text, customer_id=None, note=None, **_: tool_append_voice_sample(community_id=community_id, sample_text=sample_text, customer_id=customer_id, note=note),
    ),
    (
        "list_voice_profiles",
        "List which communities currently have voice profiles configured (with byte size + last-modified).",
        {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "additionalProperties": False,
        },
        lambda customer_id=None, **_: tool_list_voice_profiles(customer_id=customer_id),
    ),
    (
        "list_scheduled_posts",
        "List scheduled posts (future + past) for one or all communities.",
        {
            "type": "object",
            "properties": {"community_id": {"type": "string"}},
            "additionalProperties": False,
        },
        lambda community_id=None, **_: tool_list_scheduled_posts(community_id=community_id),
    ),
    (
        "scheduled_post_status",
        "Aggregate counts of scheduled / due / reviewing / sent / cancelled / skipped posts.",
        {
            "type": "object",
            "properties": {"community_id": {"type": "string"}},
            "additionalProperties": False,
        },
        lambda community_id=None, **_: tool_scheduled_post_status(community_id=community_id),
    ),
    (
        "add_scheduled_post",
        "Schedule a message for future delivery. send_at must be ISO 8601 with timezone, e.g. 2026-04-29T20:00:00+08:00. The post will go through the same review pipeline at send time.",
        {
            "type": "object",
            "properties": {
                "community_id": {"type": "string"},
                "send_at": {"type": "string", "description": "ISO 8601 with timezone offset"},
                "text": {"type": "string"},
                "customer_id": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["community_id", "send_at", "text"],
            "additionalProperties": False,
        },
        lambda community_id, send_at, text, customer_id=None, note=None, **_: tool_add_scheduled_post(community_id=community_id, send_at=send_at, text=text, customer_id=customer_id, note=note),
    ),
    (
        "cancel_scheduled_post",
        "Cancel a scheduled post by post_id. Once cancelled, it will not fire even if its send_at passes.",
        {
            "type": "object",
            "properties": {
                "community_id": {"type": "string"},
                "post_id": {"type": "string"},
                "customer_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["community_id", "post_id"],
            "additionalProperties": False,
        },
        lambda community_id, post_id, customer_id=None, reason="operator_cancelled", **_: tool_cancel_scheduled_post(community_id=community_id, post_id=post_id, customer_id=customer_id, reason=reason),
    ),
    (
        "action_queue",
        "Get the prioritized action queue for the next steps the operator should take.",
        {
            "type": "object",
            "properties": {
                "community_id": {"type": "string"},
                "customer_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
        lambda community_id=None, customer_id=None, **_: tool_action_queue(customer_id=customer_id, community_id=community_id),
    ),
]


def build_server() -> Server:
    server = Server("project-echo")

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return [
            Tool(name=name, description=description, inputSchema=schema)
            for name, description, schema, _ in TOOL_DEFINITIONS
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        for tool_name, _desc, _schema, fn in TOOL_DEFINITIONS:
            if tool_name == name:
                try:
                    result = fn(**(arguments or {}))
                except TypeError as exc:
                    result = {"status": "error", "reason": "bad_arguments", "detail": str(exc)}
                except Exception as exc:  # noqa: BLE001 — surface to LLM as a tool error
                    result = {"status": "error", "reason": "internal", "detail": repr(exc)}
                return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
        return [TextContent(type="text", text=json.dumps({"status": "error", "reason": "unknown_tool", "name": name}, ensure_ascii=False))]

    return server


async def serve_stdio() -> None:
    server = build_server()
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())
