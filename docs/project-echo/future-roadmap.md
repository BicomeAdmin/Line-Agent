# Project Echo Future Roadmap

Last updated: 2026-04-27

## Stage 1: Unblock The Real LINE Chain

Target outcome:

- real LINE APK available
- LINE installed in `emulator-5554`
- manual login completed

Tasks:

1. provide APK in `~/Downloads/line.apk` or configure `ECHO_LINE_APK_PATH`
2. run install workflow
3. confirm `line_installed == true`
4. confirm LINE can be launched repeatedly

Operational command:

- `python3 scripts/action_queue.py --community-id openchat_001`
- `python3 scripts/milestone_status.py --community-id openchat_001`

## Stage 2: Reach Target OpenChat

Target outcome:

- `openchat_validation.py --community-id openchat_001` returns `ok`

Tasks:

1. open the correct OpenChat manually after login
2. verify title matching against the real UI
3. confirm acceptance moves past `line_missing`

## Stage 3: Readback And Calibration

Target outcome:

- recent chat is readable
- send preview is fully configured

Tasks:

1. run live `read_chat`
2. save first real coordinates
3. confirm `send_preview.py` returns `ok`

## Stage 4: Human-In-The-Loop Demo

Target outcome:

- first real read -> draft -> review -> send cycle

Tasks:

1. read real OpenChat messages
2. generate review draft
3. simulate operator approval
4. execute a real send

## Stage 5: Operational Hardening

Target outcome:

- repeated patrol confidence
- real Lark callback confidence

Tasks:

1. repeated cold-start recovery validation
2. real Lark webhook and proactive push validation
3. recurring patrol checks
4. expand incident docs
