# Project Echo Folder Structure Plan

## Design Goals

The repository should make customer isolation, emulator control, Lark interaction, and AI decisioning explicit. The structure below separates platform integrations from business workflows so the system can evolve without mixing customer data, prompts, and automation code.

## Recommended Structure

```text
project-echo/
  README.md
  pyproject.toml
  .env.example

  app/
    main.py
    config.py
    logging_config.py

    api/
      __init__.py
      lark_events.py
      lark_actions.py
      health.py

    core/
      __init__.py
      security.py
      idempotency.py
      rate_limits.py
      time_windows.py
      audit.py

    workflows/
      __init__.py
      read_chat.py
      draft_reply.py
      send_reply.py
      patrol.py

    adb/
      __init__.py
      client.py
      devices.py
      line_app.py
      input.py
      uiautomator.py
      recovery.py

    parsing/
      __init__.py
      xml_cleaner.py
      line_chat_parser.py
      filters.py

    ai/
      __init__.py
      gemini_client.py
      prompt_builder.py
      decision_schema.py
      safety_rules.py

    lark/
      __init__.py
      client.py
      cards.py
      commands.py
      verification.py

    scheduler/
      __init__.py
      dispatcher.py
      locks.py
      jobs.py

    storage/
      __init__.py
      paths.py
      repositories.py
      snapshots.py

  customers/
    customer_a/
      customer.yaml
      souls/
        default.md
        investment.md
      communities/
        openchat_001.yaml
        openchat_002.yaml
      data/
        raw_xml/
        cleaned_messages/
        prompts/
        llm_outputs/
        send_logs/

    customer_b/
      customer.yaml
      souls/
      communities/
      data/

  configs/
    devices.yaml
    scheduler.yaml
    risk_control.yaml
    lark_commands.yaml

  scripts/
    adb_probe.py
    dump_ui_xml.py
    parse_xml_sample.py
    send_test_message.py
    run_patrol_once.py

  tests/
    unit/
      test_xml_cleaner.py
      test_line_chat_parser.py
      test_rate_limits.py
      test_time_windows.py
      test_prompt_builder.py
    integration/
      test_lark_url_verification.py
      test_workflow_read_chat.py
      test_workflow_send_reply.py

  samples/
    xml/
      line_chat_dump.sample.xml
    messages/
      cleaned_messages.sample.json
    lark/
      event_url_verification.sample.json
      card_action.sample.json

  docs/
    prd.md
    technical-feasibility-report.md
    folder-structure-plan.md
    runbook.md
```

## Key Directory Responsibilities

### `app/api/`

FastAPI route handlers.

Expected files:

- `lark_events.py`: receives Lark event callbacks, including `url_verification`.
- `lark_actions.py`: receives interactive card button actions.
- `health.py`: exposes local health checks for tunnel and process monitoring.

Rule:

- API handlers should acknowledge quickly and dispatch slow work to background jobs.

### `app/adb/`

All Android and LINE automation code.

Expected files:

- `client.py`: thin wrapper around `adb` commands.
- `devices.py`: discovers and resolves emulator serial IDs.
- `line_app.py`: opens LINE and verifies current package/activity.
- `input.py`: human-like tap, type, paste, and send behavior.
- `uiautomator.py`: dumps XML and pulls it from device.
- `recovery.py`: handles lost windows, popups, app relaunch, and navigation repair.

Rule:

- Other modules should not call raw `adb` subprocesses directly.

### `app/parsing/`

XML cleanup and chat extraction.

Expected files:

- `xml_cleaner.py`: removes status bar, navigation, battery, time, and irrelevant system nodes.
- `line_chat_parser.py`: converts cleaned XML into message objects.
- `filters.py`: shared regex and selector rules.

Rule:

- The parser should return structured data, not prompt-ready text.

Example output:

```json
[
  {
    "sender": "unknown",
    "text": "請問新手媽媽奶瓶怎麼選？",
    "position": 8,
    "source": "uiautomator"
  }
]
```

### `app/ai/`

LLM client, prompt construction, schemas, and safety logic.

Expected files:

- `gemini_client.py`: provider-specific API calls.
- `prompt_builder.py`: builds prompts from customer-scoped data.
- `decision_schema.py`: validates model JSON output.
- `safety_rules.py`: blocks risky categories or unsupported claims.

Rule:

- Prompt builders must receive an explicit `customer_id`.
- Prompt builders must never scan all customer folders.

### `app/lark/`

Lark-specific client logic and message formatting.

Expected files:

- `client.py`: sends proactive messages and API requests.
- `cards.py`: builds review cards.
- `commands.py`: parses operator commands.
- `verification.py`: validates signatures and URL verification payloads.

Rule:

- Lark cards should include job ID, customer ID, community ID, draft, risk flags, and action buttons.

### `app/scheduler/`

Queueing, locks, and patrol scheduling.

Expected files:

- `dispatcher.py`: decides which job runs next.
- `locks.py`: prevents concurrent use of the same account, device, or fixed IP send channel.
- `jobs.py`: job models and status transitions.

Rule:

- Under fixed-IP mode, sending should pass through a serialized global send queue.

### `customers/`

Customer-specific configuration and private data.

Expected files:

- `customer.yaml`: customer metadata, allowed operators, default persona.
- `souls/*.md`: persona files.
- `communities/*.yaml`: OpenChat metadata and scheduling settings.
- `data/raw_xml/`: original UI dumps.
- `data/cleaned_messages/`: parsed message JSON.
- `data/prompts/`: exact prompts sent to LLM.
- `data/llm_outputs/`: exact model responses.
- `data/send_logs/`: send attempts and results.

Rule:

- All private customer data stays under its customer root.
- Shared code can reference customer data only through `app/storage/paths.py`.

## Suggested Config Files

### `configs/devices.yaml`

```yaml
devices:
  - device_id: emulator-5554
    label: line-account-01
    customer_id: customer_a
    enabled: true
```

### `configs/risk_control.yaml`

```yaml
fixed_ip_mode: true
activity_window:
  start: "09:00"
  end: "23:00"
send_delay_seconds:
  min: 5
  max: 30
account_cooldown_seconds: 900
community_cooldown_seconds: 1800
require_human_approval: true
```

### `customers/customer_a/communities/openchat_001.yaml`

```yaml
community_id: openchat_001
display_name: "客戶 A - 投資群"
persona: investment
device_id: emulator-5554
patrol_interval_minutes: 120
enabled: true
```

## Minimum First Sprint File Set

For Phase 1 and Phase 2, create only this subset first:

```text
app/
  main.py
  config.py
  api/lark_events.py
  adb/client.py
  adb/uiautomator.py
  adb/input.py
  adb/line_app.py
  parsing/xml_cleaner.py
  parsing/line_chat_parser.py
  lark/client.py
  lark/cards.py
  workflows/read_chat.py
  workflows/send_reply.py
configs/
  devices.yaml
  risk_control.yaml
customers/
  customer_a/
    customer.yaml
    souls/default.md
    communities/openchat_001.yaml
scripts/
  adb_probe.py
  dump_ui_xml.py
  send_test_message.py
tests/
  unit/test_xml_cleaner.py
  unit/test_line_chat_parser.py
```

## Naming Conventions

- Customer IDs: `customer_a`, `customer_b`, or stable internal IDs.
- Community IDs: `openchat_001`, `openchat_002`.
- Device IDs: exact ADB serial where possible, such as `emulator-5554`.
- Job IDs: timestamp plus short random suffix, such as `20260427-143011-a82f`.
- Persona files: lowercase names, such as `default.md`, `investment.md`, `maternity.md`.

## Data Isolation Rules

Required:

- Every workflow accepts `customer_id`.
- Every storage path is resolved from `customer_id`.
- AI context loading must be customer-scoped.
- Test fixtures should include two customers to verify no cross-customer leakage.

Forbidden:

- Global `latest_messages.txt`.
- Shared prompt scratch files.
- Parser output that omits customer and community identity.
- Loading all `Soul.md` files and asking the LLM to choose.

## Recommended Next Build Step

Start with the minimum first sprint file set. Implement `adb_probe.py`, `dump_ui_xml.py`, and `line_chat_parser.py` before building the full Lark review flow. The fastest technical proof is confirming that one emulator can produce clean, useful OpenChat text through `uiautomator`.
