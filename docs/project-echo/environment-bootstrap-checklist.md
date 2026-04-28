# Project Echo Environment Bootstrap Checklist

Last updated: 2026-04-27

## Goal

Bring a fresh machine or fresh clone to the point where Project Echo diagnostics can run.

## Workspace

- repository path confirmed
- Python 3 available
- `.env` present for local secrets

## Required Local Capabilities

### 1. ADB

Verify:

```bash
python3 scripts/adb_probe.py
```

If needed:

- install Android platform-tools
- or set `ECHO_ADB_PATH`

### 2. Emulator

Verify:

```bash
python3 scripts/start_emulator.py --avd project-echo-api35 --no-snapshot
python3 scripts/wait_for_device.py emulator-5554 --timeout 120
python3 scripts/device_status.py emulator-5554
```

### 3. Lark Credentials

Verify:

```bash
python3 scripts/lark_auth_check.py
```

Expected local config:

- `LARK_APP_ID`
- `LARK_APP_SECRET`
- `LARK_VERIFICATION_TOKEN`

### 4. LINE APK Availability

Verify:

```bash
python3 scripts/line_apk_status.py
```

Expected:

- APK placed at `~/Downloads/line.apk`
- or `ECHO_LINE_APK_PATH` configured

## First Project Health Pass

Run:

```bash
python3 -m unittest discover -s tests
python3 scripts/project_snapshot.py --community-id openchat_001
python3 scripts/readiness_status.py
```

## Not Included In Bootstrap

These still require human action:

- LINE login
- navigating to target OpenChat
- first real send-coordinate calibration

