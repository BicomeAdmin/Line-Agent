from __future__ import annotations

import argparse
import json

import _bootstrap  # noqa: F401

from app.workflows.action_queue import get_action_queue


def main() -> int:
    parser = argparse.ArgumentParser(description="Print prioritized next actions for Project Echo.")
    parser.add_argument("--customer-id", default=None)
    parser.add_argument("--community-id", default=None)
    args = parser.parse_args()

    print(json.dumps(get_action_queue(customer_id=args.customer_id, community_id=args.community_id), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
