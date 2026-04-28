"""Watcher Phase 2 — daemon-side per-watch tick.

For each active watch:
  1. analyze_chat (which navigates + reads + classifies).
  2. Compare current message signature with last_seen — bail if unchanged.
  3. If new content AND past cooldown → spawn a Codex turn with a focused
     "auto-watch" prompt. Codex decides whether to compose; if yes it calls
     the existing compose_and_send MCP tool, which lands a review_card.
  4. Optionally push a notification card to the watch's initiator_chat_id
     so the operator sees "new draft pending" in their Lark.

The Codex spawn here is identical to the Lark bridge's spawn — same MCP,
same compliance framing, same voice profile rules — so member-style drafts
are produced consistently regardless of trigger (Lark message vs auto-watch).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

from app.adb.client import AdbClient
from app.core.audit import append_audit_event
from app.lark.client import LarkClient, LarkClientError
from app.storage.paths import default_raw_xml_path
from app.storage.config_loader import load_community_config
from app.storage.watches import (
    list_active_watches_all_customers,
    messages_signature,
    update_watch_state,
)
from app.workflows.openchat_navigate import navigate_to_openchat
from app.workflows.read_chat import read_recent_chat


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def tick_all_watches() -> dict[str, object]:
    """Called once per scheduler cycle by scheduler_daemon. Cheap when there
    are no active watches; expensive (Codex spawn) only when we detect new
    replies past cooldown.
    """

    fired: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    now = time.time()
    for watch in list_active_watches_all_customers():
        watch_id = str(watch.get("watch_id") or "")
        last_check = float(watch.get("last_check_epoch") or 0)
        if last_check and (now - last_check) < int(watch.get("poll_interval_seconds") or 60):
            skipped.append({"watch_id": watch_id, "reason": "poll_interval"})
            continue
        outcome = _tick_one(watch)
        update_watch_state(
            str(watch.get("customer_id")),
            watch_id,
            last_check_epoch=now,
            last_seen_signature=outcome.get("new_signature"),
            last_draft_epoch=outcome.get("draft_epoch"),
        )
        (fired if outcome.get("acted") else skipped).append({"watch_id": watch_id, **outcome})
    return {"fired": fired, "skipped": skipped}


def _tick_one(watch: dict[str, object]) -> dict[str, object]:
    customer_id = str(watch.get("customer_id"))
    community_id = str(watch.get("community_id"))
    watch_id = str(watch.get("watch_id"))
    cooldown = int(watch.get("cooldown_seconds") or 300)
    last_draft = float(watch.get("last_draft_epoch") or 0)
    last_signature = watch.get("last_seen_signature") or ""

    # Navigate + read.
    nav = navigate_to_openchat(customer_id, community_id, overall_timeout_seconds=20.0)
    if nav.get("status") != "ok":
        return {"acted": False, "reason": f"navigate_failed:{nav.get('reason')}"}

    try:
        community = load_community_config(customer_id, community_id)
    except Exception as exc:  # noqa: BLE001
        return {"acted": False, "reason": f"community_lookup_failed:{exc}"}

    try:
        messages = read_recent_chat(
            AdbClient(device_id=community.device_id),
            default_raw_xml_path(customer_id),
            limit=20,
        )
    except RuntimeError as exc:
        return {"acted": False, "reason": f"read_failed:{exc}"}

    new_sig = messages_signature(messages)
    if new_sig == last_signature:
        return {"acted": False, "reason": "no_new_content", "new_signature": new_sig}

    if last_draft and (time.time() - last_draft) < cooldown:
        # New content but still on cooldown — record signature, no draft.
        return {"acted": False, "reason": "cooldown", "new_signature": new_sig}

    # Spawn codex with the auto-watch prompt — let it decide compose vs skip.
    try:
        composed = _spawn_codex_for_watch(customer_id, community_id, community.display_name)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
        append_audit_event(
            customer_id,
            "watch_tick_error",
            {"watch_id": watch_id, "community_id": community_id, "error": str(exc)},
        )
        return {"acted": False, "reason": f"codex_error:{exc!r}", "new_signature": new_sig}

    append_audit_event(
        customer_id,
        "watch_tick_fired",
        {
            "watch_id": watch_id,
            "community_id": community_id,
            "codex_summary": (composed or "")[:140],
        },
    )
    # Push notification to initiator's Lark, if known.
    initiator_chat_id = watch.get("initiator_chat_id")
    if isinstance(initiator_chat_id, str) and initiator_chat_id and composed:
        try:
            LarkClient().send_message(
                initiator_chat_id,
                "text",
                {"text": f"🛎 watch tick（{community.display_name}）：{composed[:300]}"},
                receive_id_type="chat_id",
            )
        except LarkClientError as exc:
            append_audit_event(customer_id, "watch_lark_notify_failed", {"watch_id": watch_id, "error": str(exc)})

    return {
        "acted": True,
        "new_signature": new_sig,
        "draft_epoch": time.time(),
        "codex_summary": (composed or "")[:140],
    }


WATCH_PROMPT_TEMPLATE = (
    "你正在替使用者「自動追蹤」社群「{community_name}」({community_id})。\n"
    "規則：\n"
    "1. 先呼叫 read_recent_chat({community_id}, limit=20) 取得最新對話。\n"
    "2. 對照使用者最近送出的內容（你會在訊息看到 send_attempt 紀錄）：\n"
    "   - 如果有新成員針對使用者的話**直接接話**（@提及、引用、或內容明顯相關），**就 compose_and_send 一句自然回覆**。\n"
    "   - 風格：成員身份，遵守 voice_profile，短句、口語、不客套、不廣播。compose 前必先 get_voice_profile。\n"
    "3. 若**沒有人針對使用者回覆**，或話題不需要回（例如別人在自己對話），**這一輪就不要 compose**——\n"
    "   只回一行繁中說明「本輪沒值得補的回覆，本輪略過」。\n"
    "4. 不要連發、不要主動拉新話題（這條留給下一個 Phase）。\n"
    "5. 任何 Off-limits 內容一律不寫。\n"
    "回覆格式：1-2 句中文總結你做了什麼（compose 了哪句 / 為什麼略過）。\n"
)


def _spawn_codex_for_watch(customer_id: str, community_id: str, community_name: str) -> str:
    """Spawn a one-shot codex turn with the auto-watch instructions.

    Output goes to a temp file via --output-last-message; we return that.
    """

    prompt = WATCH_PROMPT_TEMPLATE.format(community_name=community_name, community_id=community_id)
    with tempfile.NamedTemporaryFile(mode="w+", suffix=".txt", delete=False, encoding="utf-8") as fh:
        last_msg_path = fh.name
    cmd = [
        "codex", "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "--cd", str(PROJECT_ROOT),
        "--output-last-message", last_msg_path,
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=180,
            env={**os.environ},
        )
        if proc.returncode != 0:
            raise RuntimeError(f"codex exited {proc.returncode}: {(proc.stderr or '')[:300]}")
        try:
            text = Path(last_msg_path).read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
        return text or "（codex 沒輸出最後訊息）"
    finally:
        try:
            os.unlink(last_msg_path)
        except OSError:
            pass
