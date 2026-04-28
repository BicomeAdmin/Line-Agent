"""List scheduled posts for a community (or all communities)."""

from __future__ import annotations

import argparse
import json

import _bootstrap  # noqa: F401

from app.workflows.scheduled_posts import list_all_scheduled_posts, list_scheduled_posts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--customer-id", default=None)
    parser.add_argument("--community-id", default=None)
    parser.add_argument(
        "--status",
        action="append",
        default=None,
        help="Filter by status (repeatable). Default: all statuses.",
    )
    args = parser.parse_args()

    statuses = set(args.status) if args.status else None
    if args.customer_id and args.community_id:
        items = list_scheduled_posts(args.customer_id, args.community_id, statuses=statuses)
    else:
        items = list_all_scheduled_posts(statuses=statuses)

    print(
        json.dumps(
            {"status": "ok", "count": len(items), "items": items},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
