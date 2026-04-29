# Project Echo Workstream Tracker

Last updated: 2026-04-29

## Principles

- Use this file to avoid duplicated work.
- Treat each item as owned by one workstream at a time.
- External blockers are listed separately so they do not get confused with engineering work.

## Workstreams

### 1. Core Runtime

Status: `done`

Completed:

- job queue
- persistent job store
- persistent review store
- calibration runtime store
- scheduler daemon (30-60s loop, multi-task: patrol + scheduled posts + watch tick + auto_watch + dashboard push)
- audit append (UTC ISO 8601 storage, Asia/Taipei display)
- state backup (`scripts/backup_state.py`, rotating tar.gz)
- event health report (`scripts/event_health_report.py`)

Open items: none active.

### 2. Lark Integration

Status: `done` (long-connection backend, Codex bridge)

Completed:

- webhook event ingestion (legacy webhook mode, deprecated)
- action ingestion
- review/result card generation (raw card body, no envelope)
- long-connection WebSocket via `lark-oapi` SDK (no ngrok / no public URL)
- Lark → Codex bridge replacing Lark → Claude (AUP classifier false-positive)
- per-chat conversation history (last 6 turns prepended to Codex prompt)
- compose / approve / edit / ignore round-trip live verified
- proactive review_card push from `watch_tick`

Open items:

- [ ] (optional) configure `LARK_VERIFICATION_TOKEN` only if reverting to webhook mode

### 3. Emulator / Device Operations

Status: `done`

Completed:

- ADB client + boot/package/wake checks
- LINE session prepare workflow
- device recovery workflow
- LINE install workflow (.apkm split bundle path documented)
- LINE 26.6.0 sideloaded via APKMirror, audit recorded

Open items: none active.

### 4. LINE / OpenChat

Status: `done` (chat-export-driven onboarding replaces ADB-only)

Completed:

- XML parser flow (with sender attribution via `chat_ui_row_sender` + x-bounds)
- LINE foreground validation
- target OpenChat validation workflow
- LINE APK source inspection workflow
- chat export ingest path (10-100x more compliant than UI scraping)
- 5 communities onboarded with full chat export + sender attribution + voice profile bootstrap
- per-community `operator_nickname` configured (mandatory for autonomy correctness)
- send dry-run + first real sends in HIL flow

Open items:

- [ ] `acceptance_status.py` + `project_snapshot.py` still report old `line_not_openchat` framing — cosmetic, doesn't block ops, but misleading. Refresh to understand chat-export-driven pipeline.

### 5. Calibration / Sending

Status: `done`

Completed:

- send dry-run plan
- community calibration CLI
- calibration status
- per-community real coordinates saved (5 communities)
- dynamic send-button resolver in `tap_type_send` (live UI dump + `chat_ui_send_button_image` lookup, calibrated coords as safety net)
- Bezier swipe via `input motionevent` (verified API 35)
- operation jitter (Gaussian sleep / triangular tap / endpoint+duration noise)

Open items:

- [ ] (optional) decide whether runtime calibration should sync back to YAML

### 6. AI / Decisioning (Tier 1+2 landed 2026-04-29)

Status: `mostly done` — pending LLM brain activation decision

Completed:

- rule-based decision template (4 branches: cold / question / lively / light)
- BGE embedding service (T1.1) — semantic topic_overlap
- Operation jitter (T1.2) — anti-fingerprint
- 4-bucket summary (T1.3) — key_points / decisions / action_items / unresolved
- Chinese-Emotion 8-class (T1.4) — reply scoring deltas
- 九宮格 KPI tracker (T1.5) — daily message count / active senders / operator participation / broadcast vs natural
- Member relationship graph (T2.1) — KOC top-5 per community
- Lifecycle tagging (T2.2) — new/active/silent/churned
- Edit feedback loop (T2.3) — Paul's Step 4 「實時回饋優化」
- Stylometric extension (T2.4) — 11+ MemberFingerprint dims (function words / punctuation / typo signature)
- Auto-Watch (T3) — per-community opt-in, daily 10:00-22:00 TPE

Open items:

- [ ] **LLM brain activation** (`ECHO_LLM_ENABLED=true`) — gated on operator authorization for independent Anthropic API key + per-community persona interview + dry-run validation
- [ ] conversion-rate KPI — needs operator-labelled order data

### 7. Acceptance / Onboarding

Status: `done` (chat-export pipeline live)

Completed:

- `add_community` MCP tool — operator pastes invite URL → bot extracts group_id + reads chat title → YAML + voice_profile bootstrap
- `import_chat_export` workflow — operator manually exports → bot parses sender attribution + style samples
- `set_operator_nickname` tool (mandatory step in onboarding SOP)
- 6-step onboarding SOP documented in CLAUDE.md §7-bis
- 5 communities onboarded end-to-end

Open items:

- [ ] refresh `acceptance_status` and `project_snapshot` to recognize chat-export-driven communities (don't say `line_not_openchat` when they're actually live)

### 8. Documentation / Runbooks

Status: `done` (this sync)

Completed:

- technical feasibility report
- folder structure plan
- implementation status (synced 2026-04-29)
- workstream tracker (synced 2026-04-29)
- AI collaboration handoff (synced 2026-04-29)
- operator runbook with APK inspection flow
- daily operator checklist
- incident recovery runbook
- environment bootstrap checklist
- services startup guide (`services-startup.md`)
- voice profile completion checklist
- skill roadmap

Open items:

- [ ] keep all three sync docs (handoff / status / tracker) in step after each significant session — operator's CLAUDE.md §4.2 rule

## Current Planning Horizon

Now (2026-04-29 → 2026-05-02):

- run T1+T2+T3 in production for 1-2 days
- accumulate `edit_feedback` signal across communities
- monitor scheduler / lark / dashboard logs for anomalies

Next (after edit_feedback signal):

- read diff patterns → identify real bottleneck
- pick next Tier 3 item (OCR fallback / real device / BERTopic / group SOP) **based on signal, not assumption**
- consider LLM brain activation if rule-based template hits ceiling

Later:

- conversion-rate KPI (needs operator-labelled order data)
- multi-community cross-reference (Cooperation in VCPVC)
- acceptance / project_snapshot refresh

## Suggested Parallel Tracks

If multiple AI sessions run simultaneously, here are non-conflicting scopes:

### Track A: Acceptance refresh

Update `app/workflows/acceptance_status.py` + `scripts/project_snapshot.py` to understand chat-export-driven communities. Currently they only know the old ADB-readback acceptance flow.

### Track B: Voice profile interview

Walk operator through `voice-profile-completion.md` checklist for each of the 5 communities. Pure conversation + markdown editing, no code changes.

### Track C: Conversion KPI wiring

Design data shape for operator-labelled orders → wire into `kpi_tracker` → expose `conversion_rate` field. Would unblock the last KPI gap in 九宮格 framework.

### Track D: Edit feedback analysis

Once 3-5 days of edit_feedback accumulate, write a one-shot analyzer script that summarizes diff patterns across all communities → recommends concrete prompt / threshold changes.
