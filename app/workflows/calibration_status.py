from __future__ import annotations

from app.storage.config_loader import load_all_communities


def get_calibration_status() -> dict[str, object]:
    communities = load_all_communities()
    items = []
    for community in communities:
        ready = None not in (community.input_x, community.input_y, community.send_x, community.send_y)
        items.append(
            {
                "customer_id": community.customer_id,
                "community_id": community.community_id,
                "community_name": community.display_name,
                "device_id": community.device_id,
                "coordinates_ready": ready,
                "coordinate_source": community.coordinate_source,
                "input_x": community.input_x,
                "input_y": community.input_y,
                "send_x": community.send_x,
                "send_y": community.send_y,
            }
        )

    return {
        "status": "ok",
        "total": len(items),
        "ready_count": sum(1 for item in items if item["coordinates_ready"]),
        "pending_count": sum(1 for item in items if not item["coordinates_ready"]),
        "items": items,
    }
