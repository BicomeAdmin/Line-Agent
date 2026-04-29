from __future__ import annotations

from app.adb.client import AdbClient
from app.adb.line_app import check_current_app
from app.core.audit import append_audit_event
from app.core.scheduler_state import scheduler_state
from app.storage.config_loader import CommunityConfig, get_device_config, load_communities_for_device, load_customer_config, load_risk_control
from app.workflows.draft_reply import draft_reply_for_device


def patrol_device(device_id: str) -> dict[str, object]:
    from app.core.risk_control import community_is_in_activity_window

    device = get_device_config(device_id)
    customer = load_customer_config(device.customer_id)
    communities = load_communities_for_device(device_id)
    # load_risk_control() retained as a side-effect-free no-op kept for API
    # compatibility — risk_control gates moved into per-community helpers.
    load_risk_control()

    # If NO community on this device is in its (possibly overridden) window,
    # short-circuit before touching ADB. As soon as any one is in-window we
    # proceed; per-community gates inside patrol_community will skip the
    # remaining out-of-window ones.
    if communities and not any(community_is_in_activity_window(c) for c in communities):
        result = {
            "status": "skipped",
            "device_id": device_id,
            "reason": "outside_activity_window",
            "community_count": len(communities),
        }
        append_audit_event(device.customer_id, "patrol_skipped", result)
        return result

    if not check_current_app(AdbClient(device_id=device_id)):
        result = {
            "status": "skipped",
            "device_id": device_id,
            "reason": "line_inactive",
            "community_count": len(communities),
        }
        append_audit_event(device.customer_id, "patrol_skipped", result)
        return result

    reviews: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    for community in communities:
        outcome = patrol_community(community)
        if outcome.get("status") == "review_ready":
            reviews.append(outcome)
        else:
            skipped.append(outcome)

    result = {
        "status": "ok",
        "device_id": device_id,
        "customer_id": device.customer_id,
        "customer_name": customer.display_name,
        "review_count": len(reviews),
        "skip_count": len(skipped),
        "reviews": reviews,
        "skipped": skipped,
    }
    append_audit_event(device.customer_id, "patrol_completed", {"device_id": device_id, "review_count": len(reviews), "skip_count": len(skipped)})
    return result


def patrol_community(community: CommunityConfig) -> dict[str, object]:
    from app.core.risk_control import community_is_in_activity_window
    if not community_is_in_activity_window(community):
        result = {
            "status": "skipped",
            "community_id": community.community_id,
            "community_name": community.display_name,
            "reason": "outside_activity_window",
        }
        scheduler_state.mark_completed(f"{community.customer_id}:{community.community_id}")
        append_audit_event(community.customer_id, "community_patrol_skipped", result)
        return result

    # Preflight: navigate to the target chat. LINE may be in foreground but on
    # the wrong room (e.g. previous patrol left us elsewhere); without this we
    # would read the wrong chat's history and draft for the wrong audience.
    from app.workflows.openchat_navigate import navigate_to_openchat

    nav = navigate_to_openchat(community.customer_id, community.community_id, overall_timeout_seconds=20.0)
    if nav.get("status") != "ok":
        result = {
            "status": "skipped",
            "community_id": community.community_id,
            "community_name": community.display_name,
            "reason": f"navigate_failed:{nav.get('reason') or 'unknown'}",
            "navigate_trace": nav.get("trace"),
        }
        scheduler_state.mark_completed(f"{community.customer_id}:{community.community_id}")
        append_audit_event(community.customer_id, "community_patrol_skipped", result)
        return result

    if not check_current_app(AdbClient(device_id=community.device_id)):
        result = {
            "status": "skipped",
            "community_id": community.community_id,
            "community_name": community.display_name,
            "reason": "line_inactive",
        }
        scheduler_state.mark_completed(f"{community.customer_id}:{community.community_id}")
        append_audit_event(community.customer_id, "community_patrol_skipped", result)
        return result

    # Rule-based draft generation (light_prompt / cold_spell / unanswered)
    # has been retired in favor of the autonomous Watcher Phase 2 pipeline,
    # which respects sender attribution + member fingerprints + persona
    # context rather than generic "encourage engagement" templates.
    # Patrol now defaults to observe-only — navigates, confirms LINE is
    # in the right room, records audit, but does NOT generate drafts.
    # Re-enable the legacy templates only via PATROL_DRAFTS_ENABLED=true.
    import os
    if os.getenv("PATROL_DRAFTS_ENABLED", "false").strip().lower() != "true":
        result = {
            "status": "observed_only",
            "community_id": community.community_id,
            "community_name": community.display_name,
            "device_id": community.device_id,
            "note": "rule-based patrol drafts disabled; autonomous watcher handles drafts",
        }
        scheduler_state.mark_completed(f"{community.customer_id}:{community.community_id}")
        append_audit_event(community.customer_id, "community_patrol_observed_only", result)
        return result

    draft = draft_reply_for_device(community.device_id, limit=20, community_id=community.community_id)
    decision = draft["decision"]
    scheduler_state.mark_completed(f"{community.customer_id}:{community.community_id}")
    if isinstance(decision, dict) and decision.get("action") == "draft_reply":
        result = {"status": "review_ready", **draft}
        append_audit_event(
            community.customer_id,
            "community_patrol_review_ready",
            {
                "community_id": community.community_id,
                "device_id": community.device_id,
                "reason": decision.get("reason"),
            },
        )
        return result

    result = {
        "status": "skipped",
        "community_id": community.community_id,
        "community_name": community.display_name,
        "reason": decision.get("reason") if isinstance(decision, dict) else "unknown",
    }
    append_audit_event(community.customer_id, "community_patrol_skipped", result)
    return result
