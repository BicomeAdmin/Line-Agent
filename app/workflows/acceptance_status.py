from __future__ import annotations

from app.adb.client import AdbClient
from app.ai.context_bundle import load_context_bundle
from app.storage.config_loader import load_all_communities, load_customer_config
from app.storage.paths import default_raw_xml_path
from app.workflows.device_status import get_device_status
from app.workflows.openchat_validation import validate_openchat_session
from app.workflows.read_chat import read_recent_chat
from app.workflows.send_preview import preview_send


def get_acceptance_status(customer_id: str | None = None, community_id: str | None = None) -> dict[str, object]:
    items = []
    for community in load_all_communities():
        if customer_id and community.customer_id != customer_id:
            continue
        if community_id and community.community_id != community_id:
            continue
        items.append(_build_acceptance_item(community))

    return {
        "status": "ok",
        "count": len(items),
        "ready_count": sum(1 for item in items if item["ready"]),
        "items": items,
    }


def _build_acceptance_item(community: object) -> dict[str, object]:
    customer = load_customer_config(community.customer_id)
    device_status = get_device_status(community.device_id)
    bundle = load_context_bundle(community.customer_id, community.community_id)
    send_preview_result = preview_send(community.customer_id, community.community_id, "Project Echo 驗收預演訊息")
    chat_probe = _probe_chat_readability(community.customer_id, community.community_id, community.device_id)
    openchat_probe = _probe_openchat_session(community.customer_id, community.community_id)

    checklist = [
        _step("device_booted", bool(device_status.get("boot_completed")), "模擬器已開機。", "模擬器尚未開機完成。"),
        _step("line_installed", bool(device_status.get("line_installed")), "LINE 已安裝。", "LINE 尚未安裝。"),
        _step("line_foreground", openchat_probe["status"] == "ok", "已進到目標 OpenChat。", str(openchat_probe["message"])),
        _step("persona_loaded", bool(bundle.persona_text.strip()), "Persona 已載入。", "Persona 內容為空。"),
        _step("playbook_loaded", bool(bundle.playbook_text.strip()), "Playbook 已載入。", "Playbook 內容為空。"),
        _step("chat_readable", chat_probe["status"] == "ok", "可成功讀取最近對話。", chat_probe["message"]),
        _step(
            "send_preview_ready",
            send_preview_result["status"] == "ok",
            "送出座標已準備完成，可做 dry-run 預演。",
            _send_preview_block_message(send_preview_result),
        ),
    ]
    stage = _acceptance_stage(checklist)
    next_actions = _acceptance_next_actions(checklist)
    sub_checklist = _acceptance_sub_checklist(stage, community.display_name)

    return {
        "customer_id": community.customer_id,
        "customer_name": customer.display_name,
        "community_id": community.community_id,
        "community_name": community.display_name,
        "device_id": community.device_id,
        "ready": stage == "ready_for_hil",
        "stage": stage,
        "checklist": checklist,
        "sub_checklist": sub_checklist,
        "openchat_probe": openchat_probe,
        "chat_probe": chat_probe,
        "send_preview": send_preview_result,
        "next_actions": next_actions,
    }


def _probe_chat_readability(customer_id: str, community_id: str, device_id: str) -> dict[str, object]:
    try:
        messages = read_recent_chat(AdbClient(device_id=device_id), default_raw_xml_path(customer_id), limit=10)
    except RuntimeError as exc:
        return {
            "status": "blocked",
            "community_id": community_id,
            "message": str(exc),
            "message_count": 0,
        }
    return {
        "status": "ok",
        "community_id": community_id,
        "message": f"成功讀取 {len(messages)} 筆訊息。",
        "message_count": len(messages),
        "sample_messages": messages[-3:],
    }


def _probe_openchat_session(customer_id: str, community_id: str) -> dict[str, object]:
    result = validate_openchat_session(customer_id=customer_id, community_id=community_id)
    items = result.get("items", [])
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item.get("community_id") == community_id:
                return item
    return {
        "status": "blocked",
        "community_id": community_id,
        "reason": "openchat_probe_missing",
        "message": "找不到 OpenChat 驗證結果。",
    }


def _step(key: str, ok: bool, ok_message: str, blocked_message: str) -> dict[str, object]:
    return {
        "key": key,
        "ok": ok,
        "severity": "info" if ok else "blocker",
        "message": ok_message if ok else blocked_message,
    }


def _acceptance_stage(checklist: list[dict[str, object]]) -> str:
    failing = {item["key"] for item in checklist if not item["ok"]}
    if "device_booted" in failing:
        return "device_not_ready"
    if "line_installed" in failing:
        return "line_missing"
    if "line_foreground" in failing:
        return "line_not_openchat"
    if "chat_readable" in failing:
        return "chat_not_readable"
    if "send_preview_ready" in failing:
        return "send_not_calibrated"
    return "ready_for_hil"


def _acceptance_next_actions(checklist: list[dict[str, object]]) -> list[str]:
    actions: list[str] = []
    for item in checklist:
        if item["ok"]:
            continue
        if item["key"] == "device_booted":
            actions.append("先執行裝置恢復流程，確認 emulator 已啟動並完成開機。")
        elif item["key"] == "line_installed":
            actions.append("先執行 LINE 安裝流程，再登入帳號。")
        elif item["key"] == "line_foreground":
            actions.append("手動打開 LINE，確認目前停在目標 OpenChat，必要時重新執行 OpenChat 驗證。")
        elif item["key"] == "chat_readable":
            actions.append("確認目前畫面真的是聊天室訊息列表，再重新執行驗收。")
        elif item["key"] == "send_preview_ready":
            actions.append("先完成 community 座標校準，再做送出預演。")
    return actions


def _acceptance_sub_checklist(stage: str, community_display_name: str) -> list[dict[str, object]]:
    if stage == "line_not_openchat":
        return [
            {"key": "open_line", "done": False, "hint": "確認 LINE 已啟動且停在首頁。"},
            {"key": "open_openchat_tab", "done": False, "hint": "切到 OpenChat 分頁。"},
            {"key": "enter_target_room", "done": False, "hint": f"進入目標 OpenChat：{community_display_name}。"},
            {"key": "rerun_validation", "done": False, "hint": "重新執行 OpenChat 驗證確認標題比對成功。"},
        ]
    return []


def _send_preview_block_message(result: dict[str, object]) -> str:
    if result.get("status") == "blocked":
        reason = result.get("reason", "unknown")
        return f"送出預演尚未就緒：{reason}"
    return "送出預演尚未就緒。"
