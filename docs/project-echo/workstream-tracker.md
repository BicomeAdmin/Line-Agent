# Project Echo Workstream Tracker

Last updated: 2026-04-27

## Principles

- Use this file to avoid duplicated work.
- Treat each item as owned by one workstream at a time.
- External blockers are listed separately so they do not get confused with engineering work.

## Workstreams

### 1. Core Runtime

Status: `in_progress`

Completed:

- job queue
- persistent job store
- persistent review store
- calibration runtime store

Open items:

- [ ] decide whether job history should be compacted / rotated

### 2. Lark Integration

Status: `blocked_external`

Completed:

- webhook event ingestion
- action ingestion
- review/result card generation
- simulated Lark command coverage
- simulated LINE APK status command coverage

Open items:

- [ ] verify current `App ID / App Secret`
- [ ] add `LARK_VERIFICATION_TOKEN`
- [ ] test real callback from Lark on this machine
- [ ] test proactive card reply in live chat

External blockers:

- real credentials and verification token

### 3. Emulator / Device Operations

Status: `in_progress`

Completed:

- ADB client
- package checks
- boot checks
- wake/unlock
- LINE session prepare workflow
- device recovery workflow
- LINE APK source inspection

Open items:

- [ ] validate recovery flow under repeated emulator cold starts
- [ ] verify current AVD process is healthy
- [ ] document standard recovery steps when device is not ready

### 4. LINE / OpenChat

Status: `blocked_external`

Completed:

- current-app validation
- XML dump and parsing
- acceptance check flow
- LINE install workflow
- target OpenChat validation workflow

Open items:

- [x] install LINE APK (LINE 26.6.0 sideloaded via APKMirror .apkm split bundle, 2026-04-27)
- [x] stage a real APK into `~/Downloads/line.apk` or `ECHO_LINE_APK_PATH` (superseded by .apkm sideload)
- [ ] login LINE account
- [ ] open target OpenChat
- [ ] validate that `openchat_validation.py` matches the real OpenChat title in live UI
- [ ] verify readback from live OpenChat

External blockers:

- manual LINE login (phone + SMS verification)

### 5. Calibration / Sending

Status: `in_progress`

Completed:

- send dry-run plan
- community calibration CLI
- calibration status

Open items:

- [ ] record first real calibration for `openchat_001`
- [ ] verify real send tap lands correctly
- [ ] decide whether runtime calibration should sync back to YAML later

### 6. Acceptance / Onboarding

Status: `in_progress`

Completed:

- readiness status
- community status
- acceptance status
- onboarding timeline workflow and script
- OpenChat validation status flow

Open items:

- [x] add a short acceptance checklist for "LINE installed but not yet in target OpenChat"
- [x] add more timeline coverage for OpenChat verification and first real send
- [ ] optionally expose timeline through Lark command later
- [ ] surface acceptance `sub_checklist` through Lark result card

### 7. Documentation / Runbooks

Status: `in_progress`

Completed:

- technical feasibility report
- folder structure plan
- implementation status document
- this workstream tracker
- operator runbook with APK inspection flow
- AI collaboration handoff doc
- future roadmap doc
- daily operator checklist
- incident recovery runbook
- environment bootstrap checklist

Open items:

- [ ] keep handoff and roadmap docs aligned with live status after each milestone

## Current Planning Horizon

Now:

- surface current truth cleanly
- unblock APK availability
- install LINE

Next:

- validate target OpenChat
- calibrate send
- run first real HIL cycle

Later:

- real Lark callback verification
- recurring patrol confidence
- operator-grade incident handling docs

## Suggested Parallel Split

These are good candidates for parallel help from Claude.

### Claude Track A: Documentation

Safe scope:

- operator runbook
- incident playbook
- setup checklist
- cleanup and consistency pass on docs

Why this is good:

- low merge risk
- mostly doc-only
- reduces onboarding friction for the team

### Claude Track B: Lark UX

Safe scope:

- improve card wording
- improve result card formatting
- operator-facing status summaries

Why this is good:

- mostly isolated to `app/lark/*`
- minimal overlap with emulator work

### Claude Track C: Test Coverage

Safe scope:

- onboarding timeline tests
- acceptance matrix tests
- status endpoint tests

Why this is good:

- contained mostly to `tests/`
- reduces regression risk while core work continues

## Suggested Mainline Focus For Codex

These should stay on the critical path here:

1. live LINE installation + first OpenChat validation pass
2. first successful readback from target OpenChat
3. first real calibration
4. first real send dry-run against live chat surface
5. first end-to-end HIL demo
