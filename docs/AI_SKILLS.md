# Project Echo - AI Skills Architecture

本文件定義了 Project Echo (Line Agent) 的 AI 技能 (SKILL) 發展藍圖與實作規格。
開發助手 (Codex / Copilot) 在進行 `app/ai/` 相關模組開發時，應參考本文件之規格。

## 概念概述
在 Project Echo 中，「SKILL」指的是 AI 處理社群營運的特定能力模組。我們將逐步把目前寫死的規則 (Rule-based) 替換為真實的大型語言模型 (LLM) 推論。

目前的優先級如下：
1. **SKILL 1: 核心決策與擬稿 (Context-Aware Decision & Draft)** - 優先替換 `app/ai/decision.py`。
2. **SKILL 2: 危機與競品監控 (Risk & Competitor Alert)** - 整合於 `patrol_device` 流程，背景攔截。
3. **SKILL 3: 破冰話題生成 (Ice-Breaker)** - 社群冷卻過久時的主動推播。

---

## 🥇 SKILL 1: 核心決策與擬稿 (Context-Aware Decision & Draft)

此技能負責閱讀最新的社群歷史對話，判斷是否需要介入，並產生符合品牌人設的草稿。

### 1. 目標檔案與修改範圍
- **目標檔案**: `app/ai/decision.py`
- **現狀**: 使用 `if "奶瓶" in question_text` 等寫死的關鍵字邏輯。
- **目標**: 實作非同步 (async) 或是透過背景 Thread，呼叫本機的 Ollama API 或 OpenClaw Gateway。

### 2. LLM 設定參數
- **推薦模型**: `qwen2.5-coder:3b` (根據 `OPENCLAW_OLLAMA_SETUP.md` 測試結果，支援 Tools 且速度最穩)
- **API Endpoint**: 本機的 `http://localhost:11434/api/generate` 
- **要求格式**: 強制設定輸出為 JSON 格式 (帶入參數 `"format": "json"`)

### 3. Prompt 設計規格

#### System Prompt (系統提示詞)
請在組合 Prompt 時套用以下樣板：

```text
你是一個專業的社群營運專家。你的任務是閱讀 LINE 社群的最新對話紀錄，並根據群組的 Persona (人設) 來決定下一步行動。

【決策邏輯】
1. 如果對話正在熱絡進行，且沒有人提問，請保持安靜 (action: no_action)。
2. 如果有人明確提問，或是對話中出現痛點，請提供專業且符合人設的回答 (action: draft_reply)。
3. 如果群組已經冷場很久，可以考慮丟出一個符合主題的破冰問題 (action: draft_reply)。

【人設與限制】
{persona_text}
- 回答必須自然、簡短，像真人用 LINE 聊天一樣，不要有過多條列式。
- 絕對不要過度推銷，保持客觀中立。
```

#### User Prompt (使用者提示詞)
將抓取到的訊息串列 (`messages`) 轉為 JSON 字串放入：

```text
社群名稱: {community_name}

最近的對話紀錄:
{messages_json}

請分析對話上下文，並嚴格輸出 JSON 格式。
```

### 4. 預期 JSON 輸出 Schema
必須與現有的 `DraftDecision` dataclass (在 `app/ai/decision.py` 中) 對齊，以便無縫接軌原有的飛書卡片審核流程。

```json
{
  "action": "draft_reply | no_action",
  "reason": "這裡用一段話解釋為什麼做出這個決策，例如：'使用者正在詢問投資標的，需要給予建議'",
  "confidence": 0.95,
  "draft": "如果 action 是 draft_reply，這裡填入你要回覆的文字。如果不需要回覆，這裡留白。"
}
```

### 5. 實作注意事項 (給開發助手 Codex 的指示)
1. **HTTP 客戶端**: 請使用 `httpx` (如果需要 async) 或 `requests` 發送 API 請求到 Ollama。
2. **非同步與阻塞處理**: 由於 LLM 推論長達數十秒，如果在 `job_processor.py` 內是同步呼叫，請確保這段邏輯是跑在 Job Worker 背景，不要阻塞 FastAPI 主流程。
3. **防呆與回退機制 (Fallback)**: 若 Ollama 回傳 Timeout 或 JSON 解析失敗，必須捕捉例外 (Exception)，並回傳一個預設的 `DraftDecision` (例如 `action="no_action"`, `reason="llm_fallback"`)，確保系統不會 Crash。
4. **清理 Markdown 語法**: 模型有時可能會在 JSON 外面包夾 Markdown 語法 (如 ````json ... ````)，在做 `json.loads` 前請先寫一段小邏輯去除這些標記。

## 🥈 SKILL 2: 危機與競品監控 (Risk & Competitor Alert)

此技能作為背景守護者，在每次系統巡邏 (`patrol_device` 抓取最新訊息) 時運作，負責找出需要管理員緊急介入的狀況。

### 1. 目標檔案與修改範圍
- **目標檔案**: 新增 `app/ai/risk.py`，並在 `app/workflows/patrol.py` 或 `read_chat.py` 內呼叫。
- **目標**: 當讀取到新訊息時，非同步丟給 LLM 判斷是否有風險。若判斷為高風險，則透過 Lark API 發送緊急通知卡片。

### 2. Prompt 設計規格

#### System Prompt (系統提示詞)
```text
你是一個社群風險控制機器人。你的任務是審查 LINE 社群的最新訊息，判斷是否有違反社群守則的內容。
請嚴格抓出以下幾類風險：
1. "spam" (廣告、詐騙連結、洗版)
2. "toxic" (辱罵、人身攻擊、嚴重負面情緒)
3. "competitor" (提及直接競爭對手品牌，且帶有推薦意味)

如果沒有發現上述情況，請判斷為 "safe"。
```

#### User Prompt (使用者提示詞)
```text
請審查以下最新對話：
{recent_messages_json}

請嚴格輸出 JSON 格式。
```

### 3. 預期 JSON 輸出 Schema
```json
{
  "has_risk": true,
  "risk_type": "spam | toxic | competitor | safe",
  "severity": "high | medium | low",
  "reason": "解釋判斷的理由",
  "culprit_user": "違規者的暱稱 (若能辨識)"
}
```

---

## 🥉 SKILL 3: 破冰話題生成 (Ice-Breaker & Engagement)

此技能負責在社群冷卻時主動帶動氣氛，減輕管理員每天想話題的負擔。

### 1. 目標檔案與修改範圍
- **目標檔案**: 新增 `app/ai/engagement.py`，並整合到 `app/workflows/patrol.py`。
- **觸發條件**: 當 `patrol_device` 發現距離最後一則訊息已經超過 4~6 小時。

### 2. Prompt 設計規格

#### System Prompt (系統提示詞)
```text
你是一個活潑且具備專業知識的社群管理員。這個社群目前已經安靜了好幾個小時，你的任務是丟出一個能夠引發群友共鳴、促使大家參與討論的「開放式問題」。

【人設與限制】
{persona_text}
- 問題必須與本群的主題高度相關。
- 語氣必須自然、親切，像是平常閒聊一樣。
- 絕對不要像在做問卷調查，避免過於生硬。
```

#### User Prompt (使用者提示詞)
```text
社群名稱: {community_name}
過去幾天的熱門話題摘要 (可選): {recent_topics}

請產生一個適合現在推播的破冰訊息，並嚴格輸出 JSON 格式。
```

### 3. 預期 JSON 輸出 Schema
```json
{
  "action": "send_icebreaker",
  "draft": "大家午安～最近...（填入破冰草稿）",
  "expected_engagement": "說明預期群友會怎麼回應，用來幫助管理員判斷是否採用"
}
```

---
**開發建議總結**：
建議 Codex 先將 **SKILL 1** 完整實作並通過本地測試，確認與 Ollama 的連線、JSON 解析與例外處理都穩固後，再把這套打底好的 HTTP Request 邏輯（如 retry 機制、清除 markdown backticks 的邏輯）封裝成一個通用的 `call_llm(prompt, schema)` 共用模組，接著再套用到 SKILL 2 與 SKILL 3 就會非常迅速！
