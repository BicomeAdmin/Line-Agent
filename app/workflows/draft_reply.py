from __future__ import annotations

from app.adb.client import AdbClient
from app.ai.context_bundle import build_prompt_context, load_context_bundle
from app.ai.decision import decide_reply
from app.lark.cards import build_review_card
from app.storage.config_loader import load_communities_for_device, load_customer_config, get_device_config
from app.storage.paths import default_raw_xml_path
from app.workflows.read_chat import read_recent_chat


def draft_reply_for_device(device_id: str, limit: int = 20, community_id: str | None = None) -> dict[str, object]:
    device = get_device_config(device_id)
    communities = load_communities_for_device(device_id)
    if not communities:
        raise RuntimeError(f"No enabled community mapped to device {device_id}")

    community = _select_community(communities, community_id)

    # Preflight: navigate to the target chat so we read the right room's history.
    # Skipped if caller didn't specify a community_id (legacy single-community path).
    if community_id is not None:
        from app.workflows.openchat_navigate import navigate_to_openchat

        nav = navigate_to_openchat(device.customer_id, community.community_id, overall_timeout_seconds=20.0)
        if nav.get("status") != "ok":
            return {
                "status": "blocked",
                "device_id": device_id,
                "customer_id": device.customer_id,
                "community_id": community.community_id,
                "community_name": community.display_name,
                "reason": f"navigate_failed:{nav.get('reason') or 'unknown'}",
                "navigate_result": {k: nav.get(k) for k in ("status", "reason", "matched_title")},
            }

    customer = load_customer_config(device.customer_id)
    messages = read_recent_chat(
        AdbClient(device_id=device_id),
        default_raw_xml_path(device.customer_id),
        limit=limit,
    )
    context_bundle = load_context_bundle(device.customer_id, community.community_id)
    prompt_context = build_prompt_context(context_bundle, messages)
    decision = decide_reply(
        messages,
        context_bundle.persona_text,
        community.display_name,
        playbook_text=context_bundle.playbook_text,
        safety_rules=context_bundle.safety_rules,
    )
    return {
        "status": "ok",
        "device_id": device_id,
        "customer_id": device.customer_id,
        "customer_name": customer.display_name,
        "community_id": community.community_id,
        "community_name": community.display_name,
        "context_bundle": context_bundle.to_dict(),
        "prompt_context": prompt_context,
        "decision": decision.to_dict(),
        "recent_messages": messages,
        "review_card": build_review_card(
            customer_name=customer.display_name,
            community_name=community.display_name,
            draft=decision.draft,
            job_id="PENDING_JOB_ID",
            customer_id=device.customer_id,
            community_id=community.community_id,
            device_id=device_id,
            reason=decision.reason,
            confidence=decision.confidence,
        ),
    }


def _select_community(communities: list[object], community_id: str | None) -> object:
    if community_id is None:
        return communities[0]
    for community in communities:
        if getattr(community, "community_id", None) == community_id:
            return community
    raise RuntimeError(f"Community not found for device: {community_id}")
