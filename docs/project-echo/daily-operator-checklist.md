# Project Echo Daily Operator Checklist

Last updated: 2026-04-27

## Start Of Shift

Run these in order:

```bash
python3 scripts/project_snapshot.py --community-id openchat_001
python3 scripts/readiness_status.py
python3 scripts/line_apk_status.py
python3 scripts/acceptance_status.py --community-id openchat_001
python3 scripts/community_status.py --community-id openchat_001
```

Confirm:

- emulator is booted
- LINE APK availability is known
- current acceptance stage is understood
- OpenChat status is not surprising

## If Emulator Is Down

Run:

```bash
python3 scripts/ensure_device_ready.py emulator-5554 --wait-timeout 60
python3 scripts/device_status.py emulator-5554
```

If still blocked, use the incident recovery runbook.

## If LINE Is Missing

Run:

```bash
python3 scripts/line_apk_status.py
python3 scripts/install_line_app.py emulator-5554 --apk-path /absolute/path/to/line.apk
```

If no APK is available locally, pause here and get the APK in place first.

## If LINE Is Installed But Not In OpenChat

Run:

```bash
python3 scripts/prepare_line_session.py emulator-5554 --boot-timeout 10
python3 scripts/openchat_validation.py --community-id openchat_001
python3 scripts/acceptance_status.py --community-id openchat_001
```

If validation still says `line_not_foreground`, open LINE manually and navigate to the target OpenChat before retrying.

## If Coordinates Are Missing

Run:

```bash
python3 scripts/calibration_status.py
python3 scripts/send_preview.py customer_a openchat_001 "這是一段預演發送訊息"
```

If still blocked, perform the first calibration pass and save it.

## Before Ending Shift

Run:

```bash
python3 scripts/project_snapshot.py --community-id openchat_001
python3 scripts/onboarding_timeline.py --community-id openchat_001
```

Make sure the latest state is reflected in:

- `implementation-status.md`
- `workstream-tracker.md`
- `ai-collaboration-handoff.md`

