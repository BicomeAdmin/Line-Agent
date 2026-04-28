from __future__ import annotations

from app.adb.client import AdbClient
from app.adb.input import tap_type_send
from app.core.audit import append_audit_event
from app.core.send_gate import send_gate
from app.storage.config_loader import get_device_config, load_community_config, load_risk_control


def send_draft(
    customer_id: str,
    community_id: str,
    device_id: str,
    draft_text: str,
) -> dict[str, object]:
    community = load_community_config(customer_id, community_id)
    if community.device_id != device_id:
        raise RuntimeError(f"Community {community_id} is not mapped to device {device_id}")
    # NOTE: missing static calibration is no longer fatal — `tap_type_send` will
    # dump live UI and resolve input + send button positions on the fly. This
    # lets dynamically-onboarded communities (via add_community) send without a
    # pre-flight calibration step.
    risk_control = load_risk_control()
    if risk_control.require_human_approval:
        append_audit_event(customer_id, "human_approved_send_started", {"community_id": community_id, "device_id": device_id})

    device = get_device_config(device_id)
    wait_meta = send_gate.wait_turn(device.label, f"{customer_id}:{community_id}", risk_control)
    result = tap_type_send(
        AdbClient(device_id=device_id),
        draft_text,
        input_x=community.input_x,
        input_y=community.input_y,
        send_x=community.send_x,
        send_y=community.send_y,
        risk_control=risk_control,
    )
    payload = {
        "status": result.get("status", "unknown"),
        "device_id": device_id,
        "community_id": community_id,
        "delay_seconds": result.get("delay_seconds"),
        "gate_wait_seconds": wait_meta["waited_seconds"],
    }
    append_audit_event(customer_id, "send_attempt", payload)
    return payload
