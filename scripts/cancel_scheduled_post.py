"""Cancel a scheduled post by post_id."""

from __future__ import annotations

import argparse
import json

import _bootstrap  # noqa: F401

from app.workflows.scheduled_posts import cancel_scheduled_post


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("customer_id")
    parser.add_argument("community_id")
    parser.add_argument("post_id")
    parser.add_argument("--reason", default="operator_cancelled")
    args = parser.parse_args()

    updated = cancel_scheduled_post(
        args.customer_id, args.community_id, args.post_id, reason=args.reason
    )
    if updated is None:
        print(json.dumps({"status": "error", "reason": "post_not_found"}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"status": "ok", "post": updated}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
