"""Add a scheduled post for a community.

Two modes:
  - **Direct text** (default): you supply the fully-written post.
  - **Compose mode** (`--compose`): you supply a `--brief` topic and the
    system runs codex_compose at fire time (default 4h before send_at)
    against the community's voice_profile. Lark card lands for review.

Examples:

    # Direct text, one-off
    python3 scripts/add_scheduled_post.py customer_a openchat_001 \\
        --send-at "2026-04-28T20:00:00+08:00" \\
        --text "晚安各位，這週末有看到讓你眼睛一亮的拍攝作品嗎？歡迎丟上來分享。"

    # Direct text from file, weekly recurrence
    python3 scripts/add_scheduled_post.py customer_a openchat_001 \\
        --send-at "2026-05-04T20:00:00+08:00" \\
        --text-file ./drafts/sunday_night.txt \\
        --recurrence "weekly:mon@20:00" \\
        --notes "週日晚安，固定欄目"

    # LLM compose mode, brand voice, weekly
    python3 scripts/add_scheduled_post.py customer_a openchat_004 \\
        --send-at "2026-05-04T20:00:00+08:00" \\
        --compose --brief "靜坐入門引子：邀請大家分享睡前放鬆的小做法" \\
        --recurrence "weekly:mon@20:00" \\
        --compose-lead-hours 6
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401

from app.workflows.scheduled_post_recurrence import RecurrenceError, parse_recurrence_string
from app.workflows.scheduled_posts import add_scheduled_post


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("customer_id")
    parser.add_argument("community_id")
    parser.add_argument("--send-at", required=True, help="ISO 8601 with timezone, e.g. 2026-04-28T20:00:00+08:00")
    parser.add_argument("--text", default=None)
    parser.add_argument("--text-file", default=None, help="Read text body from this file (UTF-8).")
    parser.add_argument("--brief", default=None, help="LLM compose brief (used with --compose).")
    parser.add_argument("--compose", action="store_true", help="Run codex_compose at fire time instead of sending --text directly.")
    parser.add_argument(
        "--compose-lead-hours",
        type=float,
        default=None,
        help="Hours before send_at to run codex_compose (default 4h).",
    )
    parser.add_argument(
        "--recurrence",
        default=None,
        help="Recurrence spec: 'daily@HH:MM' / 'weekly:mon@HH:MM' / 'monthly:1@HH:MM' / 'once'",
    )
    parser.add_argument("--notes", default=None)
    parser.add_argument(
        "--pre-approved",
        action="store_true",
        help="Mark post as pre-approved by operator (still gated by global require_human_approval; ignored in compose mode — LLM drafts always go through review).",
    )
    args = parser.parse_args()

    if args.compose:
        if args.text or args.text_file:
            parser.error("--compose is mutually exclusive with --text / --text-file")
        if not args.brief or not args.brief.strip():
            parser.error("--compose requires --brief")
        text = None
    else:
        if args.text and args.text_file:
            parser.error("--text and --text-file are mutually exclusive")
        if args.text:
            text = args.text
        elif args.text_file:
            text = Path(args.text_file).expanduser().read_text(encoding="utf-8")
        else:
            parser.error("Provide --text, --text-file, or --compose with --brief")
            return 2
        if args.brief:
            parser.error("--brief is only valid with --compose")

    try:
        recurrence = parse_recurrence_string(args.recurrence) if args.recurrence else None
    except RecurrenceError as exc:
        parser.error(f"--recurrence: {exc}")
        return 2

    compose_lead_seconds = (
        int(args.compose_lead_hours * 3600) if args.compose_lead_hours is not None else None
    )

    record = add_scheduled_post(
        args.customer_id,
        args.community_id,
        args.send_at,
        text,
        pre_approved=args.pre_approved,
        notes=args.notes,
        brief=args.brief if args.compose else None,
        compose_mode=args.compose,
        compose_lead_seconds=compose_lead_seconds,
        recurrence=recurrence,
    )
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
