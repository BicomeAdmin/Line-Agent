from __future__ import annotations

from app.storage.config_loader import load_devices_config
from app.workflows.device_status import get_device_status
from app.workflows.line_install import inspect_line_apk_sources
from app.workflows.play_store_install import has_play_store


def get_line_apk_status() -> dict[str, object]:
    inspection = inspect_line_apk_sources()
    devices = []
    devices_needing_line = 0
    devices_with_play_store = 0
    for device in load_devices_config():
        status = get_device_status(device.device_id)
        line_installed = bool(status.get("line_installed"))
        boot_completed = bool(status.get("boot_completed"))
        play_store = boot_completed and has_play_store(device.device_id)
        if play_store:
            devices_with_play_store += 1
        if not line_installed:
            devices_needing_line += 1
        devices.append(
            {
                "device_id": device.device_id,
                "customer_id": device.customer_id,
                "line_installed": line_installed,
                "boot_completed": boot_completed,
                "play_store_available": play_store,
            }
        )

    return {
        "status": "ok",
        "apk_inspection": inspection,
        "devices_needing_line": devices_needing_line,
        "devices_with_play_store": devices_with_play_store,
        "devices": devices,
        "next_actions": _next_actions(inspection, devices_needing_line, devices_with_play_store),
    }


def _next_actions(
    inspection: dict[str, object],
    devices_needing_line: int,
    devices_with_play_store: int = 0,
) -> list[str]:
    if devices_needing_line == 0:
        return ["所有裝置都已安裝 LINE，可直接進入 OpenChat 驗證。"]
    selected_path = inspection.get("selected_path")
    if inspection.get("available") and isinstance(selected_path, str):
        return [
            f"已找到可用 LINE APK：{selected_path}",
            "下一步可執行 `python3 scripts/install_line_app.py emulator-5554`。",
        ]
    if devices_with_play_store > 0:
        return [
            "建議走 Play Store 路線（供應鏈乾淨、可自動更新）：",
            "  1. `python3 scripts/open_line_play_store.py emulator-5554` 直接跳到 LINE 頁面。",
            "  2. 在模擬器上完成一次性 Google 帳號登入（如未登入），點擊「安裝」。",
            "  3. `python3 scripts/wait_for_line_installed.py emulator-5554` 輪詢安裝完成並寫入稽核。",
            "若要改走側載：將 APK 放到 `~/Downloads/`（任何 `*line*.apk` 檔名皆可），再 `python3 scripts/install_line_app.py emulator-5554`。",
        ]
    rejected = inspection.get("rejected_too_small") or []
    if rejected:
        return [
            f"找到 {len(rejected)} 個 .apk，但檔案大小看起來不像完整 LINE APK：{rejected[0]}",
            "請確認 APK 是否下載完整（正常應在 100MB 以上），重新放回 `~/Downloads/line.apk` 或設定 `ECHO_LINE_APK_PATH`。",
        ]
    return [
        "目前找不到 LINE APK，請將 APK 放到 `~/Downloads/line.apk`，或設定 `ECHO_LINE_APK_PATH`。",
        "也可在 `~/Downloads` 放任意 `*line*.apk` 檔名，系統會自動偵測。",
        "APK 就緒後，再執行 `python3 scripts/install_line_app.py emulator-5554`。",
    ]
