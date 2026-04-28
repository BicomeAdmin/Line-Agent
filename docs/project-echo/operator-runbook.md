# Project Echo Operator Runbook

## Purpose

This runbook is the first-stop guide for checking Project Echo status without rereading code.

## 1. Quick Health Checks

Run these first:

```bash
python3 scripts/dashboard_status.py
python3 scripts/readiness_status.py
python3 scripts/community_status.py
python3 scripts/acceptance_status.py --community-id openchat_001
python3 scripts/onboarding_timeline.py --community-id openchat_001
python3 scripts/line_apk_status.py
python3 scripts/openchat_validation.py --community-id openchat_001
```

What they tell you:

- `dashboard_status.py`: overall ops picture
- `readiness_status.py`: blocker / warning checklist
- `community_status.py`: per-community operational readiness
- `acceptance_status.py`: whether a community is ready for human-in-the-loop demo
- `onboarding_timeline.py`: what has already happened and in what order
- `line_apk_status.py`: whether a usable LINE APK is already on this machine
- `openchat_validation.py`: whether LINE is really sitting on the target OpenChat

## 2. If Emulator Looks Unhealthy

Check device state:

```bash
python3 scripts/device_status.py emulator-5554
python3 scripts/current_app.py emulator-5554
python3 scripts/check_line_app.py emulator-5554
```

If boot looks incomplete:

```bash
python3 scripts/wait_for_device.py emulator-5554 --timeout 120
```

If the emulator is not running:

```bash
python3 scripts/start_emulator.py --avd project-echo-api35 --no-snapshot
python3 scripts/ensure_device_ready.py emulator-5554 --wait-timeout 60
```

## 3. If LINE Is Not Ready

Try the standard preparation flow:

```bash
python3 scripts/line_apk_status.py
python3 scripts/ensure_device_ready.py emulator-5554 --wait-timeout 60
python3 scripts/prepare_line_session.py emulator-5554 --boot-timeout 10
python3 scripts/openchat_validation.py --community-id openchat_001
```

If LINE is not installed yet:

```bash
python3 scripts/line_apk_status.py
python3 scripts/install_line_app.py emulator-5554 --apk-path /absolute/path/to/line.apk
python3 scripts/install_apk.py /absolute/path/to/line.apk --device-id emulator-5554
python3 scripts/open_app.py jp.naver.line.android --device-id emulator-5554
```

Then rerun:

```bash
python3 scripts/openchat_validation.py --community-id openchat_001
python3 scripts/acceptance_status.py --community-id openchat_001
```

## 4. If Send Coordinates Are Missing

Check calibration state:

```bash
python3 scripts/calibration_status.py
python3 scripts/send_preview.py customer_a openchat_001 "這是一段預演發送訊息"
```

Dry-run the send pattern:

```bash
python3 scripts/send_test_message.py "測試訊息" \
  --device-id emulator-5554 \
  --input-x 100 --input-y 1800 \
  --send-x 1000 --send-y 1800 \
  --dry-run
```

Save calibration once confirmed:

```bash
python3 scripts/set_community_calibration.py customer_a openchat_001 \
  --input-x 100 --input-y 1800 \
  --send-x 1000 --send-y 1800 \
  --note "first calibration pass"
```

## 5. If Lark Feels Broken

First test local simulated command flow:

```bash
python3 scripts/simulate_lark_event.py "請做部署檢查" --wait-seconds 8
python3 scripts/simulate_lark_event.py "請回報 LINE APK 狀態" --wait-seconds 8
python3 scripts/simulate_lark_event.py "請回報 openchat_001 社群狀態" --wait-seconds 6
python3 scripts/simulate_lark_event.py "請幫我做 openchat_001 驗收檢查" --wait-seconds 8
python3 scripts/simulate_lark_event.py "請幫我做 openchat_001 OpenChat 驗證" --wait-seconds 8
```

Then validate credentials:

```bash
python3 scripts/lark_auth_check.py
```

If auth fails, check:

- `LARK_APP_ID`
- `LARK_APP_SECRET`
- `LARK_VERIFICATION_TOKEN`

## 6. Target State For First Real Demo

`acceptance_status.py --community-id openchat_001` should eventually show:

- `ready: true`
- `stage: ready_for_hil`

To reach that state:

1. emulator boot completes
2. LINE is installed
3. `openchat_validation.py --community-id openchat_001` returns `ok`
4. recent chat can be read
5. send preview returns `ok`

## 7. Files To Check Before Starting New Work

- `docs/project-echo/implementation-status.md`
- `docs/project-echo/workstream-tracker.md`
- `docs/project-echo/change-log.md`
- `docs/project-echo/operator-runbook.md`
