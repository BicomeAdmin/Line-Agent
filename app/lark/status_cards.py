from __future__ import annotations


def build_system_status_card(payload: dict[str, object]) -> dict[str, object]:
    device_lines = []
    for device in payload.get("devices", []):
        if not isinstance(device, dict):
            continue
        status_icon = "OK" if device.get("boot_completed") else "WAIT"
        line_state = "LINE_ACTIVE" if device.get("line_active") else "LINE_IDLE"
        device_lines.append(
            f"- `{device.get('device_id')}` {status_icon} {line_state} {device.get('foreground_package') or 'UNKNOWN'}"
        )

    summary = "\n".join(device_lines) or "- No devices configured"
    activity = payload.get("activity_window", {})
    start = activity.get("start", "09:00")
    end = activity.get("end", "23:00")
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "Project Echo Status"}},
        "elements": [
            {"tag": "markdown", "content": f"**Activity Window**\n`{start}` - `{end}`"},
            {"tag": "markdown", "content": f"**Devices**\n{summary}"},
        ],
    }


def build_readiness_status_card(payload: dict[str, object]) -> dict[str, object]:
    summary = payload.get("summary", {})
    ready = bool(summary.get("ready"))
    blocker_count = summary.get("blocker_count", 0)
    warning_count = summary.get("warning_count", 0)
    device_count = summary.get("device_count", 0)
    community_count = summary.get("community_count", 0)

    blocker_lines = _collect_check_messages(payload, severity="blocker", limit=6)
    warning_lines = _collect_check_messages(payload, severity="warning", limit=6)
    next_actions = payload.get("next_actions", [])
    action_lines = [f"- {item}" for item in next_actions[:5] if isinstance(item, str)]

    overview = [
        f"- `ready`: `{'yes' if ready else 'no'}`",
        f"- `blockers`: `{blocker_count}`",
        f"- `warnings`: `{warning_count}`",
        f"- `devices`: `{device_count}`",
        f"- `communities`: `{community_count}`",
    ]
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "Project Echo Readiness"}},
        "elements": [
            {"tag": "markdown", "content": "**Overview**\n" + "\n".join(overview)},
            {"tag": "markdown", "content": "**Blockers**\n" + ("\n".join(blocker_lines) if blocker_lines else "- None")},
            {"tag": "markdown", "content": "**Warnings**\n" + ("\n".join(warning_lines) if warning_lines else "- None")},
            {"tag": "markdown", "content": "**Next Actions**\n" + ("\n".join(action_lines) if action_lines else "- None")},
        ],
    }


def _collect_check_messages(payload: dict[str, object], severity: str, limit: int) -> list[str]:
    messages: list[str] = []
    for section in ("global_checks", "devices", "communities"):
        entries = payload.get(section, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if section == "global_checks" and isinstance(entry, dict):
                if entry.get("severity") == severity and isinstance(entry.get("message"), str):
                    messages.append(f"- {entry['message']}")
            elif isinstance(entry, dict):
                checks = entry.get("checks", [])
                if not isinstance(checks, list):
                    continue
                label = entry.get("device_id") or entry.get("community_name") or entry.get("community_id") or "unknown"
                for check in checks:
                    if not isinstance(check, dict):
                        continue
                    if check.get("severity") == severity and isinstance(check.get("message"), str):
                        messages.append(f"- `{label}` {check['message']}")
            if len(messages) >= limit:
                return messages[:limit]
    return messages[:limit]
