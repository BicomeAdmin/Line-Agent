from __future__ import annotations

import argparse
import json

import _bootstrap  # noqa: F401

from app.workflows.draft_reply import draft_reply_for_device


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("device_id")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    result = draft_reply_for_device(args.device_id, limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

