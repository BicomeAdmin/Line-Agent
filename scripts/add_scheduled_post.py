"""Add a scheduled post for a community.

Examples:
    python3 scripts/add_scheduled_post.py customer_a openchat_001 \\
        --send-at "2026-04-28T20:00:00+08:00" \\
        --text "晚安各位，這週末有看到讓你眼睛一亮的拍攝作品嗎？歡迎丟上來分享。"

    python3 scripts/add_scheduled_post.py customer_a openchat_001 \\
        --send-at "2026-04-28T20:00:00+08:00" \\
        --text-file ./drafts/sunday_night.txt \\
        --notes "週日晚安，固定欄目"
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401

from app.workflows.scheduled_posts import add_scheduled_post


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("customer_id")
    parser.add_argument("community_id")
    parser.add_argument("--send-at", required=True, help="ISO 8601 with timezone, e.g. 2026-04-28T20:00:00+08:00")
    parser.add_argument("--text", default=None)
    parser.add_argument("--text-file", default=None, help="Read text body from this file (UTF-8).")
    parser.add_argument("--notes", default=None)
    parser.add_argument(
        "--pre-approved",
        action="store_true",
        help="Mark post as pre-approved by operator (still gated by global require_human_approval).",
    )
    args = parser.parse_args()

    if args.text and args.text_file:
        parser.error("--text and --text-file are mutually exclusive")
    if args.text:
        text = args.text
    elif args.text_file:
        text = Path(args.text_file).expanduser().read_text(encoding="utf-8")
    else:
        parser.error("Provide --text or --text-file")
        return 2

    record = add_scheduled_post(
        args.customer_id,
        args.community_id,
        args.send_at,
        text,
        pre_approved=args.pre_approved,
        notes=args.notes,
    )
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
