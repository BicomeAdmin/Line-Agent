# Project Echo — SKILL 升級路線圖

> 整合「自家盤點」+「外部研究」（GitHub / 業界 / NLP / 反偵測）的優先順序清單。每條都標註對應的 Paul《私域流量》方法論層次（VCPVC / 九宮格 / KOC 金字塔）。

最後更新：2026-04-29

---

## 自家盤點：當前體質快照

### 已扎實 ✅
- HIL 鐵則（review_store + Lark 卡片，操作員逐則點通過）
- 5 個社群 onboarded：001 愛美星 / 002 特殊支援群 / 003 山納百景 / 004 水月觀音道場 / 005 Bicome 私域顧問
- operator_nickname 全部設好（比利 / 阿樂2 / 愛莎 / 翊 / Eric_營運）
- chat_export_import 五個社群完成（從 LINE .txt → voice profile + member_fingerprint）
- Watcher Phase 2 自主流程（persona → select_target → fingerprint → compose）
- 活動時段 gate（10:00-22:00 TPE）
- Web dashboard（localhost:8080）+ Lark daily digest + aging alert
- Paul VCPVC + 九宮格哲學寫進 CLAUDE.md（§0-prelude / §0.5）

### 結構性弱點 ⚠️

| 弱點 | 影響 | 對應 Paul 缺項 |
|---|---|---|
| 話題比對是 bigram，沒語義 | 「股票」vs「投資理財」對不上 | V (Value) — 看不出真正相關性 |
| 情緒只看正則 pain/broadcast | 偵測不到隱性疑惑、不滿、興奮 | C (Culture) — 文化感知薄 |
| 風格 fingerprint 只 3 個維度 | 鏡映只到字數+emoji+句尾，缺虛詞 / 標點 / 換行癖 | 互動參與深度不足 |
| 沒有成員關係圖 | 不知道誰跟誰熟、誰是樞紐人物 | KOC 金字塔識別不出 |
| 沒有 lifecycle tagging | 不知道哪些成員流失、沉默、活躍 | 留存階段沒指標 |
| 沒有 KPI 追蹤 | 九宮格的已讀率/互動率/UGC數量沒在算 | 九宮格只在哲學層、未到數據層 |
| 操作節奏可預測（固定 60s poll, 精準 tap） | LINE 反自動化偵測訊號明顯 | 工具痕跡 |
| 操作員 edit 訊號未回流 | 你修的草稿沒成為 bot 學習資料 | 沒有「實時回饋優化」(Paul Step 4) |

### 風險（不修會痛）

1. **單點 emulator** = 系統 SPOF（emulator 掛掉全停）
2. **本機 audit + state** 沒備份 = 硬碟壞了全沒
3. **沒測 multi-customer** = 將來服務 customer_b 才會發現問題
4. **CKLAUDE.md 規範依賴 session 自覺讀** = 新 session 可能漏掉

---

## 外部研究結論

### A. 中文社群營運工具（GitHub 與業界）

| 來源 | 學什麼 | 不學什麼 |
|---|---|---|
| `meetbryce/open-source-slack-ai` | 4-bucket 摘要 prompt（重點/決定/待辦/未解問題） | — |
| `asherkin/discograph` | 回覆鄰接演算法 → KOC 候選識別（NetworkX） | — |
| `wangrongding/wechat-bot` | 殭屍粉/沉默成員啟發式判定 | 整套 wechaty 架構 |
| `openscrm/api-server` | 4 階段 lifecycle tag schema（new/active/silent/churned） | Go 後端、API 層 |
| `mochat-cloud/mochat` | 「群 SOP」概念（時序自動化模板） | 整套企微 SCRM |
| Tracardi / RudderStack | 事件命名規範（user.message_sent 等） | 整套 CDP 基建（過重） |
| `chatgpt-on-wechat` | — | 整套 agent framework（會打亂現有架構） |

### B. NLP 升級路徑

| 技術點 | 推薦工具 | 部署成本 | 取代什麼 |
|---|---|---|---|
| 語義相似度 | `BAAI/bge-small-zh-v1.5` (95MB, 30-80ms/句) | 小 | bigram topic_overlap |
| 情緒分類（8 類） | `Johnson8187/Chinese-Emotion` (400MB) | 小-中 | regex pain/broadcast |
| Stylometric 擴充 | 手刻特徵 + `jieba-tw` | 中（無套件可直接用） | avg_length+emoji 三件組 |
| 對話分群 | 先「時間+@提及+回覆鏈」純規則，後續再上 BERTopic | 規則小、模型中 | （目前無） |

### C. 反偵測 + 自動化健康

| 改動 | 效益 | 工程量 |
|---|---|---|
| 操作節奏 jitter（poll ±25%、tap ±5px、人類 reading pause） | LINE 偵測訊號減少 | 0.5 天 |
| Bezier swipe（取代直線 input swipe） | 模擬手指軌跡 | 1-2 天 |
| 二手真機（Pixel 4a ~NT$2,500）取代 emulator | 解決最大訊號源 | 0.5 天設定 |
| OCR fallback（PaddleOCR + scrcpy 截圖） | LINE 鎖 dump 時的備援 | 1 週 |
| Maestro 重構 navigate | 程式碼宣告式更乾淨 | 2-3 天 |
| 紅燈：Frida / 備份解密 / Appium | — | — |

---

## 優先順序總表（按 ROI 排序）

### 🥇 Tier 1 — 本週可做完的高 ROI（每條 ≤ 1 天）

| # | 動作 | 對應問題 | 工程量 |
|---|---|---|---|
| 1 | 建 `app/ai/embedding_service.py`，引入 BGE-small-zh，取代 reply_target_selector 的 bigram topic_overlap | 語義相似度立刻有感 | ½ 天 |
| 2 | 操作節奏 jitter（poll 45-75s、tap ±5px、navigate 間 200-1200ms 隨機停頓） | LINE 偵測風險直接降 | ½ 天 |
| 3 | analyze_chat 輸出改 4-bucket（重點/決定/待辦/未解）借 open-source-slack-ai prompt | 你問「看一下 X 群」品質 +50% | ½ 天 |
| 4 | 8 類繁中情緒模型，怒/沮喪 → 升級到 operator attention queue；疑惑 → compose 觸發 | 文化敏感度 | 1 天 |
| 5 | 九宮格 KPI 追蹤器（已讀率/互動率/UGC數量/導購率 per community per day），dashboard 加區塊 | 從哲學層落到數據層 | 1 天 |

**Tier 1 總計約 3.5 天**，做完整個系統會「會看意思、會抓重點、會看群健康度、不會像 bot」。

### 🥈 Tier 2 — 基礎建設升級（2 週內）

| # | 動作 | 工程量 |
|---|---|---|
| 6 | 建 member relationship graph（discograph 演算法 + NetworkX）→ 自動產 KOC 候選清單 | 1-2 天 |
| 7 | Lifecycle tagging（new/active/silent/churned per OpenSCRM）→ reply_target_selector 跳過 churned 成員 | 3-5 天 |
| 8 | Edit feedback loop：操作員修的草稿存成 (原, 改) pair → 之後 compose prompt 帶最近 5 對學習 | 2-3 天 |
| 9 | Stylometric 擴充（虛詞頻率、標點習慣、換行癖、注音文殘留） | 3-5 天 |
| 10 | Bezier swipe（非直線 ADB swipe motionevent 序列） | 1-2 天 |

### 🥉 Tier 3 — 戰略改動（月內）

| # | 動作 | 工程量 |
|---|---|---|
| 11 | 採購二手真機（Pixel 4a），切換 emulator → 真機 | 0.5 天 |
| 12 | OCR fallback 路徑（PaddleOCR + scrcpy） | 1 週 |
| 13 | Group SOP 自動化（新人入群 1h / D+1 / D+7 sequenced drafts）per mochat-cloud | 1 週 |
| 14 | BERTopic 對話分群（等資料夠多再做） | 1 週 |
| 15 | 備份策略（git LFS or 本機定期備份 audit + state） | 半天 |

### 🔭 Tier 4 — 長期觀察

- LINE Messaging API 路徑（若想把部分流量導到官方帳號完全合規）
- 多 customer 測試（有第二個客戶才做）
- 多語支援（不是現在的事）

### ❌ 明確不做

- Frida hook / build.prop 改寫 — 違反 LINE ToS
- LINE 備份解密 - 灰色地帶
- Appium - ROI 低於 Maestro
- 整套 CDP 基建（Tracardi 等） - 過重，3 群場景不需要
- chatgpt-on-wechat agent framework - 會打亂現有 MCP + Codex 架構

---

## 我建議的執行順序

**這週：Tier 1 全做完**（3.5 天）

理由：每條都 ≤ 1 天、各自獨立、做完整體質量質變。BGE embedding 是其中最關鍵的——之後 Tier 2 的 #6 #7 #9 都會用到。

**下週：Tier 2 第 6-8（5-8 天）**

關係圖 + lifecycle + edit feedback。這三條把系統從「看到單則訊息」升級到「看到一個社群的整體生態」，呼應 Paul 的「用戶營運金字塔」。

**月內：Tier 3 第 11-13**

真機 + OCR + Group SOP。這時候系統已經夠強，重心轉移到風險控制與規模化。

---

## 與 Paul《私域流量》原則對應

| Tier 1 #1 BGE embedding | V (Value) — 真正抓到話題相關性 |
| Tier 1 #4 情緒分類 | C (Culture) — 偵測社群當下情緒生態 |
| Tier 1 #5 KPI 追蹤 | 九宮格四指標落地（已讀率 / 互動率 / UGC / 導購率）|
| Tier 2 #6 KOC 候選 | 用戶營運金字塔頂端識別 |
| Tier 2 #7 lifecycle | 生命週期管理（拉新→留存→活躍→裂變的成員流動）|
| Tier 2 #8 edit loop | Paul 的 AI Step 4「實時回饋優化」 |
| Tier 3 #13 Group SOP | mochat 風格的「新人 1h 歡迎 / D+1 引導 / D+7 喚醒」自動排程 |

---

## 給操作員的決策建議

我建議：**從 Tier 1 #1 BGE embedding 開始做，今天/明天就動手。**

理由：
- 工程量小（½ 天）
- 一改見效（topic_overlap 立刻智能化）
- 後續 Tier 2 都依賴它（KOC 識別、相似度比對都要 embedding）
- 風險低（純讀取、無外發、本機跑）

你說 OK 我就動手。或者你有別的想先做的，告訴我。
