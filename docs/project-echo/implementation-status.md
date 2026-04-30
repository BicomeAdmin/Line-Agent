# Project Echo Implementation Status

Last updated: 2026-04-30

## Snapshot

- Overall MVP progress: `95%` — 9 層防線接近完整，HIL gated，prod-signal 收集中
- Control plane progress: `97%`
- Observability / ops progress: `98%` — alert_aggregator + voice_profile_watcher + audit_log_stats 補齊觀察層
- Real LINE / OpenChat chain progress: `92%` — 5 communities onboard, send pipeline 補上 pre/post-send verification
- Test suite: **`689 unit tests passing`**（2026-04-29 280 → 2026-04-30 689，+409 in 24h，跨 8 支 session 整批 land）

## What Is Working

### 1. Core HIL Pipeline (live in 5 communities)

- `add_community` / `import_chat_export` / `set_operator_nickname`（Phase B 三道防線：形似漢字警示 + chat_export 0-hit warning + verification_hint）
- `start_watch` (manual) + `auto_watch` (per-community opt-in, daily 10:00-22:00 TPE)
- `watch_tick` → `select_reply_target` → `compose` → review_card to Lark → operator approve/edit/ignore → `send_draft`
- All 5 communities (`openchat_001`-`005`) calibrated（operator_nickname：比利 / 阿樂2 / 愛莎 / 翊 / Eric_營運），fingerprints / KPI / lifecycle / relationship graph 齊備

### 2. AI / Decisioning (Tier 1+2 landed 2026-04-29 + brand mode 2026-04-30)

- BGE embedding（`BAAI/bge-small-zh-v1.5`，semantic topic_overlap）
- Chinese-Emotion 8-class（`Johnson8187/Chinese-Emotion`）
- Member relationship graph + KOC top-5
- Lifecycle tagging（new / active / silent / churned）
- Edit feedback loop（Paul《私域流量》Step 4 「實時回饋優化」）
- Stylometric MemberFingerprint（11+ dims）
- 九宮格 KPI tracker
- Persona memory（`voice_profiles/<community>.md` v2，含 value_proposition / route_mix / stage / engagement_appetite + completeness gate）
- **Composer brand mode**（2026-04-30）— `composer_brand_v1.md` 廣播 register，與會話 register `composer_v1` 分離；scheduled_post compose_mode 觸發
- Bezier swipe + operation jitter（anti-fingerprint）

### 3. Lark Bridge (Codex backend)

- Long-connection WebSocket via `lark-oapi` SDK（無 ngrok）
- `codex exec --dangerously-bypass-approvals-and-sandbox`
- Per-chat history (last 6 turns) prepended
- Review cards [通過 / 修改 / 忽略]
- 0 token cost (ChatGPT Pro subscription)

### 4. Send Pipeline 9 層防線（2026-04-30 補齊）

| 層 | 模組 | 用途 |
|---|---|---|
| 1. HIL gate | `risk_control.yaml require_human_approval` | 鐵則，不可動 |
| 2. Pre-send navigate | `patrol.patrol_community` | 確保在正確 OpenChat |
| 3. **Pre-send openchat_verify** | `app/workflows/openchat_verify.py` | 動作前一刻再驗群 |
| 4. **Pre-send drift guard** | `_check_pre_send_drift` (job_processor) | compose 與 approve 之間漂移偵測 |
| 5. **Pre-send bot_pattern_guard** | `app/workflows/bot_pattern_guard.py` | 掃明顯 bot tells |
| 6. **Pre-send send_safety** | `app/ai/send_safety.py` | 共用安全檢查 |
| 7. ADB tap_type_send | `app/adb/send.py` | 動態送出按鈕解析 + Bezier swipe |
| 8. **Post-send send_verification** | `app/workflows/send_verification.py` | 驗訊息真在群裡出現 |
| 9. **Post-send input-box check** | `app/adb/input.py:check_input_box_cleared` | 抓殘留草稿（靜默失敗訊號） |

任一層 fail → audit `severity=important` + `action_hint`，不自動重試、不繞 HIL。

### 5. Observability Stack (2026-04-30 land)

- **alert_aggregator** — severity-tiered (info/warn/important) collation；empty-when-quiet；dashboard 「decision panel」呈現
- **audit_redact + export_audit_redacted** — PII-safe export（暱稱 / member ID 遮罩）給除錯分享
- **audit_log_stats** — daemon 啟動印 size + freshness + warn (50MB) / critical (200MB)；invariant audit
- **voice_profile_watcher** — 偵測 off_limits / nickname 異動 drift，alert 升 important
- **self_detection_health** — 24h 檢查 operator self-ratio，route_mix 動態 threshold（ip>0.4→5%、info>0.4→10%、其他 2%）

### 6. Daemon 韌性 (2026-04-30 land)

- **orphan_recovery** — 重啟時 in-flight review/job 分流：terminal-state 不動、graceful 重排 idempotent、operator-domain 只 audit
- **scheduled_post recurrence** — `daily@HH:MM` / `weekly:DOW@HH:MM` / `monthly:N@HH:MM`，sent 後 auto-spawn 下次 occurrence
- **scheduled_post compose_mode** — brief 取代 text，daemon 在 send_at - lead 跑 `_compose_brand_draft`；compose_mode 永不 auto-send

### 7. Operations / Safety

- `scripts/start_services.sh` — 一鍵啟動 scheduler_daemon + lark_bridge + web_dashboard
- `scheduler_daemon` — 30-60s loop（patrol + scheduled posts + watch tick + dashboard push + auto_watch + orphan_recovery）
- Local web dashboard at `http://localhost:8080`（read-only，HIL 走 Lark/CLI）
- `scripts/backup_state.py` rotating tar.gz
- `scripts/event_health_report.py` — 09:00 daily digest + 10:00 watcher health
- **`scripts/export_audit_redacted.py`**（2026-04-30）— PII-safe audit export

### 8. MCP Tool Surface

`add_community` / `import_chat_export` / `analyze_chat` / `compose_and_send` / `approve_review` / `add_scheduled_post`（含 brief / compose_mode / recurrence flags）/ `start_watch` / `set_voice_profile` / `set_operator_nickname` / `compute_community_kpis` / `kpi_summary` / `build_relationship_graph` / `get_koc_candidates` / `compute_lifecycle_tags` / `get_lifecycle_distribution` / `refresh_member_fingerprints` + `ai_cli` MCP for cross-LLM offload.

## Current Live State

- **5 active communities** (`openchat_001`-`005`), all calibrated
- **scheduler_daemon** + **lark_bridge** + **web_dashboard** running steady
- **edit_feedback signal**: 1 entry so far (openchat_003) — gating Tier 3 expansion
- **auto_watch adoption**: 0 communities opted in（操作員可自行 flip）
- **compose_mode 試點**：openchat_004 voice_profile v2 已升級，可開始 dry-run

## What's Pending Decision (not engineering blockers)

1. **LLM brain activation** — currently rule-based + Codex compose dual track。Anthropic API 路徑仍 dormant
2. **Tier 3 next item** — gated on edit_feedback signal accumulation
3. **Acceptance state machine refresh** — `acceptance_status.py` / `project_snapshot.py` 還在說 `line_not_openchat`，cosmetic
4. **conversion-rate KPI** — needs operator-labelled order data
5. **compose_mode 推到 005**（broadcast-heavy）— 等 004 dry-run 跑出 voice 對得上再說

## Known Limitations

- Read-rate KPI: 不可從 ADB / chat export 抽取
- "new" lifecycle counts inflated（chat exports cover ~2 weeks）
- LARK_VERIFICATION_TOKEN unset（long-connection 不需要）

## Test Coverage

- **689 / 689 unit tests passing**
- 2026-04-30 24h 內 +409 tests（跨 8 支 session 整批 land + P3 cleanup）
- Run: `python3 -m unittest discover -s tests`
