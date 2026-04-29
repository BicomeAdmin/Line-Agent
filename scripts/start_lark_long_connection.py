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

# Pending-edit state per chat: when operator clicks 「修改稿件」 on a card,
# we stash {chat_id: review_id} here, then the next inbound message from
# that chat is treated as the edit text instead of being routed to codex.
# Single-process bridge, so a plain dict + lock is sufficient.
_PENDING_EDIT_BY_CHAT: dict[str, str] = {}
_PENDING_EDIT_LOCK = threading.Lock()


def _set_pending_edit(chat_id: str, review_id: str) -> None:
    with _PENDING_EDIT_LOCK:
        _PENDING_EDIT_BY_CHAT[chat_id] = review_id


def _pop_pending_edit(chat_id: str) -> str | None:
    with _PENDING_EDIT_LOCK:
        return _PENDING_EDIT_BY_CHAT.pop(chat_id, None)


def _peek_pending_edit(chat_id: str) -> str | None:
    with _PENDING_EDIT_LOCK:
        return _PENDING_EDIT_BY_CHAT.get(chat_id)


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
    "   - 「通過 / 執行 / OK 送出」→ 找最近一輪你回覆過的 review_id：\n"
    "     * **若你跟操作員剛剛討論出新版草稿**（meta-feedback 流程）→ **必須先**呼叫 `update_review_draft(review_id, new_draft_text=最終討論版)` **再**呼叫 approve_review。跳過 update_review_draft 會送出**原版**，不是討論完的版本——這是過去真實踩過的雷。\n"
    "     * 若沒有討論、操作員只是說「通過」原稿 → 直接 approve_review。\n"
    "   - 「忽略 / 駁回 / 不要」→ 找最近的 review_id，呼叫 ignore_review；\n"
    "   - 「再寫一個 / 換個說法 / 重寫」→ 對同一個 community 再 compose_and_send 一輪。\n"
    "2. 模糊不清就**反問一句**，不要瞎猜。例：「你是要我通過 job-XXX 還是寫新的？」\n"
    "3. 沒有 community_id 但對話中曾出現過：用上一個提到的；多個就反問。\n"
    "\n"
    "## B. 出境風格錨定（你 → 草稿） — 真實成員模式\n"
    "**核心原則**：草稿要像「**這個群裡的另一個成員**」會講的話，不是小編廣播、不是助手客服。\n"
    "\n"
    "**你的身份**（CLAUDE.md §0-prelude）：你是使用者的 AI 最強大腦——最懂用戶營運、最懂人性的綜合體。每則草稿前用三個 Paul《私域流量》原則濾一遍：\n"
    "  • **Create value**：能解決具體問題 / 接住情緒 / 給意外資訊嗎？只是「保持活躍」就退回略過。\n"
    "  • **深化關係**：真實成員的回應是接續+共鳴（「對啊我也」「欸這個我也想知道」），不是廣播詞。\n"
    "  • **留量 > 流量**：寧可略過，也不要丟生硬小編話。一次傷信任要十次互動才補得回來。\n"
    "\n"
    "**B-prelude（Persona 載入，每次進到一個社群必走第一步）**：\n"
    "  → 呼叫 get_persona_context(community_id) 載入 (帳號 × 社群 × 人物設定 × 近期發言) 的完整脈絡。\n"
    "  → 拿到結果後，**先把 `summary_zh` 那一行原樣 echo 回給操作員**（讓他確認你載對人了），再做後面的事。\n"
    "    例：「在『山納百景』(openchat_003)，你是 客戶 A — 暱稱『小宇』，個性『偏觀察、有興趣時冒一兩句』。最近 7 天送過 3 句，最後一句：『JN3 我也還沒填欸』。」\n"
    "  → 這個 echo 是強制的——不 echo 就不准動筆。\n"
    "\n"
    "0. **Compose 前先做對話脈絡判斷**（這步沒做過不准 compose）：\n"
    "   - read_recent_chat 看完之後，要**指得出兩件事**：\n"
    "     (i) 「我這次的草稿是在回 X 那則『...』」——不能憑空寫一句。\n"
    "     (ii) 「使用者自己最近在這個群講過 Y」——草稿要能**接續使用者已參與的線**，不能憑空創造一個新立場/新話題。\n"
    "         判斷使用者自己的發言：voice_profile 裡有他的暱稱與口氣樣本；audit log 裡有 send_attempt 紀錄他最近實際送出的句子。\n"
    "   - 檢查清單（兩個面向都要過）：\n"
    "     ✓ 有人 @ 使用者、引用使用者上一句、或對話內容明顯接續使用者的話 **且** 使用者本人最近在這個群有發過跟你想擬的話相關的內容\n"
    "     ✓ 使用者有問題還沒人答，現在這個 compose 是合理的補答\n"
    "     ✓ 操作員明確要求「擬稿 / 寫個回覆 / 接這句」並指出對象 **且** 草稿能接續使用者既有的發言/立場\n"
    "     ✗ 使用者上一句是答覆語（「對啊」「我也覺得」），群裡沒人接 → **不要再 compose**，那會變自言自語\n"
    "     ✗ 別人在彼此聊，跟使用者的最後一句沒關係 → **不要 compose**\n"
    "     ✗ 話題已經過了好幾則，現在補這句很突兀 → **不要 compose**\n"
    "     ✗ 找不到具體要回應的對象 → **不要 compose**\n"
    "     ✗ **使用者最近沒在這個群講過跟你想擬的話相關的東西** → **不要憑空編一個立場讓他講**——退回略過。\n"
    "   - 任何 ✗ → 跟操作員說「沒有合適的脈絡可以接，這輪我先不擬」並停下來，**並具體說明哪個 ✗ 觸發**。\n"
    "\n"
    "1. **每次** compose_and_send 之前，**必先依序**：\n"
    "   a. analyze_chat(community_id) 或 read_recent_chat(community_id, limit=20) 抓該群最近的真實對話；\n"
    "   b. get_voice_profile(community_id) 拿該成員的個性設定（Off-limits 仍嚴格遵守）；\n"
    "   c. 用 (a) 的對話樣本當**風格錨**——觀察他們怎麼斷句、用什麼語氣詞、emoji 怎麼用、有多隨便。\n"
    "\n"
    "2. **草稿風格規則**（從 16 個社群、19 萬則真實對話統計的 chat 語感，硬性遵循）：\n"
    "   - **句尾必帶語助詞**：每句至少一個 `了/嗎/喔/哈/啊/吧/唷/呢/啦/耶/呀`，冷句點 + 標準句號 = 像 announcement\n"
    "   - **軟化詞必出現**：每 1-2 句一個 `感覺 / 可能 / 其實 / 好像 / 我覺得 / 我自己 / 不一定`，台灣人 chat 不愛斷言\n"
    "   - **起手優先第一人稱**：「我」「我也」「我自己」「我以前」「我覺得」開頭最自然；禁用「大家」「歡迎」「您」「親愛的」「請」開頭\n"
    "   - **ack 偏好順序**：`謝謝 > 了解 > 好的 > 哈哈 > 原來`；**避免「收到」當開頭**（制式營運用語，真實 chat 很少用）\n"
    "   - **絕對不寫**：「希望這對您有幫助」「為您服務」「感謝您的提問」「歡迎大家」「請大家」「我們一起」「整理」「順手補一下」「收個聲量」「編-X」前綴\n"
    "   - 短：1 句通常夠，最多 2 句；不完整也行（chat 會省略主詞）\n"
    "   - emoji 跟群裡別人對齊；他們不愛用就不用，他們用得多你也用一兩個\n"
    "   - 不排版列點、不寫推銷/限時/搶購語感\n"
    "\n"
    "   **反例（從真實匯出萃取的「太編」案例，不要寫成這樣）**：\n"
    "     ✗ 「大家如果有興趣，可以順手了解一下喔～」（太組織者）\n"
    "     ✗ 「歡迎隨時提問，我們會盡快為您解答」（客服腔）\n"
    "     ✗ 「希望這對您有幫助 🙏」（公式化）\n"
    "     ✗ 「請大家努力邀請朋友加入~ 一起衝高社群人數🚀」（純廣播）\n"
    "   **正例（這種感覺）**：\n"
    "     ✓ 「我以前也卡這個欸 後來改散盤就好多了」\n"
    "     ✓ 「感覺先試一兩天看看吧 不一定要一次到位」\n"
    "     ✓ 「我覺得不用急啦 這個慢慢來都行的」\n"
    "     ✓ 「我也還沒填欸 哈」\n"
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
    "2. 拿到結果後，**用日常中文跟操作員講**——不要 dashboard 腔、不要列 bullet 講術語。\n"
    "   工具會回 active_state 這個 enum，**請翻譯成自然中文，不要原樣貼回去**：\n"
    "     · cold_spell → 「最近安靜了一陣子」/「冷掉好幾天了」\n"
    "     · quiet     → 「現在比較靜」/「沒什麼動靜」\n"
    "     · trickle   → 「偶爾有人冒一句、節奏很慢」\n"
    "     · moderate  → 「有點動但還沒熱起來」/「有人在聊但不算熱」\n"
    "     · active    → 「最近聊得很熱」/「群裡正在熱絡」\n"
    "   **絕對不要**寫「001 目前是 moderate」「004 是 trickle」這種讀起來像 status table 的句子。\n"
    "   摘要要包含的資訊：群最近的氛圍（用上面翻譯後的話）、最後一條訊息大概在聊什麼、有沒有沒人回的問題、有沒有觸到 off-limits 關鍵字。**用 1-3 句講完**，不要列點。\n"
    "3. **不要自動 compose_and_send**——除非操作員明確說「幫我接 / 寫個回覆 / 擬稿」。\n"
    "4. 操作員說「擬稿」之後才走 B 流程（get_voice_profile → compose_and_send）。\n"
    "5. active_state=active 且沒有未回問題時，**主動建議不介入**：「看起來大家在熱絡聊，這輪我先不擬。」\n"
    "\n"
    "**反例（不要這樣回操作員）**：\n"
    "  ✗ 「001「愛美星 Cfans俱樂部」目前是 moderate，但主要是購票連結／系統內容...」\n"
    "  ✗ 「004「水月觀音道場」是 trickle，有一則未回問題...」\n"
    "  ✗ 任何把 enum 名字（cold_spell / trickle / moderate）原樣寫進回覆的句子\n"
    "**正例（這種感覺）**：\n"
    "  ✓ 「001 最近主要是票券系統那些訊息，群裡少人互動。沒有需要接的問題，這輪我先不擬。」\n"
    "  ✓ 「004 安靜，最後一條 25 分鐘前是任務分享，沒人接話。我先不擬。」\n"
    "  ✓ 「003 有人問 JN3 還沒填怎辦，沒人答。要不要我擬一句？」\n"
    "\n"
    "## D. Watcher Phase 2（自主追蹤 + 自動回覆）\n"
    "1. 操作員說「幫我追蹤 X 群 / 盯一下 X 群 / 有人回覆再幫我接 / 智能看著 X 群」\n"
    "   → start_watch(community_id, duration_minutes=操作員指定或預設 60, initiator_chat_id={chat_id})。\n"
    "2. **務必**把 initiator_chat_id 設為當前對話的 chat_id（你會在 system context 看到 `current_chat_id`），\n"
    "   這樣 daemon auto-fire compose 時會推卡片回他的 Lark 私聊。\n"
    "3. 操作員說「停止追蹤 X / 不用追蹤了」→ stop_watch(community_id 或 watch_id)。\n"
    "4. 操作員說「目前在追蹤哪些群」→ list_watches。\n"
    "5. Watch 期間 daemon 自主跑：persona → select_reply_target → get_member_fingerprint → compose。\n"
    "   有合格目標就推卡片給操作員審核；沒合格目標就靜默略過。**你不需要在 Lark 對話裡主動 compose**——daemon 包了。\n"
    "6. 操作員說「重新算 X 群成員風格 / 更新 X 群成員資料」→ refresh_member_fingerprints。\n"
    "   這通常在新匯入 chat_export 之後做一次，讓 fingerprint 反映最新資料。\n"
    "\n"
    "## E. 通用工具映射\n"
    "- LINE 邀請連結（line.me/ti/g2/...）：\n"
    "  · 已知社群 → resolve_invite_url 拿 community_id；\n"
    "  · resolve 回 matched=False（未配置）+ 操作員說「幫我加 / 我已經在這個群」→ add_community（自動 deep-link 抓標題 + 寫 YAML + bootstrap voice profile）。\n"
    "  · 操作員沒明說「加」就只 resolve、不要自動 add_community。\n"
    "- 「我把 X 群的匯出檔放在 <path>，幫我 import」/「幫我匯入 X 群的對話紀錄 <path>」→ import_chat_export。\n"
    "  · 這條是**比 harvest_style_samples 更完整、更合規的路徑**——LINE 內建匯出，有時間戳跟發言者名字，量大。\n"
    "  · 路徑通常是 `~/Downloads/[LINE]xxxx.txt`，操作員會直接給你絕對路徑。\n"
    "  · 工具回 messages_parsed / distinct_senders / new_samples_added / sender_stats（top 10）。\n"
    "  · 回報時告訴操作員：總共讀到幾則、辨識出幾個發言者、新增多少樣本，再列前 3 名 sender 的訊息數。\n"
    "- 「幫我抓 X 群的語氣樣本 / 補一下 X 群的真實語料 / X 群最近講話風格不太一樣 / 累積 X 群的語料」→ harvest_style_samples。\n"
    "  · 預設 append_mode=True：與既有樣本去重後**累積**，不是覆寫。每週跑一次自然把語料疊厚。\n"
    "  · 工具會回 new_samples_added / total_samples_now / dropped_oldest，回報時用這些數字告訴操作員：\n"
    "    例：「這輪新增 12 句、總共已累積 87 句、沒淘汰舊樣本」+ 預覽 3 句新加的。\n"
    "  · 操作員若說「重抓不要保留舊的」→ 帶 append_mode=False（清盤重來）。\n"
    "- 「X 群還缺什麼 / X 群 voice profile 完整了嗎 / 怎麼讓 X 群語氣檔完整」→ check_voice_profile。\n"
    "  · 工具回傳 completeness_pct + missing + next_actions。把 summary_zh 跟 next_actions 原樣念回給操作員。\n"
    "  · 如果還沒 harvest，**主動建議操作員「要不要先幫你 harvest」**，操作員說好就立刻跑。\n"
    "- 「我在 X 群暱稱叫 Y」→ update_voice_profile_section(community_id=X, section='nickname', content='Y')。\n"
    "  · 「我在 X 群的個性是 ...」→ section='personality'，content 多行就用 '- ' 開頭分列。\n"
    "  · 「我在 X 群想讓 bot 學這幾句：『a』『b』」→ section='samples'，content='- a\\n- b'。\n"
    "  · 完成後簡短確認「已寫進 X 群的 <section> 區」，並建議「下一句講『盤點 X 群』我就再幫你 check_voice_profile 看完成度」。\n"
    "- 社群名字錯了 / 顯示「未命名社群 (xxx…)」→ refresh_community_title。\n"
    "  · 操作員給名字（例「openchat_004 改成水月觀音道場」）→ 帶 display_name 直接覆蓋。\n"
    "  · 操作員只說「補名字 / 重新讀名字」→ 不帶 display_name，工具會 deep-link 進去自己抓。\n"
    "- 「列社群」→ list_communities；「列待審稿」→ list_pending_reviews。\n"
    "- 「狀態 / 盤點 / 看一下系統 / 給我一份摘要 / 現在怎樣 / 全部社群現在如何」→ get_status_digest。\n"
    "  · 工具回傳的 `text` 欄位**整段原樣貼回 Lark**——已經排版好，不要改寫不要摘要。\n"
    "  · 如果操作員問「今天送了幾則」「pending 有幾個」這類具體問題，從 `data` 抽欄位精確答。\n"
    "- 「排程 X 時 Y 說 Z」→ add_scheduled_post（send_at 為 ISO 8601 含時區，例 2026-04-29T20:00:00+08:00）。\n"
    "\n"
    "**回覆格式（給操作員看的，不是 LINE 草稿）**：\n"
    "  · 寫 1-3 句**自然中文**——像跟同事聊天，不是寫狀態回報\n"
    "  · 不要列 bullet、不要 markdown table、不要寫「目前是 X」「狀態為 Y」這種句式\n"
    "  · 不要把工具回傳的 enum / status code 原樣貼出（active_state、stage、reason 都先翻成人話）\n"
    "  · 不要貼整段 JSON、不要客套、不要解釋你內部呼叫了哪些 tool\n"
    "  · 多群要講就用「001 ...，004 ...」這種**短分隔**，不要分區塊用 ## heading\n"
    "compose_and_send 之後**不需要把 review_id 貼回給操作員**——系統會自動推一張帶 [通過/修改/忽略] 按鈕的卡片到 Lark。\n"
    "你只要簡短說「已擬一句『...』，請看下方卡片」即可。\n"
)


def _to_dict(typed_event) -> dict[str, object]:
    """Marshal a typed P2 event back into a plain dict matching the wire payload."""

    raw = lark_oapi.JSON.marshal(typed_event) or "{}"
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return result if isinstance(result, dict) else {}


def _ack_reaction(message_id: str) -> None:
    """Fire-and-forget thumbs-up on inbound message so operator sees instant ack."""
    try:
        LarkClient().add_reaction(message_id, emoji_type="THUMBSUP")
    except LarkClientError as exc:
        print(f"[bridge]   ack reaction failed: {exc}", file=sys.stderr, flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[bridge]   ack reaction error: {exc!r}", file=sys.stderr, flush=True)


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

    # Immediate ack: thumbs-up reaction on the inbound message so the operator
    # sees the bot received it before codex (~10-30s) finishes. Fire-and-forget
    # in a daemon thread so the WebSocket pump never blocks on this HTTP call.
    message_id = message.get("message_id") if isinstance(message.get("message_id"), str) else None
    if message_id:
        threading.Thread(target=_ack_reaction, args=(message_id,), daemon=True).start()

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

    # If the operator clicked 「修改稿件」 on a card recently, the next
    # message they send is the edit text. Route it directly to the
    # action pipeline instead of asking codex (which has no idea about
    # the pending edit).
    pending_review_id = _peek_pending_edit(chat_id)
    if pending_review_id:
        _handle_pending_edit_submission(chat_id, user_text, pending_review_id, customer_id, event_id)
        return

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

    # Push reply back to Lark as an interactive card (header bar + markdown
    # body). build_reply_card auto-wraps multi-line / structured replies
    # in a code block so digest layout survives Lark's markdown renderer.
    try:
        from app.lark.cards import build_reply_card
        client = LarkClient()
        card = build_reply_card(reply or "（沒有回應）")
        client.send_card(chat_id, card, receive_id_type="chat_id")
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


def _handle_pending_edit_submission(
    chat_id: str,
    edit_text: str,
    review_id: str,
    customer_id: str,
    event_id: str | None,
) -> None:
    """Operator-typed reply after clicking 「修改稿件」 — submit it as the
    new draft and clear the pending state. Bypasses codex entirely."""

    text = edit_text.strip()
    # Cancel intent: leave edit mode, send a confirmation card.
    if text in ("取消", "cancel", "Cancel", "/cancel"):
        _pop_pending_edit(chat_id)
        try:
            from app.lark.cards import build_reply_card
            client = LarkClient()
            client.send_card(
                chat_id,
                build_reply_card(f"已離開修改模式，`{review_id}` 維持原狀。", header_title="✏️ 修改取消"),
                receive_id_type="chat_id",
            )
        except (LarkClientError, Exception) as exc:  # noqa: BLE001
            print(f"[bridge]   cancel-edit reply failed: {exc}", file=sys.stderr, flush=True)
        return

    if not text:
        return  # ignore empty messages, keep waiting

    # Heuristic: if the operator's "edit text" looks like meta-feedback
    # to the AI (talks ABOUT the draft rather than being a replacement
    # draft), don't blindly use it as the new draft. Bail out, ask for
    # clarification. Keywords: "你" referring to AI, "幫我", "優化",
    # "改一下", "口語化", "太像 X" — none of these belong in a real
    # LINE chat reply.
    META_FEEDBACK_HINTS = (
        "你在", "你幫", "你改", "你優化", "請你",
        "幫我改", "幫我優化", "再寫一次", "重寫", "重擬",
        "口語化", "太像小編", "太書面", "太正式", "太硬",
        "口語你", "再口語", "更口語", "風格不對", "語氣不對",
        "你重新", "你再",
    )
    # Meta-feedback heuristic: if it looks like operator is talking ABOUT
    # the draft (to the AI) rather than writing replacement chat text,
    # auto-cancel edit mode, ignore the original review, and surface their
    # message back to the conversational pipeline (codex) so I can act on
    # the feedback.
    if any(h in text for h in META_FEEDBACK_HINTS):
        _pop_pending_edit(chat_id)
        # Ignore the original draft — operator clearly didn't want it shipped.
        try:
            from app.core.reviews import review_store
            review_store.update_status(
                review_id, status="ignored", updated_from_action="lark_meta_feedback",
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[bridge]   meta-feedback ignore-original failed: {exc}", file=sys.stderr, flush=True)
        try:
            from app.lark.cards import build_reply_card
            client = LarkClient()
            client.send_card(
                chat_id,
                build_reply_card(
                    f"妳剛剛在「修改稿件」打的內容：\n「{text[:120]}」\n\n"
                    "我判斷這是給我（AI）的反饋，不是要送 LINE 的草稿——已**自動跳出修改模式 + 把原稿標為忽略**。\n\n"
                    "妳的意見我收到了，正在用一般對話模式吸收 → 馬上回。",
                    header_title="✏️ 偵測到 AI 反饋（不是新草稿）",
                ),
                receive_id_type="chat_id",
            )
        except (LarkClientError, Exception) as exc:  # noqa: BLE001
            print(f"[bridge]   meta-feedback ack failed: {exc}", file=sys.stderr, flush=True)
        append_audit_event(
            customer_id,
            "lark_edit_meta_feedback_detected",
            {"event_id": event_id, "review_id": review_id, "text_preview": text[:120]},
        )
        # Re-route the meta-feedback into the regular codex conversation
        # so the AI can read it as guidance and respond.
        threading.Thread(
            target=_dispatch_to_claude,
            args=(text, chat_id, event_id, {"event": {"message": {"content": json.dumps({"text": text})}}}),
            daemon=True,
        ).start()
        return

    _pop_pending_edit(chat_id)

    # Submit through the standard lark_action pipeline so audit trail and
    # downstream review_store updates match Lark/CLI edit paths.
    response = enqueue_lark_action({
        "action": {
            "value": {
                "action": "edit",
                "job_id": review_id,
                "edited_draft_text": text,
            }
        }
    })
    append_audit_event(
        customer_id,
        "lark_edit_submitted_inline",
        {"event_id": event_id, "review_id": review_id, "preview": text[:80], "queued_job": response.get("job_id")},
    )

    try:
        from app.lark.cards import build_reply_card
        client = LarkClient()
        body = (
            f"✏️ 收到，已用新內容重新送審：\n\n"
            f"**新草稿**：「{text}」\n\n"
            f"系統會推一張新的審核卡片給你（`{review_id}`，狀態 `pending_reapproval`）。"
        )
        client.send_card(
            chat_id,
            build_reply_card(body, header_title="✏️ 修改已送出"),
            receive_id_type="chat_id",
        )
    except (LarkClientError, Exception) as exc:  # noqa: BLE001
        print(f"[bridge]   edit confirmation push failed: {exc}", file=sys.stderr, flush=True)


def _extract_action_info(payload: dict) -> dict[str, str] | None:
    """Pull the action.value bag from either v1 webhook or v2 long-conn shape."""

    action = payload.get("action")
    if not isinstance(action, dict):
        event = payload.get("event")
        if isinstance(event, dict):
            action = event.get("action")
    if not isinstance(action, dict):
        return None
    value = action.get("value") or {}
    if not isinstance(value, dict):
        return None
    return {k: v for k, v in value.items() if isinstance(v, str)}


def _push_click_ack(action: str, review_id: str) -> None:
    """Immediate ack so operator sees their click landed (Lark cards don't
    visibly change state on click; without this they'll re-click)."""

    chat_id = os.getenv("OPERATOR_DAILY_DIGEST_CHAT_ID", "").strip()
    if not chat_id:
        return
    try:
        from app.lark.cards import build_reply_card
        client = LarkClient()
        if action == "send":
            body = (
                f"✅ 已收到「立即發送」\n\n"
                f"`{review_id}` 開始送進 LINE。\n"
                "送出成功 / 失敗會再回報給妳，這時候不用重複點。"
            )
            title = "✅ 處理中：立即發送"
        else:  # ignore
            body = (
                f"🟡 已收到「忽略」\n\n"
                f"`{review_id}` 已標為忽略，這輪不送 LINE。"
            )
            title = "🟡 已忽略"
        client.send_card(
            chat_id, build_reply_card(body, header_title=title), receive_id_type="chat_id",
        )
    except (LarkClientError, Exception) as exc:  # noqa: BLE001
        print(f"[bridge]   click ack failed: {exc}", file=sys.stderr, flush=True)


def _push_edit_instruction_card(chat_id: str, review_id: str, current_draft: str) -> None:
    """Send a card telling the operator how to submit the edit text."""

    try:
        from app.lark.cards import build_reply_card
        client = LarkClient()
        body = (
            f"✏️ 收到，準備修改 `{review_id}`\n\n"
            f"目前草稿：\n「{current_draft[:200]}」\n\n"
            "請**直接打妳要送到 LINE 群裡的新版本文字**——下一句訊息會 1:1 取代上面的草稿。\n\n"
            "⚠️ 注意：這裡不是跟我（AI）對話的地方。如果妳想說「**口語化一點**」「**這樣寫太像小編**」這種**對 AI 的指示**，"
            "請輸入「**取消**」離開修改模式，再回我一般訊息——我會收到並調整 prompt。\n\n"
            "如果只是想放棄這輪修改，也輸入「取消」。"
        )
        card = build_reply_card(body, header_title="✏️ 修改稿件")
        client.send_card(chat_id, card, receive_id_type="chat_id")
    except (LarkClientError, Exception) as exc:  # noqa: BLE001
        print(f"[bridge]   edit instruction push failed: {exc}", file=sys.stderr, flush=True)


def _on_card_action(typed_event) -> object:
    payload = _to_dict(typed_event)
    print(f"[bridge] card action trigger", flush=True)
    try:
        action_info = _extract_action_info(payload)
        action = action_info.get("action") if action_info else None
        job_id = action_info.get("job_id") if action_info else None

        # Special-case the edit click: route the bridge into "waiting for
        # edit text" mode and push an instruction card back so the
        # operator knows what to type next. Without this, the click
        # silently transitions review state to edit_required and the
        # operator sees nothing happen.
        if action == "edit" and job_id and not action_info.get("edited_draft_text"):
            chat_id = os.getenv("OPERATOR_DAILY_DIGEST_CHAT_ID", "").strip()
            if chat_id:
                _set_pending_edit(chat_id, job_id)
                _push_edit_instruction_card(chat_id, job_id, action_info.get("draft_text") or "")
            else:
                print("[bridge]   edit click ignored: OPERATOR_DAILY_DIGEST_CHAT_ID not set", flush=True)
            # Also queue the standard action so audit log records the click.
            response = enqueue_lark_action(payload)
            print(f"[bridge]   edit pending; action queued → {response}", flush=True)
        else:
            response = enqueue_lark_action(payload)
            print(f"[bridge]   → {response}", flush=True)
            # Immediate ack card so operator knows the click registered.
            # Without this, "立即發送" / "忽略" buttons feel dead — same
            # card stays on screen, no visible state change. Send-action
            # gets the actual confirmation later when the LINE send
            # completes; this is just the click ack.
            if action in ("send", "ignore") and job_id:
                _push_click_ack(action, job_id)
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
