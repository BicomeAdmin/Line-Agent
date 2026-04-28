from __future__ import annotations

from pathlib import Path

from app.adb.client import AdbClient
from app.adb.line_app import check_current_app
from app.adb.uiautomator import dump_ui_xml
from app.core.audit import append_audit_event
from app.parsing.xml_cleaner import extract_text_nodes
from app.storage.config_loader import load_all_communities, load_customer_config
from app.storage.paths import customer_root
from app.workflows.device_status import get_device_status


def validate_openchat_session(customer_id: str | None = None, community_id: str | None = None) -> dict[str, object]:
    items = []
    for community in load_all_communities():
        if customer_id and community.customer_id != customer_id:
            continue
        if community_id and community.community_id != community_id:
            continue
        item = _build_openchat_item(community)
        append_audit_event(
            community.customer_id,
            "openchat_validation_checked",
            {
                "community_id": community.community_id,
                "device_id": community.device_id,
                "status": item["status"],
                "reason": item.get("reason"),
                "matched_title": item.get("matched_title"),
            },
        )
        items.append(item)
    return {
        "status": "ok",
        "count": len(items),
        "ready_count": sum(1 for item in items if item["status"] == "ok"),
        "items": items,
    }


def _build_openchat_item(community: object) -> dict[str, object]:
    customer = load_customer_config(community.customer_id)
    device_status = get_device_status(community.device_id)
    base = {
        "customer_id": community.customer_id,
        "customer_name": customer.display_name,
        "community_id": community.community_id,
        "community_name": community.display_name,
        "device_id": community.device_id,
        "line_active": bool(device_status.get("line_active")),
        "foreground_package": device_status.get("foreground_package"),
    }
    if not base["line_active"]:
        return {
            **base,
            "status": "blocked",
            "reason": "line_not_foreground",
            "message": "LINE 不在前景，尚未驗證到目標 OpenChat。",
            "title_candidates": _title_candidates(community.display_name),
            "matched_title": None,
            "visible_text_samples": [],
        }

    client = AdbClient(device_id=community.device_id)
    if not check_current_app(client):
        return {
            **base,
            "status": "blocked",
            "reason": "line_check_failed",
            "message": "前景包名不是 LINE，請重新打開 LINE。",
            "title_candidates": _title_candidates(community.display_name),
            "matched_title": None,
            "visible_text_samples": [],
        }

    xml_path = _openchat_dump_path(community.customer_id, community.community_id)
    try:
        dumped_xml = dump_ui_xml(client, xml_path)
        texts = extract_text_nodes(dumped_xml.read_text(encoding="utf-8"))
    except RuntimeError as exc:
        return {
            **base,
            "status": "blocked",
            "reason": "uiautomator_dump_failed",
            "message": str(exc),
            "title_candidates": _title_candidates(community.display_name),
            "matched_title": None,
            "visible_text_samples": [],
        }

    matched_title = _match_openchat_title(texts, community.display_name)
    if matched_title is None:
        return {
            **base,
            "status": "blocked",
            "reason": "target_openchat_not_visible",
            "message": "LINE 已在前景，但目前畫面看起來不是目標 OpenChat。",
            "title_candidates": _title_candidates(community.display_name),
            "matched_title": None,
            "visible_text_samples": texts[:12],
            "xml_path": str(dumped_xml),
        }

    return {
        **base,
        "status": "ok",
        "reason": "matched_target_openchat",
        "message": f"已確認目前畫面包含目標 OpenChat 標題：{matched_title}",
        "title_candidates": _title_candidates(community.display_name),
        "matched_title": matched_title,
        "visible_text_samples": texts[:12],
        "xml_path": str(dumped_xml),
    }


def _title_candidates(display_name: str) -> list[str]:
    normalized = display_name.strip()
    candidates = [normalized]
    for separator in (" - ", "-", "｜", "|"):
        if separator in normalized:
            parts = [part.strip() for part in normalized.split(separator) if part.strip()]
            candidates.extend(parts)
            if parts:
                candidates.append(parts[-1])
    unique: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in unique:
            unique.append(candidate)
    return unique


def _match_openchat_title(texts: list[str], display_name: str) -> str | None:
    for candidate in _title_candidates(display_name):
        normalized_candidate = _normalize_text(candidate)
        if not normalized_candidate:
            continue
        for original in texts:
            normalized_text = _normalize_text(original)
            if normalized_candidate == normalized_text:
                return original
            if normalized_text.startswith(normalized_candidate) and len(normalized_text) - len(normalized_candidate) <= 4:
                return original
    return None


def _normalize_text(value: str) -> str:
    return "".join(value.lower().split())


def _openchat_dump_path(customer_id: str, community_id: str) -> Path:
    return customer_root(customer_id) / "data" / "raw_xml" / f"{community_id}-openchat-validation.xml"
