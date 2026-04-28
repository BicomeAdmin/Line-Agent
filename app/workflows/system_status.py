from __future__ import annotations

from app.core.audit import append_audit_event
from app.core.scheduler_state import scheduler_state
from app.storage.config_loader import load_customer_config, load_devices_config, load_risk_control
from app.workflows.device_status import get_device_status


def get_system_status() -> dict[str, object]:
    risk_control = load_risk_control()
    devices = load_devices_config()
    payload_devices: list[dict[str, object]] = []

    for device in devices:
        customer = load_customer_config(device.customer_id)
        status = get_device_status(device.device_id)
        payload_devices.append(
            {
                "device_id": device.device_id,
                "label": device.label,
                "customer_id": device.customer_id,
                "customer_name": customer.display_name,
                "enabled": device.enabled,
                **status,
            }
        )
        append_audit_event(
            device.customer_id,
            "device_status_snapshot",
            {
                "device_id": device.device_id,
                "boot_completed": status["boot_completed"],
                "foreground_package": status["foreground_package"],
                "line_installed": status["line_installed"],
                "line_active": status["line_active"],
            },
        )

    return {
        "status": "ok",
        "fixed_ip_mode": risk_control.fixed_ip_mode,
        "activity_window": {
            "start": risk_control.activity_start.strftime("%H:%M"),
            "end": risk_control.activity_end.strftime("%H:%M"),
        },
        "device_count": len(payload_devices),
        "devices": payload_devices,
        "scheduler_state": scheduler_state.snapshot(),
    }
