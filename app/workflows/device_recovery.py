from __future__ import annotations

import time

from app.adb.client import AdbClient, AdbError
from app.adb.devices import boot_completed, wait_for_boot
from app.adb.emulator import EmulatorError, running_avd_names, start_avd
from app.core.audit import append_audit_event
from app.storage.config_loader import get_device_config
from app.workflows.device_status import get_device_status


def ensure_device_ready(device_id: str, wait_timeout_seconds: int = 60) -> dict[str, object]:
    device = get_device_config(device_id)
    customer_id = device.customer_id
    client = AdbClient(device_id=device_id, timeout=min(max(5, wait_timeout_seconds), 15))
    controller = AdbClient(timeout=15)

    try:
        visible_devices = controller.devices() if controller.is_available() else []
    except AdbError as exc:
        result = {
            "status": "blocked",
            "device_id": device_id,
            "reason": "adb_unavailable",
            "detail": str(exc),
        }
        append_audit_event(customer_id, "device_recovery_blocked", result)
        return result
    device_seen = device_id in visible_devices

    if not device_seen and device.avd_name:
        try:
            started = _ensure_avd_started(device.avd_name)
        except EmulatorError as exc:
            result = {
                "status": "blocked",
                "device_id": device_id,
                "reason": "emulator_start_failed",
                "detail": str(exc),
            }
            append_audit_event(customer_id, "device_recovery_blocked", result)
            return result
        append_audit_event(
            customer_id,
            "device_recovery_started_avd",
            {"device_id": device_id, "avd_name": device.avd_name, "started": started},
        )
        if not _wait_for_device_presence(controller, device_id, timeout_seconds=min(wait_timeout_seconds, 30)):
            result = {
                "status": "blocked",
                "device_id": device_id,
                "reason": "device_not_visible_after_start",
                "avd_name": device.avd_name,
            }
            append_audit_event(customer_id, "device_recovery_blocked", result)
            return result
        device_seen = True

    if not device_seen:
        result = {
            "status": "blocked",
            "device_id": device_id,
            "reason": "device_not_visible",
        }
        append_audit_event(customer_id, "device_recovery_blocked", result)
        return result

    try:
        device_booted = boot_completed(client)
    except AdbError as exc:
        result = {
            "status": "blocked",
            "device_id": device_id,
            "reason": "adb_unavailable",
            "detail": str(exc),
        }
        append_audit_event(customer_id, "device_recovery_blocked", result)
        return result

    if not device_booted:
        if not wait_for_boot(client, timeout_seconds=wait_timeout_seconds, poll_interval=2.0):
            result = {
                "status": "blocked",
                "device_id": device_id,
                "reason": "boot_not_completed",
            }
            append_audit_event(customer_id, "device_recovery_blocked", result)
            return result

    try:
        status = get_device_status(device_id)
    except AdbError as exc:
        result = {
            "status": "blocked",
            "device_id": device_id,
            "reason": "adb_unavailable",
            "detail": str(exc),
        }
        append_audit_event(customer_id, "device_recovery_blocked", result)
        return result
    result = {
        "status": "ready",
        "device_id": device_id,
        "device_status": status,
    }
    append_audit_event(customer_id, "device_recovery_ready", {"device_id": device_id, "boot_completed": status.get("boot_completed")})
    return result


def _ensure_avd_started(avd_name: str) -> bool:
    if avd_name in running_avd_names():
        return False
    start_avd(avd_name, no_snapshot=True)
    return True


def _wait_for_device_presence(client: AdbClient, device_id: str, timeout_seconds: int = 30) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if device_id in client.devices():
            return True
        time.sleep(1.0)
    return False
