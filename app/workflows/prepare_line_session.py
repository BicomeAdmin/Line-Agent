from __future__ import annotations

import time

from app.adb.client import AdbClient
from app.adb.devices import package_installed, wait_for_boot, wake_and_unlock
from app.adb.line_app import LINE_PACKAGE, check_current_app, open_line
from app.core.audit import append_audit_event
from app.storage.config_loader import get_device_config
from app.workflows.device_status import get_device_status


def prepare_line_session(device_id: str, boot_timeout_seconds: int = 10) -> dict[str, object]:
    device = get_device_config(device_id)
    client = AdbClient(device_id=device_id, timeout=min(max(5, boot_timeout_seconds), 10))

    boot_ok = wait_for_boot(client, timeout_seconds=boot_timeout_seconds, poll_interval=2.0)
    if not boot_ok:
        result = {
            "status": "blocked",
            "device_id": device_id,
            "reason": "boot_not_completed",
        }
        append_audit_event(device.customer_id, "line_session_prepare_blocked", result)
        return result

    wake_and_unlock(client)
    if not package_installed(client, LINE_PACKAGE):
        result = {
            "status": "blocked",
            "device_id": device_id,
            "reason": "line_not_installed",
        }
        append_audit_event(device.customer_id, "line_session_prepare_blocked", result)
        return result

    open_line(client)
    time.sleep(2.0)
    status = get_device_status(device_id)
    result = {
        "status": "ok" if status.get("line_active") else "partial",
        "device_id": device_id,
        "line_active": status.get("line_active"),
        "foreground_package": status.get("foreground_package"),
        "line_installed": status.get("line_installed"),
        "boot_completed": status.get("boot_completed"),
    }
    append_audit_event(device.customer_id, "line_session_prepared", result)
    return result
