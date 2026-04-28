from __future__ import annotations

import re
from dataclasses import asdict, dataclass


DEVICE_PATTERN = re.compile(r"(emulator-\d+)")
COMMUNITY_PATTERN = re.compile(r"(openchat_[A-Za-z0-9_-]+)")
COUNT_PATTERN = re.compile(r"(\d+)\s*(?:筆|則|條|个|條訊息|messages?)", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedCommand:
    action: str
    raw_text: str
    device_id: str | None = None
    community_id: str | None = None
    category: str | None = None
    limit: int | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def parse_command(text: str) -> ParsedCommand:
    normalized = text.strip()
    lowered = normalized.lower()
    device_id = _extract_device_id(normalized)
    community_id = _extract_community_id(normalized)
    limit = _extract_limit(normalized)

    if any(keyword in normalized for keyword in ("部署檢查", "就緒檢查", "ready", "readiness", "preflight")):
        return ParsedCommand(action="readiness_status", raw_text=normalized, device_id=device_id)

    if any(keyword in normalized for keyword in ("校準狀態", "校正狀態", "座標狀態", "calibration")):
        return ParsedCommand(action="calibration_status", raw_text=normalized, device_id=device_id)

    if any(keyword in normalized for keyword in ("社群狀態", "群組狀態", "community status", "community_status")):
        return ParsedCommand(action="community_status", raw_text=normalized, device_id=device_id, community_id=community_id)

    if any(keyword in normalized for keyword in ("驗收", "acceptance", "上線檢查", "accept")):
        return ParsedCommand(action="acceptance_status", raw_text=normalized, device_id=device_id, community_id=community_id)

    if any(keyword in normalized for keyword in ("準備LINE", "打开LINE", "打開LINE", "喚醒LINE", "prepare line")):
        return ParsedCommand(action="prepare_line_session", raw_text=normalized, device_id=device_id)

    if any(keyword in normalized for keyword in ("修復裝置", "恢復裝置", "ensure device", "recover device")):
        return ParsedCommand(action="ensure_device_ready", raw_text=normalized, device_id=device_id)

    if any(keyword in normalized for keyword in ("安裝LINE", "安装LINE", "install line")):
        return ParsedCommand(action="install_line_app", raw_text=normalized, device_id=device_id)

    if any(keyword in normalized for keyword in ("LINE APK 狀態", "LINE APK状态", "apk status", "line apk")):
        return ParsedCommand(action="line_apk_status", raw_text=normalized, device_id=device_id)

    if any(keyword in normalized for keyword in ("專案快照", "项目快照", "project snapshot", "snapshot")):
        return ParsedCommand(action="project_snapshot", raw_text=normalized, device_id=device_id, community_id=community_id)

    if any(keyword in normalized for keyword in ("行動隊列", "行动队列", "action queue", "next actions")):
        return ParsedCommand(action="action_queue", raw_text=normalized, device_id=device_id, community_id=community_id)

    if any(keyword in normalized for keyword in ("里程碑", "里程碑狀態", "milestone", "roadmap status")):
        return ParsedCommand(action="milestone_status", raw_text=normalized, device_id=device_id, community_id=community_id)

    if any(keyword in normalized for keyword in ("OpenChat 驗證", "OpenChat验证", "openchat驗證", "驗證OpenChat", "验证OpenChat", "openchat validation")):
        return ParsedCommand(action="openchat_validation", raw_text=normalized, device_id=device_id, community_id=community_id)

    if any(keyword in lowered for keyword in ("狀態", "status", "巡檢", "health")):
        action = "device_status" if device_id or "裝置" in normalized or "device" in lowered else "system_status"
        return ParsedCommand(action=action, raw_text=normalized, device_id=device_id)

    if any(keyword in normalized for keyword in ("巡邏", "巡逻", "巡檢", "巡检", "patrol")):
        return ParsedCommand(action="patrol_device", raw_text=normalized, device_id=device_id)

    # Draft / "speak in community" verbs — checked BEFORE read_chat so that natural
    # phrasing like "請幫忙在 openchat_002 接話" doesn't get swallowed by read_chat.
    if any(keyword in normalized for keyword in (
        "擬稿", "草稿", "分析群聊", "回覆建議", "回复建議",
        "說話", "发言", "發言", "開口", "开口", "接話", "接话",
        "幫忙說", "帮忙说", "聊兩句", "聊两句", "幫忙在", "帮忙在",
        "幫我在", "帮我在", "draft",
    )):
        return ParsedCommand(
            action="draft_reply",
            raw_text=normalized,
            device_id=device_id,
            community_id=community_id,
            limit=limit or 20,
        )

    if any(keyword in normalized for keyword in ("讀取", "抓取", "回傳", "聊天", "對話", "訊息")):
        return ParsedCommand(action="read_chat", raw_text=normalized, device_id=device_id, limit=limit or 10)

    if any(keyword in normalized for keyword in ("打開LINE", "開LINE", "打开LINE", "open line")):
        return ParsedCommand(action="open_line", raw_text=normalized, device_id=device_id)

    if any(keyword in normalized for keyword in ("推薦", "建议", "建議", "suggest")):
        return ParsedCommand(action="suggest", raw_text=normalized, category=_extract_category(normalized))

    return ParsedCommand(action="system_status", raw_text=normalized, device_id=device_id)


def _extract_device_id(text: str) -> str | None:
    match = DEVICE_PATTERN.search(text)
    return match.group(1) if match else None


def _extract_community_id(text: str) -> str | None:
    match = COMMUNITY_PATTERN.search(text)
    return match.group(1) if match else None


def _extract_limit(text: str) -> int | None:
    match = COUNT_PATTERN.search(text)
    return int(match.group(1)) if match else None


def _extract_category(text: str) -> str:
    mapping = {
        "母嬰": "maternity",
        "育兒": "maternity",
        "投资": "investment",
        "投資": "investment",
        "美妝": "beauty",
        "寵物": "pet",
        "宠物": "pet",
    }
    for keyword, category in mapping.items():
        if keyword in text:
            return category
    return "general"
