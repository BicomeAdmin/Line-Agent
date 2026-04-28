# Project Echo

AI-assisted private community operations prototype for Lark, Android AVD, ADB, and LINE OpenChat workflows.

## Current Prototype Scope

This repository currently includes the Phase 0/1 foundation:

- Lark `url_verification` handling.
- Zero-dependency local webhook server for smoke testing.
- ADB client wrapper with clear error handling.
- Emulator boot/readiness inspection helpers.
- `uiautomator dump` workflow.
- LINE chat XML cleaning and message parsing.
- Human-paced ADB send wrapper with activity-window and random-delay guardrails.
- Zero-dependency YAML config loading for device/customer/risk settings.
- Customer-scoped audit logging and system status snapshots.
- Lark webhook ingestion, background job queue, and command parsing skeleton.
- Persona + playbook context bundle for future Gemini prompt assembly.
- Customer-scoped starter configuration.

## Project Tracking Docs

- [Implementation Status](docs/project-echo/implementation-status.md)
- [Workstream Tracker](docs/project-echo/workstream-tracker.md)
- [Change Log](docs/project-echo/change-log.md)
- [Operator Runbook](docs/project-echo/operator-runbook.md)
- [AI Collaboration Handoff](docs/project-echo/ai-collaboration-handoff.md)
- [Future Roadmap](docs/project-echo/future-roadmap.md)
- [Daily Operator Checklist](docs/project-echo/daily-operator-checklist.md)
- [Incident Recovery Runbook](docs/project-echo/incident-recovery-runbook.md)
- [Environment Bootstrap Checklist](docs/project-echo/environment-bootstrap-checklist.md)

## Smoke Tests

Run unit tests:

```bash
python3 -m unittest discover -s tests
```

Parse the included sample LINE XML:

```bash
python3 scripts/parse_xml_sample.py
```

Check whether the current emulator screen is actually LINE before reading chat:

```bash
python3 scripts/check_line_app.py emulator-5554
```

Inspect the current foreground package:

```bash
python3 scripts/current_app.py emulator-5554
```

Wait until the emulator is fully booted:

```bash
python3 scripts/wait_for_device.py emulator-5554 --timeout 120
```

Print a structured device readiness snapshot:

```bash
python3 scripts/device_status.py emulator-5554
```

Print the whole system readiness snapshot:

```bash
python3 scripts/system_status.py
```

Print the deployment readiness checklist:

```bash
python3 scripts/readiness_status.py
```

Check whether the configured Lark app credentials can fetch a tenant token:

```bash
python3 scripts/lark_auth_check.py
```

The FastAPI app exposes:

```text
GET  /health
GET  /status/acceptance
GET  /status/dashboard
GET  /status/communities
GET  /status/line-apk
GET  /status/onboarding
GET  /status/openchat
GET  /status/project-snapshot
GET  /status/action-queue
GET  /status/milestones
GET  /status/readiness
GET  /status/reviews
GET  /status/system
GET  /status/device/{device_id}
POST /devices/{device_id}/ensure-ready
GET  /status/audit/{customer_id}
GET  /status/jobs
POST /webhooks/lark/events
POST /webhooks/lark/actions
POST /scheduler/tick
```

You can also simulate a Lark text command locally without FastAPI:

```bash
python3 scripts/simulate_lark_event.py "請回報系統狀態"
python3 scripts/simulate_lark_event.py "查詢 emulator-5554 裝置狀態"
python3 scripts/simulate_lark_event.py "請做部署檢查" --wait-seconds 8
python3 scripts/simulate_lark_event.py "請回報 openchat_001 社群狀態" --wait-seconds 6
python3 scripts/simulate_lark_event.py "請回報校準狀態" --wait-seconds 6
python3 scripts/simulate_lark_event.py "請回報 LINE APK 狀態" --wait-seconds 8
python3 scripts/simulate_lark_event.py "請回報 openchat_001 專案快照" --wait-seconds 8
python3 scripts/simulate_lark_event.py "請回報 openchat_001 行動隊列" --wait-seconds 8
python3 scripts/simulate_lark_event.py "請回報 openchat_001 里程碑狀態" --wait-seconds 8
python3 scripts/simulate_lark_event.py "請幫我做 openchat_001 驗收檢查" --wait-seconds 8
python3 scripts/simulate_lark_event.py "請幫我做 openchat_001 OpenChat 驗證" --wait-seconds 8
python3 scripts/simulate_lark_event.py "請幫 emulator-5554 準備LINE" --wait-seconds 20
python3 scripts/simulate_lark_event.py "請幫 emulator-5554 修復裝置" --wait-seconds 20
python3 scripts/simulate_lark_event.py "請幫 emulator-5554 安裝LINE" --wait-seconds 20
```

Preview a draft-reply decision from the current emulator chat context:

```bash
python3 scripts/draft_reply_preview.py emulator-5554 --limit 20
python3 scripts/simulate_lark_event.py "請幫 emulator-5554 擬稿"
```

Run one patrol cycle for a device:

```bash
python3 scripts/patrol_once.py emulator-5554
python3 scripts/simulate_lark_event.py "請幫 emulator-5554 巡邏"
```

Trigger one scheduler tick locally:

```bash
python3 scripts/scheduler_tick.py
python3 scripts/dashboard_status.py
python3 scripts/readiness_status.py
python3 scripts/community_status.py
python3 scripts/acceptance_status.py --community-id openchat_001
python3 scripts/onboarding_timeline.py --community-id openchat_001
python3 scripts/line_apk_status.py
python3 scripts/openchat_validation.py --community-id openchat_001
python3 scripts/project_snapshot.py --community-id openchat_001
python3 scripts/action_queue.py --community-id openchat_001
python3 scripts/milestone_status.py --community-id openchat_001
python3 scripts/ensure_device_ready.py emulator-5554 --wait-timeout 60
python3 scripts/install_line_app.py emulator-5554 --apk-path /absolute/path/to/line.apk
python3 scripts/review_status.py
python3 scripts/calibration_status.py
python3 scripts/audit_tail.py customer_a --limit 20
```

Simulate a Lark card action locally:

```bash
python3 scripts/simulate_lark_action.py job_xxxxx send
python3 scripts/simulate_lark_action.py job_xxxxx ignore
```

Run the local webhook server:

```bash
python3 scripts/dev_webhook_server.py --port 8787
```

Then verify it from another terminal:

```bash
curl -sS http://127.0.0.1:8787/health
curl -sS -X POST http://127.0.0.1:8787/webhooks/lark/events \
  -H 'content-type: application/json' \
  --data '{"type":"url_verification","challenge":"local-ok"}'
```

## ADB Probe

Check whether ADB and a device/emulator are available:

```bash
python3 scripts/adb_probe.py
```

List installed packages:

```bash
python3 scripts/list_packages.py emulator-5554
python3 scripts/list_packages.py emulator-5554 --contains line
```

If ADB is installed somewhere non-standard, set:

```bash
export ECHO_ADB_PATH=/path/to/adb
```

The client also checks common macOS locations:

- `~/Library/Android/sdk/platform-tools/adb`
- `/opt/homebrew/bin/adb`
- `/usr/local/bin/adb`

## Next Hardware Step

Install Android Studio platform-tools or add the existing `adb` binary to PATH. Then start one Android AVD, log in to LINE manually, open a test OpenChat, and run:

```bash
python3 scripts/adb_probe.py
python3 scripts/dump_ui_xml.py emulator-5554
python3 scripts/parse_xml_sample.py customers/customer_a/data/raw_xml/latest.xml
```

This workspace already has a working AVD created:

```bash
python3 scripts/start_emulator.py --avd project-echo-api35 --no-snapshot
python3 scripts/wait_for_device.py emulator-5554 --timeout 120
```

Once real XML parsing looks good, calibrate coordinates for the message input and send button:

```bash
python3 scripts/send_test_message.py "測試訊息" \
  --device-id emulator-5554 \
  --input-x 100 --input-y 1800 \
  --send-x 1000 --send-y 1800 \
  --dry-run

python3 scripts/send_test_message.py "測試訊息" \
  --device-id emulator-5554 \
  --input-x 100 --input-y 1800 \
  --send-x 1000 --send-y 1800
```

Replace coordinates with the actual values from the emulator screen.

Persist the calibrated coordinates into Project Echo runtime state:

```bash
python3 scripts/set_community_calibration.py customer_a openchat_001 \
  --input-x 100 --input-y 1800 \
  --send-x 1000 --send-y 1800 \
  --note "first calibration pass"
python3 scripts/calibration_status.py
```

Preview a real community send plan without tapping the emulator:

```bash
python3 scripts/send_preview.py customer_a openchat_001 "這是一段預演發送訊息"
```

Prepare a device for LINE validation:

```bash
python3 scripts/line_apk_status.py
python3 scripts/prepare_line_session.py emulator-5554 --boot-timeout 10
python3 scripts/ensure_device_ready.py emulator-5554 --wait-timeout 60
python3 scripts/openchat_validation.py --community-id openchat_001
```

If you have a LINE APK available locally, sideload it with:

```bash
python3 scripts/install_line_app.py emulator-5554 --apk-path /absolute/path/to/line.apk
python3 scripts/install_apk.py /absolute/path/to/line.apk --device-id emulator-5554
python3 scripts/open_app.py jp.naver.line.android --device-id emulator-5554
```

If you know the target Lark chat or user ID, you can send the current system status card with:

```bash
python3 scripts/lark_send_status_card.py oc_xxxxxxxxxx --receive-id-type chat_id
```
