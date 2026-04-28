from __future__ import annotations

from pathlib import Path

from app.adb.client import AdbClient, AdbError
from app.config import settings
from app.storage.config_loader import load_all_communities, load_customer_config, load_devices_config
from app.storage.paths import customer_root
from app.workflows.device_status import get_device_status
from app.workflows.line_install import inspect_line_apk_sources


def get_readiness_status() -> dict[str, object]:
    devices = load_devices_config()
    communities = load_all_communities()
    adb_client = AdbClient()
    adb_available = adb_client.is_available()

    global_checks = _build_global_checks(adb_available)
    device_checks = [_build_device_check(device.device_id, device.customer_id, adb_available) for device in devices]
    apk_check = _build_line_apk_check(device_checks)
    if apk_check is not None:
        global_checks.append(apk_check)
    community_checks = [_build_community_check(community) for community in communities]

    blockers = [
        item
        for item in [*global_checks, *_flatten_check_items(device_checks), *_flatten_check_items(community_checks)]
        if item["severity"] == "blocker"
    ]
    warnings = [
        item
        for item in [*global_checks, *_flatten_check_items(device_checks), *_flatten_check_items(community_checks)]
        if item["severity"] == "warning"
    ]

    return {
        "status": "ok",
        "summary": {
            "ready": len(blockers) == 0,
            "blocker_count": len(blockers),
            "warning_count": len(warnings),
            "device_count": len(devices),
            "community_count": len(communities),
        },
        "global_checks": global_checks,
        "devices": device_checks,
        "communities": community_checks,
        "next_actions": _next_actions(blockers, warnings),
    }


def _build_global_checks(adb_available: bool) -> list[dict[str, object]]:
    checks = [
        _check_item(
            key="adb_available",
            ok=adb_available,
            severity="blocker",
            message="ADB 可用，裝置治理鏈路存在。" if adb_available else "找不到 ADB，可先修復 platform-tools / PATH。",
        ),
        _check_item(
            key="lark_app_credentials",
            ok=bool(settings.lark_app_id and settings.lark_app_secret),
            severity="blocker",
            message="Lark App ID / Secret 已配置。" if settings.lark_app_id and settings.lark_app_secret else "缺少 Lark App ID 或 App Secret。",
        ),
        _check_item(
            key="lark_verification_token",
            ok=bool(settings.lark_verification_token),
            severity="warning",
            message="Lark verification token 已配置。" if settings.lark_verification_token else "尚未配置 Lark verification token，正式 webhook 驗證前需補上。",
        ),
        _check_item(
            key="human_approval_enabled",
            ok=settings.require_human_approval,
            severity="warning",
            message="人工審核已啟用。" if settings.require_human_approval else "目前未要求人工審核，與 PRD 的 HIL 流程不一致。",
        ),
    ]
    return checks


def _build_line_apk_check(device_checks: list[dict[str, object]]) -> dict[str, object] | None:
    needs_line_install = False
    for group in device_checks:
        checks = group.get("checks")
        if not isinstance(checks, list):
            continue
        if any(isinstance(item, dict) and item.get("key") == "line_installed" and not item.get("ok") for item in checks):
            needs_line_install = True
            break
    if not needs_line_install:
        return None

    inspection = inspect_line_apk_sources()
    available = bool(inspection.get("available"))
    return _check_item(
        key="line_apk_available",
        ok=available,
        severity="blocker",
        message="已找到可用 LINE APK。" if available else "尚未找到可用 LINE APK，安裝流程會卡在 `apk_not_found`。",
    )


def _build_device_check(device_id: str, customer_id: str, adb_available: bool) -> dict[str, object]:
    customer = load_customer_config(customer_id)
    items: list[dict[str, object]] = []
    status: dict[str, object] | None = None
    if adb_available:
        try:
            status = get_device_status(device_id)
        except (AdbError, RuntimeError) as exc:
            items.append(
                _check_item(
                    key="device_status_fetch",
                    ok=False,
                    severity="blocker",
                    message=f"無法讀取裝置狀態：{exc}",
                )
            )
    else:
        items.append(
            _check_item(
                key="device_status_fetch",
                ok=False,
                severity="blocker",
                message="ADB 不可用，無法讀取裝置狀態。",
            )
        )

    if status is not None:
        items.extend(
            [
                _check_item(
                    key="boot_completed",
                    ok=bool(status.get("boot_completed")),
                    severity="blocker",
                    message="模擬器已完成開機。" if status.get("boot_completed") else "模擬器尚未完成開機。",
                ),
                _check_item(
                    key="line_installed",
                    ok=bool(status.get("line_installed")),
                    severity="blocker",
                    message="LINE 已安裝。" if status.get("line_installed") else "LINE 尚未安裝到模擬器。",
                ),
                _check_item(
                    key="line_active",
                    ok=bool(status.get("line_active")),
                    severity="warning",
                    message="LINE 目前在前景，可直接做讀取/送出驗證。" if status.get("line_active") else "LINE 目前不在前景，巡邏會安全跳過。",
                ),
            ]
        )

    return {
        "device_id": device_id,
        "customer_id": customer_id,
        "customer_name": customer.display_name,
        "checks": items,
    }


def _build_community_check(community: object) -> dict[str, object]:
    customer_id = getattr(community, "customer_id")
    community_id = getattr(community, "community_id")
    root = customer_root(customer_id)
    persona_name = str(getattr(community, "persona"))
    persona_path = root / "souls" / f"{persona_name}.md"
    playbook_path = root / "playbooks" / "review_rules.md"

    coords_ready = all(
        getattr(community, field) is not None
        for field in ("input_x", "input_y", "send_x", "send_y")
    )
    coordinate_source = str(getattr(community, "coordinate_source", "missing"))

    items = [
        _check_item(
            key="persona_exists",
            ok=persona_path.exists(),
            severity="blocker",
            message=f"Persona 檔已存在：{persona_name}" if persona_path.exists() else f"缺少 persona 檔：{persona_path.name}",
        ),
        _check_item(
            key="playbook_exists",
            ok=playbook_path.exists(),
            severity="warning",
            message="Playbook 已存在。" if playbook_path.exists() else "尚未建立 review_rules.md，AI 規則上下文會較弱。",
        ),
        _check_item(
            key="send_coordinates_ready",
            ok=coords_ready,
            severity="blocker",
            message=(
                f"送出座標已配置，來源：{coordinate_source}。"
                if coords_ready
                else "尚未配置 input/send 座標，無法安全發送。"
            ),
        ),
        _check_item(
            key="patrol_interval_valid",
            ok=int(getattr(community, "patrol_interval_minutes")) > 0,
            severity="warning",
            message="巡邏間隔設定有效。" if int(getattr(community, "patrol_interval_minutes")) > 0 else "巡邏間隔需大於 0。",
        ),
    ]
    return {
        "customer_id": customer_id,
        "community_id": community_id,
        "community_name": getattr(community, "display_name"),
        "device_id": getattr(community, "device_id"),
        "checks": items,
    }


def _check_item(key: str, ok: bool, severity: str, message: str) -> dict[str, object]:
    return {
        "key": key,
        "ok": ok,
        "severity": "info" if ok else severity,
        "message": message,
    }


def _flatten_check_items(groups: list[dict[str, object]]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for group in groups:
        checks = group.get("checks")
        if isinstance(checks, list):
            for item in checks:
                if isinstance(item, dict):
                    items.append(item)
    return items


def _next_actions(blockers: list[dict[str, object]], warnings: list[dict[str, object]]) -> list[str]:
    actions: list[str] = []
    blocker_keys = {item["key"] for item in blockers}
    warning_keys = {item["key"] for item in warnings}

    if "lark_app_credentials" in blocker_keys:
        actions.append("確認 `.env` 中的 `LARK_APP_ID` 與 `LARK_APP_SECRET`，再重新執行 `python3 scripts/lark_auth_check.py`。")
    if "line_installed" in blocker_keys:
        actions.append("將 LINE APK 安裝到 emulator，完成登入後再做 OpenChat 巡邏驗證。")
    if "line_apk_available" in blocker_keys:
        actions.append("先把 LINE APK 放到 `~/Downloads/line.apk`，或設定 `ECHO_LINE_APK_PATH` 指向 APK。")
    if "send_coordinates_ready" in blocker_keys:
        actions.append("校準每個 community 的 `input_x/input_y/send_x/send_y`，送出流程才會解除封鎖。")
    if "adb_available" in blocker_keys:
        actions.append("修復 ADB 路徑或 platform-tools 安裝，恢復裝置治理能力。")
    if "lark_verification_token" in warning_keys:
        actions.append("補上 `LARK_VERIFICATION_TOKEN`，讓正式 webhook 驗證完整。")
    if "line_active" in warning_keys:
        actions.append("需要做讀取或送出驗證時，先把 LINE 切到前景。")
    return actions[:8]
