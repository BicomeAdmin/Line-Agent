from __future__ import annotations

def get_action_queue(customer_id: str | None = None, community_id: str | None = None) -> dict[str, object]:
    from app.workflows.project_snapshot import get_project_snapshot

    snapshot = get_project_snapshot(customer_id=customer_id, community_id=community_id)
    spotlight = snapshot.get("spotlight")
    sections = snapshot.get("sections")
    queue = build_action_queue_items(spotlight if isinstance(spotlight, dict) else {}, sections if isinstance(sections, dict) else {})

    return {
        "status": "ok",
        "queue_count": len(queue),
        "items": queue,
        "snapshot_summary": snapshot.get("summary"),
    }


def build_action_queue_items(spotlight: dict[str, object], sections: dict[str, object]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    community_id = spotlight.get("community_id")
    community_name = spotlight.get("community_name")

    line_apk = sections.get("line_apk")
    devices_needing_line = 0
    apk_available = False
    if isinstance(line_apk, dict):
        devices_needing_line = int(line_apk.get("devices_needing_line") or 0)
        apk_inspection = line_apk.get("apk_inspection")
        if isinstance(apk_inspection, dict):
            apk_available = bool(apk_inspection.get("available"))

    # apk_stage only matters while at least one device still needs LINE installed.
    if devices_needing_line > 0 and not apk_available:
        items.append(
            _queue_item(
                "apk_stage",
                1,
                "取得 LINE APK",
                "將 LINE APK 放到 `~/Downloads/line.apk`，或設定 `ECHO_LINE_APK_PATH`。也可下載 .apkm split bundle 用 `adb install-multiple` 直接側載。",
                "blocked_external",
                community_id,
                community_name,
            )
        )

    if spotlight.get("acceptance_stage") == "line_missing":
        items.append(
            _queue_item(
                "install_line",
                2,
                "安裝 LINE",
                "首選 Play Store：`python3 scripts/open_line_play_store.py emulator-5554` → 模擬器登入 Google 點安裝 → `python3 scripts/wait_for_line_installed.py emulator-5554`。側載備援：`python3 scripts/install_line_app.py emulator-5554`。",
                "pending",
                community_id,
                community_name,
            )
        )

    if spotlight.get("openchat_status") != "ok":
        items.append(
            _queue_item(
                "open_target_openchat",
                3,
                "進入目標 OpenChat",
                "完成 LINE 登入後，手動切到目標 OpenChat，再執行 OpenChat 驗證。",
                "pending",
                community_id,
                community_name,
            )
        )

    if spotlight.get("coordinates_ready") is False:
        items.append(
            _queue_item(
                "calibrate_send",
                4,
                "校準發送座標",
                "在目標 OpenChat 可見後完成 `input_x/input_y/send_x/send_y` 校準。",
                "pending",
                community_id,
                community_name,
            )
        )

    items.append(
        _queue_item(
            "hil_demo",
            5,
            "跑第一個 HIL demo",
            "完成真實 read -> draft -> review -> send 迴圈驗證。",
            "pending",
            community_id,
            community_name,
        )
    )
    return items


def _queue_item(
    item_id: str,
    priority: int,
    title: str,
    description: str,
    status: str,
    community_id: object,
    community_name: object,
) -> dict[str, object]:
    return {
        "item_id": item_id,
        "priority": priority,
        "title": title,
        "description": description,
        "status": status,
        "community_id": community_id,
        "community_name": community_name,
    }
