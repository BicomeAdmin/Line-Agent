"""Onboard a new LINE OpenChat community into Project Echo configs.

Operator-driven flow (called via MCP `add_community` tool):
1. Extract group_id from a LINE invite URL.
2. Generate the next free community_id (openchat_NNN) under the customer.
3. Deep-link into the chat (assumes the operator has already joined as a member),
   dump UI, and read the title from the chat header — used as display_name.
4. Write the YAML config with conservative defaults (patrol 720 min, persona=default,
   no static calibration; the dynamic send-button resolver in tap_type_send handles
   coordinates).
5. Bootstrap an empty voice_profile so future compose_and_send has somewhere to land
   operator-provided samples.

Idempotency: if a community with the same group_id already exists, return that
existing one rather than creating a duplicate.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from app.adb.client import AdbClient, AdbError
from app.adb.line_app import check_current_app, open_line
from app.adb.uiautomator import dump_ui_xml
from app.core.audit import append_audit_event
from app.parsing.xml_cleaner import extract_text_nodes
from app.storage.config_loader import load_all_communities, load_devices_config
from app.storage.paths import customer_root, voice_profile_path
from app.storage.voice_profiles import set_voice_profile

INVITE_URL_RE = re.compile(r"https?://line\.me/ti/g2/([A-Za-z0-9_-]+)|line://ti/g2/([A-Za-z0-9_-]+)")
COMMUNITY_ID_RE = re.compile(r"^openchat_(\d{3,})$")
CHAT_HEADER_NOISE = {"聊天", "搜尋", "Chats", "Search", "全部", "好友", "群組", "官方帳號", "社群", "通知", "VOOM", "TODAY"}


def add_community(
    invite_url: str,
    *,
    customer_id: str,
    device_id: str | None = None,
    display_name: str | None = None,
    patrol_interval_minutes: int = 720,
    persona: str = "default",
) -> dict[str, object]:
    invite_url = (invite_url or "").strip()
    match = INVITE_URL_RE.search(invite_url)
    if not match:
        return {"status": "error", "reason": "not_a_line_invite_url", "invite_url": invite_url}
    group_id = match.group(1) or match.group(2)

    # Idempotency: already configured?
    for community in load_all_communities():
        if community.group_id and community.group_id == group_id:
            return {
                "status": "ok",
                "already_exists": True,
                "customer_id": community.customer_id,
                "community_id": community.community_id,
                "display_name": community.display_name,
                "group_id": group_id,
            }

    # Resolve device_id from devices.yaml if not specified.
    if device_id is None:
        devices = [d for d in load_devices_config() if d.customer_id == customer_id and d.enabled]
        if not devices:
            return {"status": "error", "reason": "no_device_for_customer", "customer_id": customer_id}
        device_id = devices[0].device_id

    next_community_id = _next_community_id(customer_id)

    # Best-effort: deep-link into the chat to read its real title.
    auto_display_name: str | None = None
    nav_trace: list[str] = []
    if display_name is None:
        auto_display_name, nav_trace = _detect_display_name(device_id, group_id)

    final_display_name = (display_name or auto_display_name or f"未命名社群 ({group_id[:8]}…)").strip()

    yaml_path = _community_yaml_path(customer_id, next_community_id)
    yaml_body = _format_yaml(
        community_id=next_community_id,
        display_name=final_display_name,
        invite_url=invite_url,
        group_id=group_id,
        persona=persona,
        device_id=device_id,
        patrol_interval_minutes=patrol_interval_minutes,
    )
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    yaml_path.write_text(yaml_body, encoding="utf-8")

    # Bootstrap a voice profile so the LLM brain has something to read on first compose.
    profile_path = voice_profile_path(customer_id, next_community_id)
    if not profile_path.exists():
        set_voice_profile(
            customer_id,
            next_community_id,
            _default_voice_profile(final_display_name),
            note="auto-bootstrapped on community onboarding",
        )

    append_audit_event(
        customer_id,
        "community_onboarded",
        {
            "community_id": next_community_id,
            "display_name": final_display_name,
            "device_id": device_id,
            "group_id": group_id,
            "invite_url": invite_url,
            "auto_display_name": auto_display_name,
            "nav_trace": nav_trace,
            "yaml_path": str(yaml_path),
        },
    )

    return {
        "status": "ok",
        "already_exists": False,
        "customer_id": customer_id,
        "community_id": next_community_id,
        "display_name": final_display_name,
        "device_id": device_id,
        "group_id": group_id,
        "invite_url": invite_url,
        "auto_display_name": auto_display_name,
        "yaml_path": str(yaml_path),
        "voice_profile_path": str(profile_path),
        "next_steps": [
            "操作員可手動編輯 YAML 設 patrol_interval / persona",
            "voice_profile 是 default bootstrap，請編 .md 加 Tone notes / Samples",
            "calibration 不需手設，dynamic send-button 解析會自動找 send 鈕",
        ],
    }


def refresh_community_title(
    customer_id: str,
    community_id: str,
    *,
    display_name: str | None = None,
) -> dict[str, object]:
    """Re-extract the chat title for an existing community and rewrite its YAML.

    Two modes:
      - explicit override: caller passes `display_name` directly (operator
        knows the real name and just wants to set it).
      - auto-detect (display_name is None): re-run the deep-link → dump UI →
        title extraction flow. Used when add_community's first attempt fell
        back to the placeholder "未命名社群 (xxx…)".

    Only the YAML's display_name line is rewritten; the rest of the config
    (persona, device, patrol interval, calibration nulls) stays put.
    """

    from app.storage.config_loader import load_community_config

    try:
        community = load_community_config(customer_id, community_id)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": f"community_lookup_failed:{exc}"}

    old_name = community.display_name
    trace: list[str] = []

    if display_name is not None:
        new_name = display_name.strip()
        if not new_name:
            return {"status": "error", "reason": "display_name_empty"}
        trace.append(f"explicit_override:{new_name[:40]}")
    else:
        if not community.group_id:
            return {"status": "error", "reason": "no_group_id_on_community"}
        detected, detect_trace = _detect_display_name(community.device_id, community.group_id)
        trace.extend(detect_trace)
        if not detected:
            return {
                "status": "error",
                "reason": "title_not_detected",
                "trace": trace,
                "old_display_name": old_name,
                "hint": "可以改用 display_name=... 直接指定，或先確認 LINE 已開啟並停在聊天列表",
            }
        new_name = detected.strip()

    if new_name == old_name:
        return {
            "status": "ok",
            "changed": False,
            "community_id": community_id,
            "display_name": new_name,
            "trace": trace,
        }

    yaml_path = _community_yaml_path(customer_id, community_id)
    try:
        original_text = yaml_path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"status": "error", "reason": f"yaml_read_failed:{exc}"}

    # Rewrite just the display_name line — preserve everything else (comments,
    # operator-edited fields, ordering) so this is a minimal, safe edit.
    new_text, n_subs = re.subn(
        r'^display_name:\s*.*$',
        f'display_name: "{new_name}"',
        original_text,
        count=1,
        flags=re.MULTILINE,
    )
    if n_subs == 0:
        return {"status": "error", "reason": "display_name_line_not_found_in_yaml"}

    yaml_path.write_text(new_text, encoding="utf-8")

    append_audit_event(
        customer_id,
        "community_title_refreshed",
        {
            "community_id": community_id,
            "old_display_name": old_name,
            "new_display_name": new_name,
            "mode": "explicit" if display_name is not None else "auto_detect",
            "trace": trace,
        },
    )

    return {
        "status": "ok",
        "changed": True,
        "community_id": community_id,
        "old_display_name": old_name,
        "display_name": new_name,
        "yaml_path": str(yaml_path),
        "trace": trace,
    }


def _next_community_id(customer_id: str) -> str:
    used: set[int] = set()
    for community in load_all_communities():
        if community.customer_id != customer_id:
            continue
        m = COMMUNITY_ID_RE.match(community.community_id)
        if m:
            used.add(int(m.group(1)))
    n = 1
    while n in used:
        n += 1
    return f"openchat_{n:03d}"


def _community_yaml_path(customer_id: str, community_id: str) -> Path:
    return customer_root(customer_id) / "communities" / f"{community_id}.yaml"


def _format_yaml(
    *,
    community_id: str,
    display_name: str,
    invite_url: str,
    group_id: str,
    persona: str,
    device_id: str,
    patrol_interval_minutes: int,
) -> str:
    return (
        f"community_id: {community_id}\n"
        f"display_name: \"{display_name}\"\n"
        f"invite_url: \"{invite_url}\"\n"
        f"group_id: \"{group_id}\"\n"
        f"persona: {persona}\n"
        f"device_id: {device_id}\n"
        f"patrol_interval_minutes: {patrol_interval_minutes}\n"
        f"enabled: true\n"
        f"input_x: null\n"
        f"input_y: null\n"
        f"send_x: null\n"
        f"send_y: null\n"
    )


def _detect_display_name(device_id: str, group_id: str) -> tuple[str | None, list[str]]:
    """Deep link → dump UI → extract chat header title (best effort)."""

    trace: list[str] = []
    try:
        client = AdbClient(device_id=device_id)
    except Exception as exc:  # noqa: BLE001
        trace.append(f"adb_client_failed:{exc}")
        return None, trace

    if not check_current_app(client):
        try:
            open_line(client)
        except AdbError as exc:
            trace.append(f"open_line_failed:{exc}")
            return None, trace
        time.sleep(2)

    deep_link = f"line://ti/g2/{group_id}"
    try:
        client.shell("am", "start", "-a", "android.intent.action.VIEW", "-d", deep_link, check=False)
    except AdbError as exc:
        trace.append(f"deep_link_failed:{exc}")
        return None, trace
    trace.append("deep_link_dispatched")
    time.sleep(2.5)

    xml_path = customer_root("customer_a") / "data" / "raw_xml" / "navigate" / f"onboard-{group_id[:8]}.xml"
    xml_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        dumped = dump_ui_xml(client, xml_path)
        xml = dumped.read_text(encoding="utf-8")
    except (AdbError, RuntimeError, OSError) as exc:
        trace.append(f"dump_failed:{exc}")
        return None, trace

    # Heuristic: look for a node whose text looks like a chat title — typically
    # appears in the top header area, contains member count "(NN)" suffix or is
    # a short phrase. We pick the first non-noise text in the top quarter of
    # the screen (y < 350 in our 1080x2400 emulator).
    candidate = _pick_title_from_xml(xml)
    if candidate:
        trace.append(f"title_found:{candidate[:40]}")
        return candidate, trace
    trace.append("title_not_found")
    return None, trace


def _pick_title_from_xml(xml: str) -> str | None:
    bounds_re = re.compile(r'\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]')
    members_re = re.compile(r"^(?P<name>.+?)\s*\((\d{1,5})\)\s*$")
    title_candidates: list[tuple[int, str]] = []
    for m in re.finditer(r'<node[^>]*>', xml):
        s = m.group(0)
        text_match = re.search(r'\btext="([^"]*)"', s)
        if not text_match:
            continue
        text = text_match.group(1).strip()
        if not text or text in CHAT_HEADER_NOISE:
            continue
        bounds_match = re.search(r'bounds="([^"]+)"', s)
        if not bounds_match:
            continue
        bm = bounds_re.match(bounds_match.group(1))
        if not bm:
            continue
        x0, y0, x1, y1 = map(int, bm.groups())
        if y0 > 350:  # skip non-header area
            continue
        # Strongly prefer "Name(NN)" pattern; if found, use the bare name.
        members_match = members_re.match(text)
        if members_match:
            return members_match.group("name").strip()
        title_candidates.append((y0, text))
    if title_candidates:
        title_candidates.sort()
        return title_candidates[0][1]
    return None


def _default_voice_profile(display_name: str) -> str:
    return (
        f"# Voice profile — {display_name}\n\n"
        "**身份**：我是這個群裡的一個普通成員，不是小編、不是管理員、不是客服。\n"
        "我講話的方式跟群裡其他人差不多。\n\n"
        "## My nickname in this group\n\n"
        "- （請操作員填：你在這個群顯示的暱稱）\n\n"
        "## My personality (1-3 lines)\n\n"
        "- （請操作員寫：你想呈現的個性，例：「平常觀察居多，遇到有興趣的話題會冒個一句」）\n\n"
        "## Style anchors (the way I chat)\n\n"
        "- 像 chat 不像公告：1 句通常夠，常用「欸」「啊」「對啊」「哈」「噢」\n"
        "- 不講「大家」「歡迎」「請」這種廣播詞\n"
        "- emoji 配合群裡別人的用量，不刻意\n"
        "- 我會自然發語：「我也...」「欸這個...」「等等是不是...」\n"
        "- 不主導話題、不下結論、有疑問就直接問，沒就閉嘴\n\n"
        "## Samples (real lines I've said or would say)\n\n"
        "- （請操作員之後用 Lark 對 bot 說「幫我記下這個語氣 ...」累積真實樣本）\n\n"
        "## Off-limits（底線，不可破）\n\n"
        "- 不評論個人（外貌、身材、誰更紅、誰更可愛）\n"
        "- 不發政治立場、不戰宗教、不站隊\n"
        "- 涉醫療/投資/法律：不給結論，最多說「這個我也想知道」「再看看」\n"
        "- 不推銷、不替他人打廣告、不主動拉訂閱\n"
        "- 不用客服式語句（「您好」「為您」「希望這對您有幫助」）\n"
    )
