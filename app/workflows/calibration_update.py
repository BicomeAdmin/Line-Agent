from __future__ import annotations

from app.core.audit import append_audit_event
from app.core.calibrations import CalibrationRecord, calibration_store
from app.storage.config_loader import load_community_config


def save_community_calibration(
    customer_id: str,
    community_id: str,
    input_x: int,
    input_y: int,
    send_x: int,
    send_y: int,
    note: str | None = None,
    source: str = "runtime_cli",
) -> dict[str, object]:
    community = load_community_config(customer_id, community_id)
    record = calibration_store.upsert(
        CalibrationRecord(
            customer_id=customer_id,
            community_id=community_id,
            input_x=input_x,
            input_y=input_y,
            send_x=send_x,
            send_y=send_y,
            note=note,
            source=source,
        )
    )
    refreshed = load_community_config(customer_id, community_id)
    payload = {
        "status": "ok",
        "customer_id": customer_id,
        "community_id": community_id,
        "community_name": community.display_name,
        "coordinate_source": refreshed.coordinate_source,
        "record": record.to_dict(),
    }
    append_audit_event(
        customer_id,
        "community_calibration_saved",
        {
            "community_id": community_id,
            "device_id": community.device_id,
            "coordinate_source": refreshed.coordinate_source,
            "input_x": input_x,
            "input_y": input_y,
            "send_x": send_x,
            "send_y": send_y,
            "note": note,
        },
    )
    return payload
