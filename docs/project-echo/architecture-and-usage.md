# Project Echo — 架構地圖 & 使用方式（一頁讀完）

> 9 輪紅隊強化後，整套系統散在 change-log 各條目裡很難 reload。這份是給「回來幾天後想 30 秒抓回 mental model」的人看的。
>
> 細節請查 [`change-log.md`](change-log.md)，敘事版看 [`growth-log.md`](growth-log.md)。

---

## 0. 一句話定位

**LINE OpenChat 社群營運者輔助系統**。所有外發訊息經操作員人工審核才會送出（HIL gated）。底層哲學：Paul《私域流量》— 留量比流量重要，價值塑造 > 短期轉換（CLAUDE.md §0.5）。

---

## 1. 三大目標 ↔ 工程實踐

| 目標 | 對應路徑 | 關鍵模組 |
|---|---|---|
| **品牌小編定時發文** | scheduled_post compose_mode + recurrence | `scheduled_posts.py`、`scheduled_post_recurrence.py`、`composer_brand_v1.md` |
| **社群活絡有溫度** | watcher → selector → composer 自主擬稿 | `watch_tick_inproc.py`、`reply_target_selector.py`、`codex_compose.py`、`composer_v1.md` |
| **最強儀表盤** | 本機 :8080 + alert layer + KPI 紅綠燈 | `dashboard_server.py`、`alert_aggregator.py`、`kpi_tracker.py` |

---

## 2. 系統地圖（資料流）

```
                                                  ┌─────────────────────────┐
LINE 群成員發言                                    │ daemon 30-60s tick loop │
  │                                                └─────┬───────────────────┘
  ▼                                                      │
[adb / line_chat_parser]                                 ├── enqueue_due_patrols()
  │ chat_ui_message_text + row_timestamp 配對 (Layer 1)  ├── enqueue_due_scheduled_posts()
  │ → ChatMessage{sender, text, ts_epoch, is_self}       ├── tick_watches()
  ▼                                                      ├── detect_voice_profile_changes()
[reply_target_selector]                                  └── orphan_recovery (boot only)
  │ 評分 + 時態硬閘 (Layer 2: >3h disqualify, ...)
  │ + bot_pattern_guard (≥10/day block)
  │ + cross_community guard (verify_chat_title)
  ▼
[codex_compose / compose_brand_post]
  │ 安全規則 (prompt 注入隔離 <chat_data>)
  │ + voice_profile (placeholder 反偵)
  │ + 時態提示 (Layer 1: target_age + 群冷度)
  │ + Taiwan chat register cheat-sheet
  ▼
[server post-composer override]
  │ if target >180min → force should_engage=false
  ▼
[ReviewStore + off_limits_hash 快照]
  │ pending → Lark card 推送給操作員
  ▼
[操作員審 → approve / edit / ignore]
  │
  ▼
[approve_send drift guard + chat_title_verify + send_safety_lint + send_draft]
  │ 30min+ stale review + group hot → abort
  │ 含 URL/電話/email/金流 → block
  │ 動手前再驗一次 chat title
  ▼
[ADB tap_type_send → LINE 群]
  │
  ▼
[post-send verification]
  │ read_recent_chat 找 self-bubble 比對 expected
  │ 不匹配 → status=sent_unconfirmed + audit
  │
  ▼
[audit log + edit_feedback + dashboard refresh]
```

---

## 3. 九層防線速查

| 層 | 名稱 | 觸發點 | 失敗 audit |
|---|---|---|---|
| 1 | 時態 (parser+composer) | 抓 ts_epoch + prompt 教時態 | `composer_codex_skipped` |
| 2 | Selector 硬閘 | >3h disqualify | （score=0） |
| 3 | Server post-composer override | LLM say yes 但 target stale | `composer_temporal_override` |
| 4 | Cross-community guard | 動手前驗 header_title | `*_chat_title_mismatch` |
| 5 | Cancel race guard | codex 期間 post 被 cancel | `scheduled_post_compose_dropped_after_cancel` |
| 6 | Approve drift guard | review 老 + 群已變 | `approve_send_aborted_temporal_drift` |
| 7 | Off-limits drift hash | voice_profile 改了 | `approve_send_off_limits_drift` |
| 8 | Send safety lint | URL/電話/email/金流 | `send_safety_blocked` / `_warned` |
| 9 | Post-send verification | LINE 真進群 | `send_verification_failed` |
| ＋ | Bot-pattern guard | 累計 ≥10/day block | `watch_tick_blocked_bot_pattern` |
| ＋ | Recurrence cap | 預設 daily=90 / weekly=52 / monthly=24 | （normalize_recurrence） |
| ＋ | Prompt 注入隔離 | `<chat_data>` / `<operator_brief>` 標籤 | `off_limits_hit=prompt_injection_attempt` |

每條都寫獨立 audit event_type，後續調 prompt / 改閾值各自看訊號（feedback_three_layer_defense）。

---

## 4. 操作員日常使用

### 4.1 啟動 / 停止
```bash
bash scripts/start_services.sh           # 啟動全部三服務
bash scripts/start_services.sh restart   # 改完代碼用這個
bash scripts/start_services.sh status    # 看誰在跑
```
log 在 `/tmp/scheduler_daemon.log` / `/tmp/lark_bridge.log` / `/tmp/web_dashboard.log`。

### 4.2 看儀表盤
打開 http://localhost:8080
- 頂部「⚡ 今天該關注什麼」alert panel：blocking → important → info
- 每筆 alert 帶 `→ action_hint` 指出下一步
- KPI panel 用顏色 badge 標 quiet/cool/warm/hot（依 Paul 九宮格）

### 4.3 排程貼文（直送）
```bash
python3 scripts/add_scheduled_post.py customer_a openchat_004 \
    --send-at "2026-05-04T20:00:00+08:00" \
    --text "晚安各位..." \
    --recurrence "weekly:mon@20:00"
```

### 4.4 排程貼文（LLM compose）
```bash
python3 scripts/add_scheduled_post.py customer_a openchat_004 \
    --send-at "2026-05-04T20:00:00+08:00" \
    --compose --brief "靜坐入門引子" \
    --recurrence "weekly:mon@20:00" \
    --compose-lead-hours 4
```
在 `send_at - 4h` 跑 codex_compose，產草稿推 Lark 卡待審。**永不 auto-send**（HIL 鐵則）。

### 4.5 dry-run 驗證 prompt 品質
```bash
python3 scripts/dry_run_compose.py --community-id openchat_004 \
    --brief "想發的主題"
```
不寫 review_store / 不推 Lark / 不寫 audit。純看 codex 產出品質。

### 4.6 對外分享 audit（隱私防線）
```bash
python3 scripts/export_audit_redacted.py customer_a --since-hours 24 \
    --level default > redacted.jsonl
```
成員訊息全部替換成 `[redacted N chars]`。

### 4.7 自主 watcher
透過 MCP tool 呼叫 `start_watch(community_id, duration_minutes=720)`（從 codex / Lark / claude 的 MCP client 都行）。或更穩定的：在社群 yaml 加 `auto_watch.enabled: true`，daemon 會在 `start_hour_tpe` 自動開、`end_hour_tpe` 自動停。

預覽當前所有 watch 狀態：
```bash
python3 scripts/preview_autonomous.py
```

---

## 5. 新社群 onboarding（六步缺一不可，CLAUDE.md §7-bis）

```
1. add_community(invite_url=..., display_name=...)
2. import_chat_export(community_id, file_path=...)        ← 操作員手動匯出 LINE 對話
3. refresh_member_fingerprints(community_id)
4. set_operator_nickname(community_id, nickname=...)      ← 最關鍵的一步！沒這個 selector 會崩
   → 確認 chat-export 裡你的真實 sender 名，補進 community.yaml 的 operator_aliases
5. set_voice_profile / 操作員親自寫 voice_profile.md
   → frontmatter (value_proposition / route_mix / stage / appetite) + nickname / personality / off_limits 必填
   → 寫 TODO / FIXME / 「待補」會被偵測拒絕
6. start_watch(community_id) 或設 yaml auto_watch
```

---

## 6. 故障排查

### 「儀表盤顯示 watch_tick_chat_title_mismatch」
LINE 在 daemon 讀的瞬間被切到別群。確認 device 上 LINE 是否有通知 / 別 app 喚起。alerts 累積 >3 次/天就值得查。

### 「approve 後 status=sent_unconfirmed」
ADB 說送了但 read_recent_chat 沒看到 self-bubble。可能 IME 切失敗 / LINE crash。手動確認群裡有沒有那則訊息，沒有就手動補送。

### 「composer_codex_unavailable」連續觸發
codex CLI 失效（PATH / 訂閱 / 登入）。`which codex && codex --version` 驗證。期間 watcher 會 fallback 到 rule-based composer（品質會差）。

### 「daemon 啟動印 ⚠️×6 HIL DISABLED」
你或誰把 `ECHO_REQUIRE_HUMAN_APPROVAL=false` 寫進 .env。3 秒紅警告 + sleep 給 Ctrl-C 救回窗口。要救 → unset 或設 true → restart daemon。

### 「scheduled_post 卡在 due/reviewing 狀態超過幾小時」
daemon crash 後沒清乾淨。重啟 daemon 會跑 orphan_recovery：due >5min 重置回 scheduled、reviewing >30min 無 review record 標 skipped。

### 「audit log 變大」
啟動時印的 size_human 看一眼。50MB 開始考慮 archive，200MB 強烈建議。`scripts/export_audit_redacted.py` 可導出 + 清理舊 entries。

---

## 7. 文件索引（往哪查什麼）

| 想知道什麼 | 看哪個檔 |
|---|---|
| 為什麼這樣設計 / 哲學 | `CLAUDE.md` §0-prelude + §0.5（Paul《私域流量》） |
| 每次改了什麼 | `change-log.md` |
| 敘事版（為什麼當時走這條路） | `growth-log.md` |
| 啟動 / 停止 / log | `services-startup.md` |
| 故障 + 復原 | `incident-recovery-runbook.md` |
| 操作員日常 | `operator-runbook.md` |
| 社群 onboarding 細節 | CLAUDE.md §7-bis + `voice-profile-completion.md` |
| AI 心法（給協作 AI） | `ai-self-identity.md` |
| 工程進度 | `implementation-status.md` |
| 待辦 / 路線 | `workstream-tracker.md` + `future-roadmap.md` |

---

## 8. 已知欠缺（誠實清單）

工程上沒處理但風險評估後可接受：

- 日期分隔符（「昨天」/「M月D日」）解析未做：群停聊 1+ 天後舊訊息會被誤判為今天，時態 gate 可能錯放行
- 多 process 寫 state file 的 OS 級 file lock 未做：理論存在 race，實測沒撞過
- 系統時鐘大跳的冪等性：post 可能雙觸發 / 跳過
- Codex CLI 整段失效時無 graceful fallback：alert 浮上但要操作員處理
- Dashboard 純 read-only：alert 看到要動仍切 Lark / CLI
- 「今天 vs 昨天」diff view：alert 是 snapshot，看不出趨勢
- Prompt A/B 實驗框架：改 prompt 不能比對品質前後

優先處理建議：操作員產生實際痛點再修，不主動加。

---

## 9. 哲學 reminder（給回來的自己）

- **HIL 是不可妥協的**（CLAUDE.md §3.1）
- **留量比流量**（每張防線寧可少擋一張可疑、不要擋掉十張正常）
- **單一真相 + 三層獨立防線**（feedback_single_source_of_truth + feedback_three_layer_defense）
- **觀察性是信任的一部分**（不只擋錯誤、要讓主人看見系統在做什麼）
- **拼命強化自身**（feedback_strengthen_to_completion — 觸發即抵達真人，不可撤回）
