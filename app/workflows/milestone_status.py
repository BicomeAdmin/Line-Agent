from __future__ import annotations

from app.workflows.project_snapshot import get_project_snapshot


def get_milestone_status(customer_id: str | None = None, community_id: str | None = None) -> dict[str, object]:
    snapshot = get_project_snapshot(customer_id=customer_id, community_id=community_id)
    spotlight = snapshot.get("spotlight")
    sections = snapshot.get("sections")

    spotlight_map = spotlight if isinstance(spotlight, dict) else {}
    sections_map = sections if isinstance(sections, dict) else {}
    milestones = _build_milestones(spotlight_map, sections_map)
    current = _current_milestone(milestones)

    return {
        "status": "ok",
        "current_milestone": current,
        "milestones": milestones,
        "snapshot_summary": snapshot.get("summary"),
    }


def _build_milestones(spotlight: dict[str, object], sections: dict[str, object]) -> list[dict[str, object]]:
    line_apk = sections.get("line_apk")
    line_apk_available = False
    devices_needing_line = 0
    if isinstance(line_apk, dict):
        devices_needing_line = int(line_apk.get("devices_needing_line") or 0)
        apk_inspection = line_apk.get("apk_inspection")
        if isinstance(apk_inspection, dict):
            line_apk_available = bool(apk_inspection.get("available"))

    # Stage 1 is "real LINE chain unblocked" — completed once the device actually
    # has LINE installed (regardless of whether a loose .apk still sits in Downloads).
    line_chain_ready = devices_needing_line == 0
    milestone_1 = _milestone(
        "stage_1_line_chain",
        1,
        "Unblock The Real LINE Chain",
        completed=line_chain_ready,
        active=not line_chain_ready,
        status="completed" if line_chain_ready else ("blocked_external" if not line_apk_available else "ready"),
        note="LINE 已安裝到目標裝置。" if line_chain_ready else "需要先取得可用 LINE APK。",
    )

    openchat_ok = spotlight.get("openchat_status") == "ok"
    milestone_2 = _milestone(
        "stage_2_openchat",
        2,
        "Reach Target OpenChat",
        completed=openchat_ok,
        active=line_chain_ready and not openchat_ok,
        status="completed" if openchat_ok else ("pending" if line_chain_ready else "blocked"),
        note="目標 OpenChat 已驗證。" if openchat_ok else "需要完成 LINE 登入並切到目標 OpenChat。",
    )

    coordinates_ready = spotlight.get("coordinates_ready") is True
    milestone_3 = _milestone(
        "stage_3_readback_calibration",
        3,
        "Readback And Calibration",
        completed=coordinates_ready,
        active=openchat_ok and not coordinates_ready,
        status="completed" if coordinates_ready else ("pending" if openchat_ok else "blocked"),
        note="座標已校準。" if coordinates_ready else "需要讀取真實聊天並完成第一輪座標校準。",
    )

    acceptance_stage = spotlight.get("acceptance_stage")
    ready_for_hil = acceptance_stage == "ready_for_hil"
    milestone_4 = _milestone(
        "stage_4_hil_demo",
        4,
        "Human-In-The-Loop Demo",
        completed=False,
        active=coordinates_ready and not ready_for_hil,
        status="pending" if coordinates_ready and not ready_for_hil else ("ready" if ready_for_hil else "blocked"),
        note="需要跑第一個真實 read -> draft -> review -> send 迴圈。",
    )

    milestone_5 = _milestone(
        "stage_5_operational_hardening",
        5,
        "Operational Hardening",
        completed=False,
        active=ready_for_hil,
        status="pending" if ready_for_hil else "blocked",
        note="在第一個 HIL demo 成功後再進入硬化階段。",
    )

    return [milestone_1, milestone_2, milestone_3, milestone_4, milestone_5]


def _current_milestone(milestones: list[dict[str, object]]) -> dict[str, object] | None:
    for item in milestones:
        if item.get("active"):
            return item
    return milestones[0] if milestones else None


def _milestone(
    milestone_id: str,
    order: int,
    title: str,
    completed: bool,
    active: bool,
    status: str,
    note: str,
) -> dict[str, object]:
    return {
        "milestone_id": milestone_id,
        "order": order,
        "title": title,
        "completed": completed,
        "active": active,
        "status": status,
        "note": note,
    }
