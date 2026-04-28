from __future__ import annotations

import time

from app.adb.client import AdbClient, AdbError
from app.core.audit import append_audit_event
from app.storage.config_loader import get_device_config
from app.workflows.device_recovery import ensure_device_ready
from app.workflows.device_status import get_device_status

LINE_PACKAGE = "jp.naver.line.android"
PLAY_STORE_PACKAGE = "com.android.vending"
PLAY_STORE_URL = f"https://play.google.com/store/apps/details?id={LINE_PACKAGE}"


def has_play_store(device_id: str) -> bool:
    """Return True only if a *functional* Play Store can resolve a market:// intent.

    The package `com.android.vending` is also present on Google APIs emulator images as
    a `LicenseChecker` stub with no launchable activity. Checking only for the package
    would yield a false positive on those images, so we verify the intent actually
    resolves to an activity.
    """

    client = AdbClient(device_id=device_id)
    try:
        result = client.shell(
            "cmd",
            "package",
            "resolve-activity",
            "--brief",
            "-a",
            "android.intent.action.VIEW",
            "-d",
            "market://details?id=jp.naver.line.android",
            check=False,
        )
    except AdbError:
        return False
    output = (result.stdout or "").strip()
    if not output or "No activity found" in output:
        return False
    return PLAY_STORE_PACKAGE in output


def open_line_in_play_store(device_id: str) -> dict[str, object]:
    device = get_device_config(device_id)
    customer_id = device.customer_id

    recovery = ensure_device_ready(device_id, wait_timeout_seconds=60)
    if recovery.get("status") != "ready":
        result = {
            "status": "blocked",
            "device_id": device_id,
            "reason": "device_not_ready",
            "recovery": recovery,
        }
        append_audit_event(customer_id, "play_store_open_blocked", result)
        return result

    if not has_play_store(device_id):
        result = {
            "status": "blocked",
            "device_id": device_id,
            "reason": "play_store_missing",
            "message": "模擬器沒有 Play Store，請改用 sideload 流程。",
        }
        append_audit_event(customer_id, "play_store_open_blocked", result)
        return result

    client = AdbClient(device_id=device_id)
    try:
        client.shell("input", "keyevent", "KEYCODE_WAKEUP")
        client.shell("am", "start", "-a", "android.intent.action.VIEW", "-d", PLAY_STORE_URL)
    except AdbError as exc:
        result = {
            "status": "blocked",
            "device_id": device_id,
            "reason": "intent_failed",
            "detail": str(exc),
        }
        append_audit_event(customer_id, "play_store_open_blocked", result)
        return result

    result = {
        "status": "ok",
        "device_id": device_id,
        "play_store_url": PLAY_STORE_URL,
        "next_actions": [
            "在模擬器上完成一次性 Google 帳號登入（如未登入）。",
            "在 Play Store LINE 頁面點擊「安裝」。",
            "執行 `python3 scripts/wait_for_line_installed.py emulator-5554` 等待安裝完成並寫入稽核。",
        ],
    }
    append_audit_event(customer_id, "play_store_opened", {"device_id": device_id, "package": LINE_PACKAGE})
    return result


def is_line_installed(device_id: str) -> bool:
    client = AdbClient(device_id=device_id)
    try:
        result = client.shell("pm", "list", "packages", LINE_PACKAGE)
    except AdbError:
        return False
    return LINE_PACKAGE in (result.stdout or "")


def wait_for_line_installed(device_id: str, timeout_seconds: int = 600, poll_seconds: int = 5) -> dict[str, object]:
    device = get_device_config(device_id)
    customer_id = device.customer_id

    append_audit_event(
        customer_id,
        "line_install_wait_started",
        {"device_id": device_id, "timeout_seconds": timeout_seconds, "source": "play_store"},
    )

    deadline = time.monotonic() + timeout_seconds
    polls = 0
    while time.monotonic() < deadline:
        polls += 1
        if is_line_installed(device_id):
            status = get_device_status(device_id)
            result = {
                "status": "ok",
                "device_id": device_id,
                "polls": polls,
                "device_status": status,
                "source": "play_store",
            }
            append_audit_event(
                customer_id,
                "line_install_completed",
                {
                    "device_id": device_id,
                    "apk_path": "play_store",
                    "line_installed": True,
                    "foreground_package": status.get("foreground_package"),
                    "source": "play_store",
                },
            )
            return result
        time.sleep(poll_seconds)

    result = {
        "status": "blocked",
        "device_id": device_id,
        "reason": "timeout",
        "polls": polls,
        "timeout_seconds": timeout_seconds,
    }
    append_audit_event(customer_id, "line_install_blocked", result)
    return result
