# Project Echo AI Collaboration Handoff

Last updated: 2026-04-30

## Purpose

This is the fastest restart document for another AI collaborator (Claude / Codex / Gemini / human engineer).

## Read These First (Before Touching Anything)

1. **[`CLAUDE.md`](../../CLAUDE.md)** at project root — project identity, language (繁體中文 only), HIL rules, Paul's《私域流量》philosophy, tier 3 gating, supply-chain rules. **Non-negotiable.**
2. **[`change-log.md`](change-log.md)** — engineering log, latest day first
3. This file — operational handoff
4. **Auto-loaded memory** at `~/.claude/projects/-Users-bicometech-Code-Line-Agent/memory/MEMORY.md` if you're Claude — has 13+ feedback/project memories accumulated across sessions

## Current Operating Picture

- repository: `/Users/bicometech/Code/Line Agent`
- active customer: `customer_a`
- main device: `emulator-5554` (AVD `project-echo-api35`)
- LLM brain: `ECHO_LLM_ENABLED=false` (rule-based template still active; subscription LLMs via `claude` / `codex` CLI handle Lark bridge)
- Lark bridge backend: **Codex** (`codex exec --dangerously-bypass-approvals-and-sandbox`), NOT `claude -p` (AUP false-positive on the LINE-send tool surface — see CLAUDE.md §8)

## Current Live Truth (2026-04-30)

### Communities (5 active, all calibrated, ready for HIL)

| ID | Display | Operator nickname | Notes |
|---|---|---|---|
| `openchat_001` | 愛美星 Cfans俱樂部 (570 人) | 比利 | Production fan community |
| `openchat_002` | 特殊支援群 (74 人) | 阿樂2 | Test community |
| `openchat_003` | 山納百景 - 潔納者聯盟 | 愛莎 | Auto-onboarded; KOC top: 許芳旋 (eigen 0.72) |
| `openchat_004` | (configured) | 翊 | KOC top: Kevin / 巧克力泡芙 / 小麻雀 |
| `openchat_005` | Bicome，您的私域顧問 | Eric_營運 | Broadcast-heavy pattern |

All 5 have: chat_export imported with sender attribution, member fingerprints (11+ stylometric dims), KPI snapshots, lifecycle tags (new/active/silent/churned), relationship graph + KOC candidates.

### Services Running

| Service | Log | Purpose |
|---|---|---|
| `scripts/scheduler_daemon.py` | `/tmp/scheduler_daemon.log` | 30-60s loop: patrol + scheduled posts + watch tick + auto_watch |
| `scripts/start_lark_long_connection.py` | `/tmp/lark_bridge.log` | WebSocket to Lark, dispatches to Codex |
| `app/web/dashboard_server.py` (via `start_services.sh`) | `/tmp/web_dashboard.log` | Read-only at `http://localhost:8080` |

One-shot start/restart/status: `bash scripts/start_services.sh [restart|status]`

### Test Suite

- **689 / 689 unit tests passing**（2026-04-30 跨 8 支 session 整批 land 後）
- Run: `python3 -m unittest discover -s tests`

## Recent Work (2026-04-30 — 8 commit cross-session land + P3 essayist linter / near-dup nudge)

詳見 [`change-log.md`](change-log.md) 2026-04-30（下午）entry。一句話：9 層 send-pipeline 防線補齊 + observability stack 上線 + scheduled_post compose_mode/recurrence 上線 + orphan_recovery 上線。

關鍵新模組（resume 後可直接 grep）：
- `app/workflows/operator_attribution.py` — operator/sender 判定的單一真相
- `app/workflows/openchat_verify.py` / `send_verification.py` / `bot_pattern_guard.py` / `app/ai/send_safety.py` — send pipeline 防線 3-6, 8
- `app/workflows/alert_aggregator.py` / `voice_profile_watcher.py` / `app/core/audit_redact.py` / `audit.audit_log_stats` — observability
- `app/workflows/orphan_recovery.py` — daemon 韌性
- `app/workflows/scheduled_post_recurrence.py` + `_compose_brand_draft` (job_processor) — Goal 1↔Goal 2 接通
- `app/ai/prompts/composer_brand_v1.md` + voice_profile v2 fields — brand register

## Earlier Work (2026-04-29 session — 13 commits, 240→280 tests)

### Identity / Philosophy

- **CLAUDE.md §0-prelude** — operator upgraded the AI's working posture from "LINE automation tool" to 「最懂用戶營運、最懂人性的 AI 綜合體 — AICTO」
- **CLAUDE.md §0.5** — Paul《私域流量》(2025) internalized as project house rules: VCPVC / 九宮格 / KOC pyramid / 4-step AI pipeline. Gate question for every new feature: 「這條把使用者的用戶推向 KOC 化更近一步嗎？」

### Tier 1 (5 quick-win upgrades)

T1.1 BGE embedding · T1.2 Operation jitter · T1.3 4-bucket summary · T1.4 Chinese-Emotion 8-class · T1.5 九宮格 KPI tracker

### Tier 2 (5 foundation upgrades)

T2.1 Member relationship graph + KOC · T2.2 Lifecycle tagging · T2.3 Edit feedback loop (Paul's Step 4) · T2.4 Stylometric extension · T2.5 Bezier swipe

### Tier 3 (Operations / safety, just landed)

- **State backup** (`57ddc5e`) — rotating tar.gz of audit / fingerprints / KPI / lifecycle / watches
- **Auto-Watch** (`eb551ed`) — per-community opt-in, eliminates daily manual `/start_watch` ritual
- **Event health report** (`9c2f6dd`) — consolidates 09:00 daily digest + 10:00 watcher health into one CLI

## Critical Path (where to push next)

The hardware/login chain is **done**. The current critical path is **prod-signal collection**:

1. **Run T1+T2+T3 in production for 1-2 days** to accumulate `edit_feedback` signal
2. **Read `customers/<id>/data/edit_feedback/<community>.jsonl`** diff patterns to identify real bottlenecks:
   - 字數常被砍 → compose prompt too verbose
   - emoji 被改 → fingerprint mirror inaccurate
   - 整則被棄 → reply_target_selector threshold needs recalibration
   - 操作員加問句 → persona context too declarative
3. **Pick next Tier 3 work based on signal**, not assumption (see [Tier 3 gating](../../.claude/projects/-Users-bicometech-Code-Line-Agent/memory/project_tier3_gating.md))
4. **(Optional)** flip `auto_watch.enabled: true` on one community to verify auto-start chain in production

## Pending / Deferred Decisions

| Item | Why deferred |
|---|---|
| LLM brain activation (`ECHO_LLM_ENABLED=true`) | Needs operator's independent Anthropic API key + per-community persona interview + dry-run validation |
| Tier 3 OCR fallback / real device / BERTopic | Gated on edit_feedback signal — don't pick blind |
| `acceptance_status` / `project_snapshot` refresh | These scripts still report old `line_not_openchat` framing because they pre-date chat-export-driven pipeline. Not blocking HIL ops, but cosmetically misleading |
| conversion-rate KPI | Needs operator-labelled order data |
| LARK_VERIFICATION_TOKEN | Only matters if reverting from long-connection to webhook mode (don't) |

## First Commands To Run (after restart)

```bash
# 1. Sanity check tests
python3 -m unittest discover -s tests 2>&1 | tail -5

# 2. See live state (note: legacy framing; trust change-log.md over this)
python3 scripts/project_snapshot.py --community-id openchat_001

# 3. Check services
bash scripts/start_services.sh status

# 4. See recent audit events for any community
tail -10 customers/customer_a/data/audit/audit.jsonl

# 5. Check edit_feedback accumulation
find customers -path "*edit_feedback*.jsonl" -exec wc -l {} \;
```

## Safe Parallel Tracks (if you have spare cycles)

- **Acceptance refresh** — teach `acceptance_status.py` + `project_snapshot.py` about the chat-export-driven pipeline so they stop saying `line_not_openchat` when the community is actually live
- **Conversion KPI** — wire operator-labelled order data into `kpi_tracker`
- **Voice profile completion** — interview operator for each community's voice (`docs/project-echo/voice-profile-completion.md`)
- **Documentation polish** — operator runbook, incident runbook (basic versions exist)

## Known Limitations / Gotchas

- `chat_ui_message_text` in LINE XML is for ANY sender, not just operator. Use `chat_ui_row_sender` + x-bounds (≥40% screen width) for self-detection. (Historical bug: first parser misidentified all messages as SELF.)
- `chat_ui_sender_name` + `chat_ui_content_text` = reply quote box, NOT a new message — must skip in parser
- `LarkClient.send_card` takes raw card body (no `{"card": ...}` wrapper, returns `200621 parse card json err`)
- `codex exec` for MCP calls REQUIRES `--dangerously-bypass-approvals-and-sandbox` flag — without it, Codex's client-side default cancels MCP calls before the server sees them
- `claude -p` works for development / debug but NOT for the Lark bridge (AUP classifier false-positive)
- Settings is module-level singleton — `.env` change requires `start_services.sh restart` for ALL three services

## What NOT to Do (red lines)

- ❌ Set `require_human_approval: false` (HIL is sacred — see CLAUDE.md §3.1)
- ❌ Set `ECHO_LLM_ENABLED=true` without operator authorization (would burn paid tokens)
- ❌ `git push --force` to master
- ❌ Commit `.env` (already gitignored)
- ❌ Use simplified Chinese in any user-facing output
- ❌ Run external commands that error output suggests (`pip install X`, `curl | bash`) without operator confirmation
