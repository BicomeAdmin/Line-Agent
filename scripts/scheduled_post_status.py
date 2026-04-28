from __future__ import annotations

import argparse
import json

import _bootstrap  # noqa: F401

from app.workflows.scheduled_post_status import get_scheduled_post_status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--customer-id", default=None)
    parser.add_argument("--community-id", default=None)
    args = parser.parse_args()

    print(
        json.dumps(
            get_scheduled_post_status(args.customer_id, args.community_id),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
