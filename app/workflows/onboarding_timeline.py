from __future__ import annotations

from app.core.audit import read_all_audit_events
from app.storage.config_loader import load_all_communities, load_customer_config


RELEVANT_EVENT_TYPES = {
    "community_calibration_saved",
    "community_patrol_review_ready",
    "community_patrol_skipped",
    "scheduled_patrol_processed",
    "send_attempt",
    "line_session_prepare_blocked",
    "line_session_prepared",
    "device_recovery_started_avd",
    "device_recovery_blocked",
    "device_recovery_ready",
    "line_install_started",
    "line_install_completed",
    "line_install_blocked",
    "openchat_validation_checked",
    "job_completed",
}


def get_onboarding_timeline(customer_id: str | None = None, community_id: str | None = None) -> dict[str, object]:
    items = []
    for community in load_all_communities():
        if customer_id and community.customer_id != customer_id:
            continue
        if community_id and community.community_id != community_id:
            continue
        items.append(_build_timeline_item(community))
    return {
        "status": "ok",
        "count": len(items),
        "items": items,
    }


def _build_timeline_item(community: object) -> dict[str, object]:
    customer = load_customer_config(community.customer_id)
    audits = read_all_audit_events(community.customer_id)
    timeline = []
    milestones = {
        "line_session_attempted": False,
        "line_install_attempted": False,
        "openchat_verified": False,
        "acceptance_checked": False,
        "calibration_saved": False,
        "patrol_attempted": False,
        "send_attempted": False,
        "first_send_completed": False,
    }

    for event in audits:
        event_type = event.get("event_type")
        if event_type not in RELEVANT_EVENT_TYPES:
            continue
        timeline_item = _timeline_item_for_event(event, community.community_id, community.device_id)
        if timeline_item is None:
            continue
        timeline.append(timeline_item)
        milestone = timeline_item.get("milestone")
        if isinstance(milestone, str) and milestone in milestones:
            milestones[milestone] = True
        extra_milestones = timeline_item.get("extra_milestones")
        if isinstance(extra_milestones, list):
            for extra in extra_milestones:
                if isinstance(extra, str) and extra in milestones:
                    milestones[extra] = True

    return {
        "customer_id": community.customer_id,
        "customer_name": customer.display_name,
        "community_id": community.community_id,
        "community_name": community.display_name,
        "device_id": community.device_id,
        "timeline_count": len(timeline),
        "milestones": milestones,
        "latest_stage": timeline[-1]["stage"] if timeline else "no_activity",
        "timeline": _strip_internal_fields(timeline[-20:]),
    }


def _timeline_item_for_event(event: dict[str, object], community_id: str, device_id: str) -> dict[str, object] | None:
    event_type = str(event.get("event_type"))
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None

    payload_community_id = payload.get("community_id")
    payload_device_id = payload.get("device_id")
    if payload_community_id not in (None, community_id):
        return None
    if payload_device_id not in (None, device_id):
        return None

    if event_type == "community_calibration_saved":
        return _build_timeline_entry(event, "calibration_saved", "calibration_ready", "已保存社群送出座標。")
    if event_type == "line_install_started":
        return _build_timeline_entry(event, "line_install_attempted", "line_install_started", "已開始安裝 LINE APK。")
    if event_type == "line_install_completed":
        return _build_timeline_entry(event, "line_install_attempted", "line_install_completed", "LINE 安裝流程完成。")
    if event_type == "line_install_blocked":
        reason = payload.get("reason", "unknown")
        return _build_timeline_entry(event, "line_install_attempted", "line_install_blocked", f"LINE 安裝流程受阻：{reason}")
    if event_type == "openchat_validation_checked":
        status = payload.get("status", "unknown")
        matched_title = payload.get("matched_title")
        summary = f"OpenChat 驗證結果：{status}"
        if isinstance(matched_title, str) and matched_title:
            summary += f"（{matched_title}）"
        milestone = "openchat_verified" if status == "ok" else "line_session_attempted"
        stage = "openchat_verified" if status == "ok" else "openchat_validation_blocked"
        return _build_timeline_entry(event, milestone, stage, summary)
    if event_type == "device_recovery_started_avd":
        return _build_timeline_entry(event, "line_session_attempted", "device_recovery_started", "已嘗試啟動 AVD 恢復裝置。")
    if event_type == "device_recovery_blocked":
        reason = payload.get("reason", "unknown")
        return _build_timeline_entry(event, "line_session_attempted", "device_recovery_blocked", f"裝置恢復失敗：{reason}")
    if event_type == "device_recovery_ready":
        return _build_timeline_entry(event, "line_session_attempted", "device_recovery_ready", "裝置恢復完成，可繼續後續流程。")
    if event_type == "line_session_prepare_blocked":
        reason = payload.get("reason", "unknown")
        return _build_timeline_entry(event, "line_session_attempted", "line_prepare_blocked", f"LINE 工作階段準備失敗：{reason}")
    if event_type == "line_session_prepared":
        status = payload.get("status", "unknown")
        return _build_timeline_entry(event, "line_session_attempted", "line_prepare_result", f"LINE 工作階段準備結果：{status}")
    if event_type == "community_patrol_review_ready":
        reason = payload.get("reason", "unknown")
        return _build_timeline_entry(event, "patrol_attempted", "review_ready", f"巡邏產出待審稿件，原因：{reason}")
    if event_type == "community_patrol_skipped":
        reason = payload.get("reason", "unknown")
        return _build_timeline_entry(event, "patrol_attempted", "patrol_skipped", f"巡邏略過，原因：{reason}")
    if event_type == "scheduled_patrol_processed":
        status = payload.get("status", "unknown")
        return _build_timeline_entry(event, "patrol_attempted", "scheduled_patrol_processed", f"排程巡邏處理完成：{status}")
    if event_type == "send_attempt":
        status = payload.get("status", "unknown")
        entry = _build_timeline_entry(event, "send_attempted", "send_attempt", f"發送嘗試結果：{status}")
        if status == "ok":
            entry["extra_milestones"] = ["first_send_completed"]
        return entry
    if event_type == "job_completed":
        action = payload.get("action")
        result = payload.get("result")
        if not isinstance(result, dict):
            return None
        if action == "acceptance_status":
            item = _acceptance_item_from_result(result, community_id)
            if item is None:
                return None
            return _build_timeline_entry(
                event,
                "acceptance_checked",
                "acceptance_checked",
                f"驗收檢查 stage：{item.get('stage', 'unknown')}",
            )
        if action == "community_status":
            item = _community_item_from_result(result, community_id)
            if item is None:
                return None
            ready = "ready" if item.get("coordinates_ready") else "not_ready"
            return _build_timeline_entry(
                event,
                "acceptance_checked",
                "community_status_checked",
                f"社群狀態檢查完成，座標狀態：{ready}",
            )
        if action == "prepare_line_session":
            status = result.get("status", "unknown")
            return _build_timeline_entry(event, "line_session_attempted", "line_prepare_result", f"LINE 工作階段準備結果：{status}")
        if action == "ensure_device_ready":
            status = result.get("status", "unknown")
            return _build_timeline_entry(event, "line_session_attempted", "device_recovery_result", f"裝置恢復流程結果：{status}")
        if action == "install_line_app":
            status = result.get("status", "unknown")
            return _build_timeline_entry(event, "line_install_attempted", "line_install_result", f"LINE 安裝流程結果：{status}")
        if action == "openchat_validation":
            item = _community_item_from_result(result, community_id)
            if item is None:
                return None
            status = item.get("status", "unknown")
            milestone = "openchat_verified" if status == "ok" else "line_session_attempted"
            stage = "openchat_validation_result" if status == "ok" else "openchat_validation_blocked"
            return _build_timeline_entry(event, milestone, stage, f"OpenChat 驗證結果：{status}")
        if action == "calibration_status":
            item = _calibration_item_from_result(result, community_id)
            if item is None:
                return None
            ready = "ready" if item.get("coordinates_ready") else "missing"
            return _build_timeline_entry(event, "acceptance_checked", "calibration_status_checked", f"校準狀態檢查完成：{ready}")
    return None


def _build_timeline_entry(event: dict[str, object], milestone: str, stage: str, summary: str) -> dict[str, object]:
    return {
        "timestamp": event.get("timestamp"),
        "event_type": event.get("event_type"),
        "milestone": milestone,
        "stage": stage,
        "summary": summary,
    }


def _strip_internal_fields(timeline: list[dict[str, object]]) -> list[dict[str, object]]:
    return [{k: v for k, v in entry.items() if k != "extra_milestones"} for entry in timeline]


def _acceptance_item_from_result(result: dict[str, object], community_id: str) -> dict[str, object] | None:
    for item in result.get("items", []):
        if isinstance(item, dict) and item.get("community_id") == community_id:
            return item
    return None


def _community_item_from_result(result: dict[str, object], community_id: str) -> dict[str, object] | None:
    for item in result.get("items", []):
        if isinstance(item, dict) and item.get("community_id") == community_id:
            return item
    return None


def _calibration_item_from_result(result: dict[str, object], community_id: str) -> dict[str, object] | None:
    for item in result.get("items", []):
        if isinstance(item, dict) and item.get("community_id") == community_id:
            return item
    return None
