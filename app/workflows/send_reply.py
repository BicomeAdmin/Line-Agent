from __future__ import annotations

from app.adb.client import AdbClient
from app.adb.input import check_input_box_cleared, tap_type_send
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
    client = AdbClient(device_id=device_id)
    result = tap_type_send(
        client,
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

    # Post-send verification: a successful tap doesn't guarantee LINE actually
    # transmitted. If the input box still has our draft text, LINE swallowed
    # the send silently. Surface this so the operator can verify before
    # retrying — accidental double-sends happened on 2026-04-29 16:13.
    if payload["status"] == "sent":
        check = check_input_box_cleared(client)
        if check.get("status") == "not_cleared":
            append_audit_event(
                customer_id,
                "send_attempt_input_box_not_cleared",
                {
                    "community_id": community_id,
                    "device_id": device_id,
                    "preview": check.get("preview"),
                    "residual_length": check.get("residual_length"),
                    "severity": "important",
                    "action_hint": "上一次送出可能沒成功；確認 LINE 群裡是否實際出現訊息再決定是否重送",
                },
            )

    return payload
