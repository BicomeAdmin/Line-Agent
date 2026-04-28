from __future__ import annotations

import argparse
import json

import _bootstrap  # noqa: F401

from app.workflows.community_status import get_community_status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--customer-id", default=None)
    parser.add_argument("--community-id", default=None)
    args = parser.parse_args()
    print(
        json.dumps(
            get_community_status(customer_id=args.customer_id, community_id=args.community_id),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
