# Project Echo Incident Recovery Runbook

Last updated: 2026-04-27

## Purpose

Use this document when the system is not merely blocked by missing setup, but appears unhealthy or inconsistent.

## Incident Types

### 1. Emulator Not Ready

Symptoms:

- `boot_completed == false`
- `device_status` cannot be fetched
- `prepare_line_session` returns `boot_not_completed`

Response:

```bash
python3 scripts/ensure_device_ready.py emulator-5554 --wait-timeout 60
python3 scripts/device_status.py emulator-5554
```

If unresolved:

```bash
python3 scripts/start_emulator.py --avd project-echo-api35 --no-snapshot
python3 scripts/wait_for_device.py emulator-5554 --timeout 120
```

### 2. LINE Installation Blocked

Symptoms:

- `install_line_app.py` returns `apk_not_found`
- readiness shows `line_apk_available = false`

Response:

```bash
python3 scripts/line_apk_status.py
```

Then either:

- place APK at `~/Downloads/line.apk`
- or set `ECHO_LINE_APK_PATH`

### 3. LINE Not In Target OpenChat

Symptoms:

- `openchat_validation.py` returns `line_not_foreground`
- `acceptance_status.py` returns `line_not_openchat`

Response:

```bash
python3 scripts/prepare_line_session.py emulator-5554 --boot-timeout 10
python3 scripts/openchat_validation.py --community-id openchat_001
```

If still blocked, manually navigate LINE to the target OpenChat and rerun validation.

### 4. Lark Callback Or Push Failure

Symptoms:

- webhook validation fails
- proactive send fails
- `lark_auth_check.py` fails

Response:

```bash
python3 scripts/lark_auth_check.py
python3 scripts/simulate_lark_event.py "請做部署檢查" --wait-seconds 8
```

Verify:

- `LARK_APP_ID`
- `LARK_APP_SECRET`
- `LARK_VERIFICATION_TOKEN`

## Escalation Rule

Escalate to human help when the blocker requires:

- APK sourcing
- manual LINE login
- manual OpenChat navigation
- credential recovery from Lark admin

## After Recovery

Always rerun:

```bash
python3 scripts/project_snapshot.py --community-id openchat_001
python3 scripts/acceptance_status.py --community-id openchat_001
python3 scripts/onboarding_timeline.py --community-id openchat_001
```

