from __future__ import annotations

from app.workflows.acceptance_status import get_acceptance_status
from app.workflows.action_queue import build_action_queue_items
from app.workflows.community_status import get_community_status
from app.workflows.line_apk_status import get_line_apk_status
from app.workflows.onboarding_timeline import get_onboarding_timeline
from app.workflows.openchat_validation import validate_openchat_session
from app.workflows.readiness_status import get_readiness_status
from app.workflows.scheduled_post_status import get_scheduled_post_status


def get_project_snapshot(customer_id: str | None = None, community_id: str | None = None) -> dict[str, object]:
    readiness = get_readiness_status()
    line_apk = get_line_apk_status()
    communities = get_community_status(customer_id=customer_id, community_id=community_id)
    acceptance = get_acceptance_status(customer_id=customer_id, community_id=community_id)
    openchat = validate_openchat_session(customer_id=customer_id, community_id=community_id)
    onboarding = get_onboarding_timeline(customer_id=customer_id, community_id=community_id)
    scheduled_posts = get_scheduled_post_status(customer_id=customer_id, community_id=community_id)

    acceptance_items = acceptance.get("items", [])
    community_items = communities.get("items", [])
    openchat_items = openchat.get("items", [])
    onboarding_items = onboarding.get("items", [])

    spotlight = None
    if isinstance(acceptance_items, list) and acceptance_items:
        acceptance_item = acceptance_items[0] if isinstance(acceptance_items[0], dict) else {}
        community_item = community_items[0] if isinstance(community_items, list) and community_items and isinstance(community_items[0], dict) else {}
        openchat_item = openchat_items[0] if isinstance(openchat_items, list) and openchat_items and isinstance(openchat_items[0], dict) else {}
        onboarding_item = onboarding_items[0] if isinstance(onboarding_items, list) and onboarding_items and isinstance(onboarding_items[0], dict) else {}
        spotlight = {
            "customer_id": acceptance_item.get("customer_id"),
            "community_id": acceptance_item.get("community_id"),
            "community_name": acceptance_item.get("community_name"),
            "acceptance_stage": acceptance_item.get("stage"),
            "openchat_status": openchat_item.get("status"),
            "openchat_reason": openchat_item.get("reason"),
            "coordinates_ready": community_item.get("coordinates_ready"),
            "last_openchat_validation_at": community_item.get("last_openchat_validation_at"),
            "latest_timeline_stage": onboarding_item.get("latest_stage"),
        }

    next_actions = []
    readiness_actions = readiness.get("next_actions")
    if isinstance(readiness_actions, list):
        next_actions = [action for action in readiness_actions if isinstance(action, str)][:8]

    readiness_summary = readiness.get("summary")
    if not isinstance(readiness_summary, dict):
        readiness_summary = {}

    sections = {
        "readiness": readiness,
        "line_apk": line_apk,
        "communities": communities,
        "acceptance": acceptance,
        "openchat": openchat,
        "onboarding": onboarding,
        "scheduled_posts": scheduled_posts,
    }
    action_queue_items = build_action_queue_items(spotlight if isinstance(spotlight, dict) else {}, sections)
    active_phase = _active_phase(action_queue_items)

    return {
        "status": "ok",
        "generated_from": {
            "readiness": "get_readiness_status",
            "line_apk": "get_line_apk_status",
            "communities": "get_community_status",
            "acceptance": "get_acceptance_status",
            "openchat": "validate_openchat_session",
            "onboarding": "get_onboarding_timeline",
        },
        "summary": {
            "overall_ready": bool(readiness_summary.get("ready")),
            "blocker_count": int(readiness_summary.get("blocker_count", 0)),
            "warning_count": int(readiness_summary.get("warning_count", 0)),
            "devices_needing_line": int(line_apk.get("devices_needing_line", 0)),
            "acceptance_ready_count": int(acceptance.get("ready_count", 0)),
            "openchat_ready_count": int(openchat.get("ready_count", 0)),
            "active_phase": active_phase,
            "action_queue_count": len(action_queue_items),
            "scheduled_posts_active": int(scheduled_posts.get("active_count", 0)),
        },
        "spotlight": spotlight,
        "next_actions": next_actions,
        "action_queue": {
            "queue_count": len(action_queue_items),
            "items": action_queue_items,
        },
        "sections": sections,
    }


def _active_phase(action_queue_items: list[dict[str, object]]) -> str:
    if not action_queue_items:
        return "ready_for_demo"
    first = action_queue_items[0]
    item_id = first.get("item_id")
    if item_id == "apk_stage":
        return "apk_blocked"
    if item_id == "install_line":
        return "install_line"
    if item_id == "open_target_openchat":
        return "openchat_navigation"
    if item_id == "calibrate_send":
        return "send_calibration"
    if item_id == "hil_demo":
        return "hil_demo"
    return "unknown"
