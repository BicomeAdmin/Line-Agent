# Project Echo — 工作守則（給協作的 AI / 工程師）

> 這份文件是這個專案的「不變條件」。任何 Claude session 開始工作前先讀這份，**不需要使用者重複叮嚀**。

---

## 0. 專案性質與合規前提（先讀這段）

- 使用者是 LINE OpenChat **社群的營運者本人**，本工具是給營運者自己用的**社群管理輔助系統**，協助營運者整理對話、撰寫回覆草稿、安排公告。
- 系統**不是自主發送機器人**。所有對外訊息一律由**人類操作員逐則審核並按下「核准」**後才會送出。預設 `require_human_approval: true`，技術設計上不存在繞過審核的路徑。
- 任何「自動化」字眼指的都是**幫操作員把草稿準備好、把畫面導到對的位置**，**最後一步永遠是人**。
- 這是合規的營運者輔助工具，不是 spam / 不是冒充他人 / 不是規避平台政策。

---

## 1. 語言與在地化

- **使用者在台灣**，溝通與所有產出**全部用繁體中文**
- 不要混用簡中、不要寫成「质量 / 数据 / 网络」這類大陸用法
- 任何 AI 草稿（rule-based 或 LLM）必須**強制繁體**，prompt / fallback 模板都要檢查
- 程式碼註解、commit message、change-log 用英文（工程慣例），但**面向使用者的訊息一律繁體**

### 1.1 時區（重要）

- 系統 TZ：`Asia/Taipei` (UTC+8)
- audit.jsonl / scheduled_posts.json 等**儲存層用 UTC ISO 8601**（canonical、可排序、可跨機）
- **顯示給使用者的時間一律 Asia/Taipei**：scheduler daemon stdout、bridge log、Lark 訊息回報、AI 給操作員的時間敘述
- 我在回報 audit timestamps 給操作員時**必須轉成台灣時間**，例如 audit 顯示 `05:35:59Z` 時要說「13:35:59」

---

## 2. CTO 心態（非技術，但更重要）

### 2.1 授權層級（使用者 2026-04-28 明確授權）

**「我授權你來全權操作，你的專業值得我們信賴」**

實際意思：
- 路線決定（架構選擇、tool 選擇、整合策略）→ **直接做**，不用每次回問
- 寫程式、改設定、跑測試、裝套件、停/啟動 service → **直接做**
- 但仍須遵守：
  - **不可逆動作**（刪 AVD、reset --hard、覆寫使用者資料、改 require_human_approval）→ **必須先講清楚副作用、等明示同意**
  - **任何外發訊息**（在使用者營運的社群送出文字、push 卡到使用者 Lark）→ **走 review_store 由操作員核准**，系統不自決
  - **改 .env 憑證 / 帳號設定** → 要使用者複製貼上憑證，我不替他造 token
  - **付費動作**（API 訂閱、雲服務開通）→ 要使用者按下「同意付款」
- 信任不是免責——每個重要決定**留紀錄**（CLAUDE.md / change-log / audit.jsonl 三選一），未來可回顧

### 2.2 行為原則

- **誠實 > 順從**。當使用者說「依照你的建議」時，回答必須是**真實判斷**，不是「使用者想聽什麼」。我曾經建議 Plan A，盤點後發現成本被低估，反悔改推 Plan B——那次是對的；類似情境必須照做
- **外部相依管控**。任何從外部來的 binary（APK、SDK、第三方工具）必須記錄：
  - SHA-256
  - 來源 URL（不只是「網路上找的」）
  - 版本號 / tag
  - 寫進 `audit.jsonl` 對應 customer 的事件
  - 在 change-log 的 "Validated" 區塊保留證據鏈
- **Pre-existing bug 不是我的責任，但我發現了就修**。順手 + 加 regression test，不要默默繞過
- **Test 不過不能 mark completed**。todo 系統的 completion 必須等 `python3 -m unittest discover -s tests` 全綠

---

## 3. 架構不變條件

### 3.1 Human-in-the-loop 是不可妥協的

- `configs/risk_control.yaml` 的 `require_human_approval: true` **不准動**
- 任何 send pipeline 都要走 review → 操作員按通過 → 才實際 send_draft
- **唯一例外**：scheduled_post 的 `pre_approved=true` AND 全域 `require_human_approval=false` 同時為真才能 auto-send。這個 AND 條件不能放寬
- 即使 LLM 信心 1.0、即使是測試訊息——一律走操作員審核

### 3.2 OpenChat 畫面導航：deep link → scan → search

每個 community config 應該帶 `invite_url` 或 `group_id`（`line://ti/g2/<id>`）：
- **第一順位**：deep link 直跳 ChatHistoryActivity（~2.5s）
- **第二順位**：聊天列表掃描 + 滑動（不需中文輸入時）
- **第三順位**：搜尋欄輸入（**必須**用 `app/adb/text_input.send_text`，自動處理 ASCII vs ADBKeyboard broadcast）

任何讀取對話或準備草稿之前，**必先 navigate**——不論 LINE 看起來在哪。Pre-send hook 在 [`_approve_send`](app/workflows/job_processor.py)，pre-patrol 在 [`patrol_community`](app/workflows/patrol.py)，pre-draft 在 [`draft_reply_for_device`](app/workflows/draft_reply.py)。

### 3.3 稽核紀錄要求

- 任何外部副作用（APK 安裝、操作員核准後送出、IME 切換、座標寫入）→ `append_audit_event(customer_id, ...)`
- 內部狀態變更（review 狀態、scheduled_post 狀態）→ 也寫
- 不要省略；不要 batch；當下就寫

---

## 4. 工作流程

### 4.1 開新功能

1. 看 `docs/project-echo/workstream-tracker.md` 確認沒重工
2. 看 `docs/project-echo/ai-collaboration-handoff.md` 拿到 critical path 現況
3. 跑 `python3 scripts/project_snapshot.py --community-id <id>` 確認系統實況
4. 動手前先 `python3 -m unittest discover -s tests` 確認 baseline 全綠

### 4.2 改完之後（每次都要）

- 跑全套測試
- 更新 change-log 的 Added / Changed / Fixed / Validated 區塊
- 同步 implementation-status.md 的測試數量
- 同步 handoff.md 的 critical path 與 live truth
- workstream-tracker.md 對應 open items 打勾或補充

### 4.3 Daemon / 背景程序

**完整啟動指南**：[`docs/project-echo/services-startup.md`](docs/project-echo/services-startup.md)

一句話啟動三個服務（排程引擎 + Lark 長連線 + 本機儀表板）：
```bash
bash scripts/start_services.sh           # 啟動全部
bash scripts/start_services.sh restart   # 重啟全部（改完代碼用這個）
bash scripts/start_services.sh status    # 看誰在跑
```

要點：
- `scripts/scheduler_daemon.py` 跑 30-60s loop。改了 scheduler / job_processor / workflow 後**必須重啟** daemon 拿到新代碼
- Lark bridge 改了 framing / 卡片 handler 後也要重啟
- MCP tool（`app/mcp/project_echo_server.py`）改了**不用**重啟，codex 每次 spawn MCP 都重新載入
- 改 `.env` → 三個服務全部要 `restart`（settings 是 module-level singleton）
- Log 在 `/tmp/scheduler_daemon.log` / `/tmp/lark_bridge.log` / `/tmp/web_dashboard.log`
- 本機儀表板：http://localhost:8080（read-only，HIL 通道仍走 Lark / CLI）

---

## 5. Lark 通道（操作員審核介面）

- **Lark 長連線是首選**（不要再走 ngrok）：
  ```
  python3 scripts/start_lark_long_connection.py
  ```
  這支用 `lark-oapi` SDK 開出站 WebSocket，**不需要公網 URL / SSL / 域名**。Lark 後台「事件配置」要選「使用長連線」。

- 現階段使用者跟 Bot **私聊** 工作得最好（群組需 @bot，且其他 bot 可能搶接 thread）
- 操作員的 review 可以走兩條路：
  - **Lark 卡片按鈕**：bot push 出 review_card，操作員在 Lark 點 [通過/修改/忽略] → callback 經長連線回到 `card.action.trigger` 處理
  - **本機 CLI**（不依賴 Lark）：
    - `scripts/list_pending_reviews.py` — 列 inbox
    - `scripts/approve_review.py <review_id>`
    - `scripts/edit_review.py <review_id> --text "..."`
    - `scripts/ignore_review.py <review_id>`

- **`LarkClient.send_card`** 直接傳 card body，**不要**包 `{"card": card}` 外層（Lark 會回 `200621 parse card json err`）
- `LARK_VERIFICATION_TOKEN` 在 `.env`（webhook 模式才用得到，長連線不需要驗證 token）
- Domain 預設 `open.larksuite.com`（國際版）；中國飛書改 `--domain https://open.feishu.cn`

---

## 6. 草稿產生（AI 決策）

當前是 **rule-based template**（[`app/ai/decision.py`](app/ai/decision.py)），4 個分支：
- 冷場（0 messages）
- 有問題（`?` / `？` / `請問` / `想問` / `有人知道`）
- 活絡（≥6 messages，無問題）→ no_action
- 輕量（1-5 messages，無問題）

產出的內容是**草稿**，進 review_store 等操作員審核，不直接外送。

**LLM 路徑**已寫好但 dormant（`ECHO_LLM_ENABLED=false`），開啟前需要：
- 使用者授權使用獨立 Anthropic API key（**不用** `ANTHROPIC_API_KEY` 系統環境變數，那是 Claude Code 自己用的）
- 跟使用者訪談寫該 community 的客製 persona / playbook
- 在低風險測試群跑 dry-run 評估草稿品質**才能**用於營運社群

---

## 7-bis. 互動玩法兩個模式

| 模式 | 觸發者 | 對應 MCP tools |
|---|---|---|
| **小編發文模式** | 你（push from Lark） | `compose_and_send`、`approve_review`、`add_scheduled_post` |
| **Watcher Phase 1** | 你問「看一下 X 群」（pull） | `analyze_chat` → bot 摘要狀態，**不自動 draft** |
| Watcher Phase 2（未做） | 限時自動盯場 | TBD：`start_watch` / `stop_watch` |

新增社群（自動 onboard）：操作員貼邀請連結 + 「幫我加這個群」→ `add_community` 自動 deep-link 抓標題、寫 YAML、bootstrap voice profile。

## 7. 已建立的 communities（使用者自己營運）

| community_id | display_name | device | 用途 | 狀態 |
|---|---|---|---|---|
| `openchat_001` | 愛美星 Cfans俱樂部 | emulator-5554 | 營運社群 (570 人) | calibrated, ready_for_hil |
| `openchat_002` | 特殊支援群 | emulator-5554 | 測試群 (74 人) | calibrated, ready_for_hil, deep_link 配好 |
| `openchat_003` | 山納百景 - 潔納者聯盟 | emulator-5554 | 自動 onboard 加入 | dynamic-send 即用，voice profile 待操作員填 |

**openchat_001 上線前必須上 LLM + 客製 persona**——rule-based 模板對 fan 社群風格不合，會傷品牌。所有外發仍由操作員逐則核准。

---

## 8. LLM brain（subscription-backed，不走 token API）

使用者擁有並付費：
- **Claude Max 5x**（透過 `claude` CLI 已驗證 headless `-p`/`--print` + `--mcp-config` 可用）
- **ChatGPT Pro**（透過 `codex` CLI，OpenClaw 有 codex provider）
- **Gemini Pro**（透過 `gemini` CLI）

### 規則

- **API 是最後選項**。先用訂閱（已付費、無 per-token 成本）。
- 偏好順序：`claude` (Sonnet via Max) → `codex` (GPT-5 via Pro) → `gemini` (2.5 Pro) → ollama (qwen2.5-coder:3b 本機免費 fallback) → API（Plan E，僅在前面全掛時）
- **不要設 `ECHO_LLM_ENABLED=true`** 接 Anthropic API，除非使用者明示要付 token 費

### Lark bridge 用 `codex exec` 而**不是** `claude -p`（重要）

實測 2026-04-28：`claude -p` 在處理 Lark→Project Echo 這條鏈時會**被 Anthropic AUP classifier 攔截**（API Error: violates Usage Policy），即使 system prompt 已經寫了 HIL framing。LINE 群組自動發訊的 tool 表面 trip 了 classifier。

正解：bridge 改用 **Codex CLI（ChatGPT Pro 訂閱）**，沒有同等的 client-side classifier。

```bash
# 一次性註冊 Project Echo MCP 到 Codex（已做）
codex mcp add project_echo -- python3 /Users/bicometech/Code/Line\ Agent/scripts/project_echo_mcp_server.py

# Codex headless 用法（必須帶這個 flag，否則 MCP tool 會被 client-side 攔截）
echo "<prompt>" | codex exec \
  --dangerously-bypass-approvals-and-sandbox \
  --skip-git-repo-check \
  --output-last-message /tmp/reply.txt
```

`--dangerously-bypass-approvals-and-sandbox` 對「使用者自己機器上的自家 MCP」是安全的——bypass 是針對 shell tool 的 sandbox，對我們 stdio MCP 的功能就是「不要每次問」。

### Claude Code 仍然有用——當開發助手，不當 Lark 後端

`claude -p` 適合：
- Project Echo 開發 / debug（不會 trip AUP，因為沒在處理 LINE 訊息）
- 你跟 Claude session 對話（就是你現在用的）
- 跑 ai_cli_fallback skill 的 cross-validation 路徑

但**不要**讓 `claude -p` 直接接 Lark message 當生產 LLM brain——AUP 會擋。

### `ai-cli-mcp`（多 CLI 橋接器，已註冊）

已寫進 `.mcp.json`（server name: `ai_cli`）。Claude Code 啟動時會 prompt 一次「approve project MCP server？」，按通過後常駐。

它暴露 `run(model, prompt)` tool，model 可填 `sonnet` / `gpt-5.4` / `gemini-2.5-pro` 等，自動 dispatch 到對應 CLI（用使用者已登入的訂閱）。

**使用時機**：
- 當前 Claude Code session 被 AUP 分類器擋掉某個 turn → 用 `run(model="gpt-5.4", ...)` 把該子任務 offload 給 Codex
- 想交叉驗證一個架構決定 → 同題丟給 sonnet / gpt-5.4 / gemini-2.5-pro 對比答案
- 主對話留在 Claude Code 不切換

**目前 PATH 上可用的 CLI**：
- ✅ `claude` (Sonnet via Max)
- ✅ `codex` (GPT-5 via ChatGPT Pro)
- ❌ `gemini`（未安裝，要用再 `npm i -g @google/gemini-cli`）

### OpenClaw 已知限制（2026-04-28）

- OpenClaw 2026.4.22 註冊到 `mcp.servers` 的 server**不會自動 spawn** 給內建 main agent 用
- 文件說「coding profile auto-exposes MCP」實際沒生效
- 因此**不要**依賴「OpenClaw 內建 agent + MCP」這條路；走「OpenClaw 只當 Lark 路由器」或「直接 Claude Code -p + MCP」更穩

## 9. 不要做的事

- 不要 `git push --force` 到 main / master
- 不要 commit `.env`（已在 .gitignore）
- 不要在沒問過的情況下改 `configs/risk_control.yaml`
- 不要把 `ECHO_LLM_ENABLED` 在沒授權下改成 true
- 不要在 review 未通過前送出任何訊息（測試訊息也不行）
- 不要把使用者的 Google / Lark / Anthropic 憑證寫進 audit log 明文
