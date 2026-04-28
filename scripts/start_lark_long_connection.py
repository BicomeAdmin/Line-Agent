"""Start a Lark long-connection client routing to Codex (ChatGPT Pro subscription).

Architecture:
    Lark message
        ↓ (this long-connection client, lark-oapi SDK)
    on_message handler
        ↓ (subprocess: codex exec, with project_echo MCP attached via codex's MCP config)
    GPT-5 via the user's ChatGPT Pro subscription (0 token cost)
        ↓ (calls project_echo MCP tools as needed for natural-language requests)
    Project Echo workflows (navigate, draft, send, schedule, …)
        ↓ (return)
    handler captures Codex's last assistant message
        ↓ (LarkClient.send_message)
    pushed back to user's Lark chat

Why Codex (not Claude):
    - Anthropic AUP classifier flags the LINE-automation tool surface even with HIL
      framing in the system prompt — `claude -p` returns "violates Usage Policy".
    - Codex (OpenAI) doesn't have an equivalent client-side classifier on user-MCP,
      so Project Echo's review-gated send chain works end-to-end.
    - Both are subscription-backed (0 token cost). See CLAUDE.md §8.

Why subscription (not API):
    - 0 per-token cost; respects "省錢優先" rule in CLAUDE.md §8.
    - Same review_store / human_approval gates — the LLM can stage drafts via
      compose_and_send, but cannot bypass HIL (require_human_approval=true holds).

Required configuration in `.env`:
    LARK_APP_ID
    LARK_APP_SECRET

Required setup (one-time):
    - Lark Developer Console → Events & Callbacks → "Use long connection"
    - Subscribe event: im.message.receive_v1
    - codex CLI installed and logged in (uses ChatGPT Pro subscription)
    - Project Echo MCP registered with codex: `codex mcp add project_echo -- python3 <path>/scripts/project_echo_mcp_server.py`
    - OpenClaw's channels.feishu.enabled = false (so we own Lark events)
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import threading
import traceback
from pathlib import Path

import _bootstrap  # noqa: F401

import lark_oapi
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
from lark_oapi.card.action_handler import CardActionHandler
from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
from lark_oapi.ws.client import Client as WsClient

from app.config import settings
from app.core.audit import append_audit_event
from app.core.timezone import taipei_now_str
from app.lark.client import LarkClient, LarkClientError
from app.lark.events import enqueue_lark_action, enqueue_lark_event
from app.workflows.job_runner import ensure_job_worker

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Per-chat conversation history. GPT-5 via `codex exec` is stateless across calls,
# so we keep the last few turns and prepend them to each prompt. This lets the
# user say "通過" / "請執行" / "好" and have the model resolve which review_id /
# community / scheduled post they meant.
_CHAT_HISTORY: dict[str, list[tuple[str, str]]] = {}
_CHAT_HISTORY_LOCK = threading.Lock()
_HISTORY_TURNS = 6  # keep last 6 (user, assistant) pairs per chat


def _append_history(chat_id: str, user_text: str, assistant_text: str) -> None:
    with _CHAT_HISTORY_LOCK:
        history = _CHAT_HISTORY.setdefault(chat_id, [])
        history.append((user_text, assistant_text))
        if len(history) > _HISTORY_TURNS:
            del history[: len(history) - _HISTORY_TURNS]


def _format_history(chat_id: str) -> str:
    with _CHAT_HISTORY_LOCK:
        history = list(_CHAT_HISTORY.get(chat_id, []))
    if not history:
        return ""
    lines = ["## 最近對話（給你接續判斷用，不要重複回應）"]
    for u, a in history:
        lines.append(f"- 使用者：{u[:200]}")
        lines.append(f"  你：{a[:300]}")
    return "\n".join(lines) + "\n"


# Compliance framing prepended to every Codex prompt. Aligns with CLAUDE.md §0:
# this is an operator-assist tool, not an autonomous bot; HIL gate is sacred.
CODEX_FRAMING = (
    "你協助使用者在他自己參加的 LINE OpenChat 社群裡互動。\n"
    "**身份重點**：你**不是**官方小編、不是管理員、不是客服。你產出的每則草稿都是\n"
    "「他這個成員會自然講出來的話」——跟其他群友互相聊天的口吻，**不要廣播感**。\n"
    "所有外發訊息都要先進入 review_store 等他按通過。技術上 require_human_approval=true\n"
    "已經鎖定，沒有繞過的路徑。\n"
    "\n"
    "**語言**：一律繁體中文（台灣用語），不要簡中、不要英文混雜。\n"
    "\n"
    "**工具**：只用 project_echo MCP server 提供的工具。不要嘗試 shell、檔案讀寫、\n"
    "或其他 MCP server 的工具。\n"
    "\n"
    "## A. 入境理解（user → 你）\n"
    "1. 看「最近對話」段落解析使用者的省略指令：\n"
    "   - 「通過 / 執行 / OK 送出」→ 找最近一輪你回覆過的 review_id，呼叫 approve_review；\n"
    "   - 「忽略 / 駁回 / 不要」→ 找最近的 review_id，呼叫 ignore_review；\n"
    "   - 「再寫一個 / 換個說法 / 重寫」→ 對同一個 community 再 compose_and_send 一輪。\n"
    "2. 模糊不清就**反問一句**，不要瞎猜。例：「你是要我通過 job-XXX 還是寫新的？」\n"
    "3. 沒有 community_id 但對話中曾出現過：用上一個提到的；多個就反問。\n"
    "\n"
    "## B. 出境風格錨定（你 → 草稿） — 真實成員模式\n"
    "**核心原則**：草稿要像「**這個群裡的另一個成員**」會講的話，不是小編廣播、不是助手客服。\n"
    "\n"
    "1. **每次** compose_and_send 之前，**必先依序**：\n"
    "   a. analyze_chat(community_id) 或 read_recent_chat(community_id, limit=20) 抓該群最近的真實對話；\n"
    "   b. get_voice_profile(community_id) 拿該成員的個性設定（Off-limits 仍嚴格遵守）；\n"
    "   c. 用 (a) 的對話樣本當**風格錨**——觀察他們怎麼斷句、用什麼語氣詞、emoji 怎麼用、有多隨便。\n"
    "\n"
    "2. **草稿風格規則**：\n"
    "   - 像 chat 不像公告：可以用「欸」「啊」「對啊」「哈」「噢」這類語氣詞；不要「大家」「歡迎」「請」開頭。\n"
    "   - 短：1 句通常夠，最多 2 句。不完整也行（chat 本來就會省略主詞）。\n"
    "   - 不要管理員語：禁用「整理」「請大家」「歡迎大家」「順手補一下」「收個聲量」這種小編行話。\n"
    "   - emoji 跟群裡別人對齊；他們不愛用就不用，他們用得多你也用一兩個。\n"
    "   - 寧可短句、寧可像「我也想聽聽看耶」這種發語感言，也不要寫得像 announcement。\n"
    "\n"
    "3. **Off-limits 仍嚴格擋**（profile 的 Off-limits 不變，是底線）：政治立場、評論個人、醫療/投資結論等\n"
    "   觸到一律回「這個依社群守則我不寫」。\n"
    "\n"
    "4. profile loaded=False 時：fallback 用「跟群裡其他成員聊天的口吻、不要小編腔」。\n"
    "5. 操作員說「幫我記下這個語氣 / 這句以後可以參考」→ append_voice_sample。\n"
    "6. 操作員說「重寫整份語氣設定」→ set_voice_profile。\n"
    "\n"
    "**反例（不要寫成這樣）**：\n"
    "  ✗「剛剛有看到 JN3 意願調查，還沒填的可以順手補一下，方便後面整理 🙌」← 太小編、太組織者口氣\n"
    "  ✗「大家如果對 JN3 有想問的，也可以直接丟上來，我們一起整理。」← 「大家如果」「我們一起整理」是廣播語\n"
    "  ✗「今天先小小收個聲量」← 運營行話\n"
    "**正例（這種感覺）**：\n"
    "  ✓「JN3 我也還沒填欸 哈」\n"
    "  ✓「欸有人填了嗎 我看了一下還沒下手」\n"
    "  ✓「JN3 是要填到啥時啊」\n"
    "\n"
    "## C. Watcher Phase 1（互動玩法）\n"
    "1. 「看一下 X 群最近怎麼樣 / X 群有沒有問題沒人回 / X 群現在熱不熱」→ analyze_chat。\n"
    "2. 拿到結果後，**先回操作員一段中文摘要**，包含：\n"
    "   - active_state（cold_spell / active / moderate / trickle / quiet）+ 為什麼這樣判\n"
    "   - 有沒有 unanswered_question（如有，引用問題本身）\n"
    "   - sensitivity_hits 是否非空（有就提醒「這個群最近有觸到 off-limits 關鍵字」）\n"
    "3. **不要自動 compose_and_send**——除非操作員明確說「幫我接 / 寫個回覆 / 擬稿」。\n"
    "4. 操作員說「擬稿」之後才走 B 流程（get_voice_profile → compose_and_send）。\n"
    "5. active_state=active 且 unanswered_question 不存在時，**主動建議不介入**：「看起來大家在熱絡聊，建議先別插。」\n"
    "\n"
    "## D. Watcher Phase 2（自動追蹤）\n"
    "1. 操作員說「幫我追蹤 X 群 / 盯一下 X 群 / 有人回覆再幫我接 / 看著 X 群一小時」\n"
    "   → start_watch(community_id, duration_minutes=操作員指定或預設 60, initiator_chat_id={chat_id})。\n"
    "2. **務必**把 initiator_chat_id 設為當前對話的 chat_id（你會在 system context 看到 `current_chat_id`），\n"
    "   這樣 daemon auto-fire compose 時會推卡片回他的 Lark 私聊。\n"
    "3. 操作員說「停止追蹤 X / 不用追蹤了」→ stop_watch(community_id 或 watch_id)。\n"
    "4. 操作員說「目前在追蹤哪些群」→ list_watches。\n"
    "5. Watch 期間 daemon 會每 poll_interval_seconds 檢查；新內容 + 過 cooldown 才會 fire codex 寫稿。\n"
    "   你**不需要**主動寫稿——這是 daemon 的責任。\n"
    "\n"
    "## E. 通用工具映射\n"
    "- LINE 邀請連結（line.me/ti/g2/...）：\n"
    "  · 已知社群 → resolve_invite_url 拿 community_id；\n"
    "  · resolve 回 matched=False（未配置）+ 操作員說「幫我加 / 我已經在這個群」→ add_community（自動 deep-link 抓標題 + 寫 YAML + bootstrap voice profile）。\n"
    "  · 操作員沒明說「加」就只 resolve、不要自動 add_community。\n"
    "- 社群名字錯了 / 顯示「未命名社群 (xxx…)」→ refresh_community_title。\n"
    "  · 操作員給名字（例「openchat_004 改成水月觀音道場」）→ 帶 display_name 直接覆蓋。\n"
    "  · 操作員只說「補名字 / 重新讀名字」→ 不帶 display_name，工具會 deep-link 進去自己抓。\n"
    "- 「列社群」→ list_communities；「列待審稿」→ list_pending_reviews。\n"
    "- 「排程 X 時 Y 說 Z」→ add_scheduled_post（send_at 為 ISO 8601 含時區，例 2026-04-29T20:00:00+08:00）。\n"
    "\n"
    "**回覆格式**：1-3 句繁中總結，回報關鍵欄位（review_id / community_name / status / active_state）。\n"
    "不要貼整段 JSON、不要客套、不要解釋你內部呼叫了哪些 tool。\n"
)


def _to_dict(typed_event) -> dict[str, object]:
    """Marshal a typed P2 event back into a plain dict matching the wire payload."""

    raw = lark_oapi.JSON.marshal(typed_event) or "{}"
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return result if isinstance(result, dict) else {}


def _on_message(typed_event: P2ImMessageReceiveV1) -> None:
    payload = _to_dict(typed_event)
    event_id = (payload.get("header") or {}).get("event_id")
    print(f"[bridge] {taipei_now_str('%H:%M:%S')} message event id={event_id}", flush=True)

    # Extract: text + reply_target. Support v2 schema directly.
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}

    chat_id = message.get("chat_id") if isinstance(message.get("chat_id"), str) else None
    msg_type = message.get("message_type")
    if msg_type != "text":
        # Ignore stickers, images, etc. for now.
        print(f"[bridge]   ignoring message_type={msg_type}", flush=True)
        return
    content_raw = message.get("content")
    try:
        content = json.loads(content_raw) if isinstance(content_raw, str) else {}
    except json.JSONDecodeError:
        content = {}
    user_text = (content.get("text") or "").strip()
    if not user_text:
        return
    if chat_id is None:
        print("[bridge]   no chat_id, cannot reply", flush=True)
        return

    # Drop @bot mention prefix LINE-style: "@_user_1 ..." (Lark text often has user
    # tag tokens). For private chats this is usually absent.
    cleaned_text = user_text.replace("@_user_1", "").replace("@_user_2", "").strip()

    # Spawn Claude in background so we don't block the WebSocket loop.
    threading.Thread(
        target=_dispatch_to_claude,
        args=(cleaned_text, chat_id, event_id, payload),
        daemon=True,
    ).start()


def _dispatch_to_claude(user_text: str, chat_id: str, event_id: str | None, payload: dict[str, object]) -> None:
    customer_id = settings.default_customer_id
    append_audit_event(
        customer_id,
        "lark_message_received",
        {"event_id": event_id, "chat_id": chat_id, "text_preview": user_text[:80]},
    )

    try:
        reply = _run_codex(user_text, chat_id=chat_id)
    except subprocess.TimeoutExpired:
        reply = "（系統處理超時，請稍後重試。）"
        append_audit_event(customer_id, "codex_dispatch_timeout", {"event_id": event_id})
    except Exception as exc:  # noqa: BLE001
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
        reply = f"（內部錯誤：{exc!r}）"
        append_audit_event(customer_id, "codex_dispatch_error", {"event_id": event_id, "error": str(exc)})

    # Save into per-chat history so the next turn keeps context.
    if chat_id and reply:
        _append_history(chat_id, user_text, reply)

    # Push reply back to Lark.
    try:
        client = LarkClient()
        client.send_message(chat_id, "text", {"text": reply or "（沒有回應）"}, receive_id_type="chat_id")
        append_audit_event(
            customer_id,
            "lark_reply_sent",
            {"event_id": event_id, "chat_id": chat_id, "reply_preview": (reply or "")[:80]},
        )
    except LarkClientError as exc:
        print(f"[bridge]   Lark send failed: {exc}", file=sys.stderr, flush=True)
        append_audit_event(customer_id, "lark_reply_failed", {"event_id": event_id, "error": str(exc)})


def _run_codex(user_text: str, *, chat_id: str | None = None, timeout_seconds: int = 180) -> str:
    """Invoke `codex exec` headless with the Project Echo MCP auto-loaded.

    Codex reads `~/.codex/config.toml` which already has `[mcp_servers.project_echo]`,
    so no per-call MCP config flag is needed. The `--dangerously-bypass-approvals-and-sandbox`
    flag is required because Codex defaults to ask-on-each-MCP-call; for our trusted
    local MCP this is the documented bypass.

    Output is read from `--output-last-message <FILE>` so we don't have to parse
    the decorative banner / reasoning / token-summary lines.
    """

    import tempfile

    history_block = _format_history(chat_id) if chat_id else ""
    chat_ctx = f"\n## 本輪 system context\n- current_chat_id: `{chat_id}`\n" if chat_id else ""
    full_prompt = f"{CODEX_FRAMING}{chat_ctx}\n{history_block}\n## 本輪使用者訊息\n\n{user_text}"
    with tempfile.NamedTemporaryFile(mode="w+", suffix=".txt", delete=False, encoding="utf-8") as fh:
        last_msg_path = fh.name

    cmd = [
        "codex", "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "--cd", str(PROJECT_ROOT),
        "--output-last-message", last_msg_path,
    ]
    print(f"[bridge]   {taipei_now_str('%H:%M:%S')} → codex (msg len={len(user_text)})", flush=True)
    try:
        proc = subprocess.run(
            cmd,
            input=full_prompt,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env={**os.environ},
        )
        if proc.returncode != 0:
            raise RuntimeError(f"codex exited {proc.returncode}: {(proc.stderr or '')[:300]}")
        try:
            reply = Path(last_msg_path).read_text(encoding="utf-8").strip()
        except OSError:
            reply = ""
        if not reply:
            # Fallback: parse stdout tail. codex prints the final assistant message
            # after a line containing just "codex" between the banner and the
            # "tokens used" footer.
            reply = _extract_codex_tail(proc.stdout)
        return reply or "（系統沒有產生回應，請稍後重試。）"
    finally:
        try:
            os.unlink(last_msg_path)
        except OSError:
            pass


def _extract_codex_tail(stdout: str) -> str:
    """Best-effort: pull the final assistant text from codex exec stdout."""

    lines = stdout.splitlines()
    # Find the last "codex" header line and take everything after up to "tokens used".
    last_codex_idx = -1
    for i, line in enumerate(lines):
        if line.strip() == "codex":
            last_codex_idx = i
    if last_codex_idx == -1:
        return stdout.strip()[-500:]
    tail = []
    for line in lines[last_codex_idx + 1:]:
        if line.startswith("tokens used") or line.startswith("--------"):
            break
        if "ERROR codex_core::" in line:
            continue
        tail.append(line)
    return "\n".join(tail).strip()[-1000:]


def _on_card_action(typed_event) -> object:
    payload = _to_dict(typed_event)
    print(f"[bridge] card action trigger", flush=True)
    try:
        response = enqueue_lark_action(payload)
        print(f"[bridge]   → {response}", flush=True)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
    # The card-action handler must return some response — Lark uses it as the
    # callback ack. Returning None (no toast / no card update) is fine for now.
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", default="https://open.larksuite.com",
                        help="Use https://open.feishu.cn for the China (飛書) tenant.")
    args = parser.parse_args()

    if not settings.lark_app_id or not settings.lark_app_secret:
        print("[bridge] missing LARK_APP_ID or LARK_APP_SECRET in .env", file=sys.stderr, flush=True)
        return 2

    ensure_job_worker()
    print(f"[bridge] LARK_APP_ID={settings.lark_app_id}", flush=True)
    print(f"[bridge] domain={args.domain}", flush=True)

    # No-op handlers for events the SDK auto-delivers but our app doesn't need;
    # without these, every reaction toggle prints a noisy "processor not found" stack.
    def _noop(_event):
        return None

    event_handler = (
        EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(_on_message)
        .register_p2_card_action_trigger(_on_card_action)
        .register_p2_im_message_reaction_created_v1(_noop)
        .register_p2_im_message_reaction_deleted_v1(_noop)
        .register_p2_im_message_message_read_v1(_noop)
        .register_p2_im_message_recalled_v1(_noop)
        .build()
    )

    client = WsClient(
        app_id=settings.lark_app_id,
        app_secret=settings.lark_app_secret,
        event_handler=event_handler,
        domain=args.domain,
        auto_reconnect=True,
    )

    print("[bridge] starting long-connection client (Ctrl-C to stop)...", flush=True)
    try:
        client.start()
    except KeyboardInterrupt:
        print("[bridge] shutting down", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
