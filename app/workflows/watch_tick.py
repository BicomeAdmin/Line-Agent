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
from app.workflows.style_harvest import fingerprint_conversation


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

    # Activity-hour gate: outside operator's defined working window
    # (default 10:00-22:00 Asia/Taipei, env ACTIVITY_HOURS_START/END),
    # autonomous watchers stay silent — no navigate, no read, no codex
    # spawn. Operator-driven Lark commands (compose_and_send via codex
    # bridge) are NOT gated here; only watcher / patrol autonomy is.
    from app.core.risk_control import default_risk_control
    if not default_risk_control.is_activity_time():
        return {
            "acted": False,
            "reason": "outside_activity_hours",
            "activity_window": f"{default_risk_control.activity_start.strftime('%H:%M')}-{default_risk_control.activity_end.strftime('%H:%M')} Asia/Taipei",
        }

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

    # Dedup guard: if the previous auto_watch draft is still pending operator
    # review, don't compose another one — that just stacks near-duplicate
    # drafts in the inbox while the operator hasn't acted on the first.
    pending_id, _ = _find_recent_auto_watch_review(customer_id, community_id)
    if pending_id is not None:
        return {
            "acted": False,
            "reason": f"prior_auto_watch_pending:{pending_id}",
            "new_signature": new_sig,
        }

    # Compute current-conversation style fingerprint and inject into the
    # codex prompt — Skill B. Lets the auto-watch draft match the *current
    # vibe* (median length / emoji rate / particles) on top of the static
    # voice profile, so replies don't read as off-tone for what's happening
    # right now.
    style_hint = fingerprint_conversation(messages)

    # Spawn codex with the auto-watch prompt — let it decide compose vs skip.
    try:
        composed = _spawn_codex_for_watch(
            customer_id,
            community_id,
            community.display_name,
            style_hint=style_hint,
        )
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
    # Push notification to initiator's Lark, if known. Prefer an interactive
    # review card (so the operator can [通過/修改/忽略] from the notification
    # itself) when we can pinpoint the review the auto-watch just created.
    initiator_chat_id = watch.get("initiator_chat_id")
    if isinstance(initiator_chat_id, str) and initiator_chat_id and composed:
        review_id, draft_text = _find_recent_auto_watch_review(customer_id, community_id)
        try:
            client = LarkClient()
            if review_id and draft_text:
                from app.lark.cards import build_review_card
                from app.storage.config_loader import load_customer_config

                customer = load_customer_config(customer_id)
                card = build_review_card(
                    customer_name=customer.display_name,
                    community_name=community.display_name,
                    draft=draft_text,
                    job_id=review_id,
                    customer_id=customer_id,
                    community_id=community_id,
                    device_id=community.device_id,
                    reason="auto_watch",
                    confidence=None,
                    draft_title="🛎 自動追蹤 — 待審核",
                )
                client.send_card(initiator_chat_id, card, receive_id_type="chat_id")
            else:
                # Fallback to plain text when we can't link a specific review.
                client.send_message(
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


def _find_recent_auto_watch_review(customer_id: str, community_id: str) -> tuple[str | None, str | None]:
    """Find the most recent auto_watch-sourced review for this community
    that's still active (pending/edit_required/pending_reapproval). Used to
    pin the right review_id onto the notification card the daemon pushes.
    """

    from app.core.reviews import ACTIVE_REVIEW_STATUSES, review_store

    # Match auto_watch-sourced reviews precisely (reason="mcp_compose:auto_watch").
    # Legacy records before source-tagging stored bare "mcp_compose" — accept
    # those too so existing pending drafts still trigger the dedup guard.
    candidates = [
        r for r in review_store.list_all()
        if r.customer_id == customer_id
        and r.community_id == community_id
        and r.status in ACTIVE_REVIEW_STATUSES
        and (r.reason or "") in ("mcp_compose:auto_watch", "mcp_compose")
    ]
    if not candidates:
        return None, None
    candidates.sort(key=lambda r: r.created_at, reverse=True)
    latest = candidates[0]
    return latest.review_id, latest.draft_text


WATCH_PROMPT_TEMPLATE = (
    "你正在替使用者「自主追蹤」社群「{community_name}」({community_id})。\n"
    "**這是 autonomous 模式**：你自己決定要不要回、回誰、回什麼。HIL 鐵則仍守住——草稿會推到 Lark 卡片給操作員按 [通過/修改/忽略]，但選擇跟動筆是你的事。\n"
    "\n"
    "## 你的身份心智（每次都要有）\n"
    "你不是寫一句萬用稿丟進群裡的工具人。你是「最懂用戶營運、最懂人性的 AI 大腦」。每則草稿問自己三個問題：\n"
    "  1. **Create value 嗎**？（Paul VCPVC 第二個 V）這句回出去，能解決對方的具體問題 / 接住對方的情緒 / 給對方意外有用的資訊嗎？只是「不要讓社群安靜」就略過。\n"
    "  2. **深化關係嗎**？真實成員會回的話往往是「對啊我也」「欸這個我也想知道」這種**接續+共鳴**，不是「歡迎大家」「請各位」這種廣播。\n"
    "  3. **配比對嗎**？這個社群的營運途徑是 IP / 興趣話題 / 資訊為主？你的草稿要對齊這個比例（看 voice_profile）。\n"
    "\n"
    "**留量比流量重要**——寧可這輪略過，也不要為了「保持活躍」就丟一句生硬的小編話。一次傷信任，要十次互動才補得回來。\n"
    "{style_hint_block}"
    "## 必走流程（每步缺一不可）\n"
    "\n"
    "**Step 1 — 載入 persona（你是誰、你在這個群是誰）**\n"
    "→ 呼叫 get_persona_context({community_id})。把回傳的 `summary_zh` 那一行記在心裡，這是你接下來判斷「替誰講話」的根。\n"
    "\n"
    "**Step 2 — 自動選擇要回覆的對象**\n"
    "→ 呼叫 select_reply_target({community_id})。工具會自動：讀最近 20 則對話，幫每則訊息打分（@提及 / 未答問題 / 接續操作員的話 / 話題重疊度 / recency），回傳最佳對象+信心分數，**或 target=None 代表這輪沒人值得接**。\n"
    "→ **target=None → 立刻略過本輪**，回一句繁中說明工具給的 `skip_reason`，停下來不要繼續。不要硬找對象。\n"
    "→ target 有值 → 進 Step 3。\n"
    "\n"
    "**Step 3 — 載入目標的個人風格**\n"
    "→ 呼叫 get_member_fingerprint({community_id}, sender=<target.sender>)。\n"
    "→ 拿到 avg_length / median_length / emoji_rate / top_ending_particles / recent_lines。**你的草稿要鏡映這些數字**：\n"
    "    ‧ 對方平均 18 字，你的草稿就在 12-22 字之間，不要寫 50 字\n"
    "    ‧ 對方不用 emoji，你也別放\n"
    "    ‧ 對方愛用「啊/欸」結尾，你也順著用\n"
    "→ 如果 loaded=False（這個社群還沒算過 fingerprint）→ 用 get_voice_profile 的 Style anchors 當 fallback，並在 codex 回報時提一句「未來建議跑 refresh_member_fingerprints」。\n"
    "\n"
    "**Step 4 — Compose（且只有此時才能 compose）**\n"
    "→ 草稿必須能填這句：「我在回 <target.sender> 說的『<target.text>』，他平均 N 字、語氣偏 X，所以我寫『...』」。\n"
    "→ 寫完前再對照 persona 的 recent_self_posts 一次：使用者本人最近講過跟這個話題沾邊的東西嗎？沒有的話**寧可略過**，不要憑空生新立場。\n"
    "→ compose_and_send(community_id, text, source=\"auto_watch\")。系統會自動推卡片給操作員審核。\n"
    "\n"
    "## 回覆給操作員的格式（簡潔）\n"
    "1-2 句繁中總結，包含：\n"
    "  ‧ 你選了誰、為什麼（引用 select_reply_target 的 reasons 一條）\n"
    "  ‧ 草稿是什麼、模仿了對方什麼風格\n"
    "  例：「接 許芳旋（信心 4.5：unanswered_q + after_operator_speech）。對方平均 19 字、句尾常用「啊」，所以擬「對啊我也覺得這樣好」（11 字）。卡片已送審。」\n"
    "略過時：「本輪略過：<skip_reason>」。\n"
    "\n"
    "## 鐵則（任何時候都要遵守）\n"
    "- 不要連發（如果你最近一則訊息就還沒人回，就別再補一句）。\n"
    "- 不要主動拉新話題（這條留給下一個 Phase）。\n"
    "- 不要回應自己——使用者剛講完一句，不要立刻再 compose 一句承接自己。\n"
    "- 任何 Off-limits 內容一律不寫。\n"
    "\n"
    "回覆格式：1-2 句中文總結（compose 了哪句並說明對應到誰的訊息 / 為什麼略過）。\n"
)


def _format_style_hint_block(style_hint: dict[str, object] | None) -> str:
    if not style_hint or style_hint.get("median_length") is None:
        return ""
    summary = style_hint.get("summary_zh") or ""
    if not summary:
        return ""
    return (
        f"本群現在的講話氛圍（從最近 {style_hint.get('sample_count')} 則統計）：{summary}\n"
        "→ 你的草稿風格、字數、語助詞要貼近這個氛圍，不要寫得比群裡正式或冗長。\n\n"
    )


def _spawn_codex_for_watch(
    customer_id: str,
    community_id: str,
    community_name: str,
    *,
    style_hint: dict[str, object] | None = None,
) -> str:
    """Spawn a one-shot codex turn with the auto-watch instructions.

    Output goes to a temp file via --output-last-message; we return that.
    """

    prompt = WATCH_PROMPT_TEMPLATE.format(
        community_name=community_name,
        community_id=community_id,
        style_hint_block=_format_style_hint_block(style_hint),
    )
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
