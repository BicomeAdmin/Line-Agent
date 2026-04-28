"""List reviews currently waiting on operator decision.

Surfaces what the system has drafted but not yet sent / ignored. Used as the
'inbox' when no Lark webhook is wired up.
"""

from __future__ import annotations

import argparse
import json

import _bootstrap  # noqa: F401

from app.core.reviews import ACTIVE_REVIEW_STATUSES, review_store


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", action="append", default=None,
                        help="Filter by status. Default: all active states (pending, edit_required, pending_reapproval).")
    parser.add_argument("--customer-id", default=None)
    parser.add_argument("--community-id", default=None)
    args = parser.parse_args()

    statuses = set(args.status) if args.status else set(ACTIVE_REVIEW_STATUSES)
    items = []
    for record in review_store.list_all():
        if record.status not in statuses:
            continue
        if args.customer_id and record.customer_id != args.customer_id:
            continue
        if args.community_id and record.community_id != args.community_id:
            continue
        items.append(record.to_dict())

    items.sort(key=lambda r: r.get("created_at", 0))

    print(json.dumps(
        {"status": "ok", "count": len(items), "items": items},
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
