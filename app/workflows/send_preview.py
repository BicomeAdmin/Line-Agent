from __future__ import annotations

from app.adb.input import build_send_plan
from app.storage.config_loader import load_community_config


def preview_send(customer_id: str, community_id: str, text: str) -> dict[str, object]:
    community = load_community_config(customer_id, community_id)
    if None in (community.input_x, community.input_y, community.send_x, community.send_y):
        return {
            "status": "blocked",
            "customer_id": customer_id,
            "community_id": community_id,
            "community_name": community.display_name,
            "reason": "missing_send_coordinates",
            "coordinate_source": community.coordinate_source,
        }

    plan = build_send_plan(
        text,
        input_x=community.input_x,
        input_y=community.input_y,
        send_x=community.send_x,
        send_y=community.send_y,
    )
    return {
        "status": "ok",
        "customer_id": customer_id,
        "community_id": community_id,
        "community_name": community.display_name,
        "device_id": community.device_id,
        "coordinate_source": community.coordinate_source,
        "plan": plan,
    }
