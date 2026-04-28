from __future__ import annotations

import argparse
import json

import _bootstrap  # noqa: F401

from app.workflows.onboarding_timeline import get_onboarding_timeline


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--customer-id", default=None)
    parser.add_argument("--community-id", default=None)
    args = parser.parse_args()
    print(
        json.dumps(
            get_onboarding_timeline(customer_id=args.customer_id, community_id=args.community_id),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
