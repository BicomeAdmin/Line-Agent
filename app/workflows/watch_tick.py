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
    "你正在替使用者「自動追蹤」社群「{community_name}」({community_id})。\n"
    "{style_hint_block}"
    "## Persona 載入（必走第一步）\n"
    "→ 呼叫 get_persona_context({community_id}) 拿到 (帳號 × 社群 × voice profile × 近期使用者送過的句子)。\n"
    "→ 用裡面的 `summary_zh`、`nickname`、`recent_self_posts` 當動筆基礎。\n"
    "→ 沒呼叫不准 compose——這是要避免「替使用者編一個他從沒講過的立場」的硬性閘。\n"
    "\n"
    "## 第一步：判斷對話脈絡（這步沒做完不准 compose）\n"
    "1. 先呼叫 read_recent_chat({community_id}, limit=20) 取得最新對話。\n"
    "2. 從訊息往下逐則檢視：\n"
    "   a. **使用者上一則訊息是哪一句？** （往前找 send_attempt 紀錄或 voice_profile 裡能對得上的句子）\n"
    "   b. **使用者那句之後，誰講了什麼？** 把「使用者那句之後的所有訊息」當作要判斷的對話。\n"
    "   c. **那段對話裡，有沒有人在跟使用者講話？** 判斷標準：\n"
    "      ✓ 有人 @ 使用者、引用使用者那句、或明顯針對使用者那句延伸（例「對啊我也」「你說的這個...」）\n"
    "      ✓ 使用者上次有問問題且還沒人答，現在有人答了\n"
    "      ✗ 別人在彼此互聊（A 跟 B 對話、跟使用者無關）\n"
    "      ✗ 話題已經自然過了（中間隔了好幾則新話題）\n"
    "      ✗ 沒人回應使用者，群裡冷掉了\n"
    "      ✗ **使用者自己上一則就是答覆語（例「對啊」「我也覺得」），現在沒人接 → 話題結束了**\n"
    "   d. **草稿能不能接續使用者自己已有的發言？** —— 真實成員的回覆會跟自己之前講過的話一致：\n"
    "      ✓ 草稿是延續使用者上一則或前幾則的話題、立場、語氣\n"
    "      ✗ 草稿是憑空生出一個新立場 / 新觀點 / 新話題，跟使用者最近講的東西沒連結\n"
    "      → 後者一律退回略過。「替使用者編一句他從沒講過的立場」就是不真實。\n"
    "\n"
    "## 第二步：決定 compose 還是略過\n"
    "**只有 c 步驟得到 ✓ 的情況才能 compose**。任何 ✗ 的情況一律略過。\n"
    "略過時回一行繁中說明「本輪略過：<具體原因，例如『話題已被 B 接住』『使用者上一句沒人回，再補會像自言自語』>」。\n"
    "\n"
    "## 第三步：如果決定 compose\n"
    "- 在 compose 之前先 get_voice_profile，拿到風格錨點。\n"
    "- 草稿必須**明確指向你看到的那則訊息**——你在回應的是誰、那個人講了什麼。\n"
    "  寫之前先在心裡填：「我在回 X 說的『...』，因為他說的這句確實在針對我」。\n"
    "  填不出來就退回略過。\n"
    "- 風格：成員身份，短句、口語、不客套、不廣播。\n"
    "- **字數要對齊上面的『本群現在的講話氛圍』**——中位字數附近 ±30%。\n"
    "- 呼叫 compose_and_send 時**必須帶 `source=\"auto_watch\"`**。\n"
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
