from __future__ import annotations

import argparse
import json

import _bootstrap  # noqa: F401

from app.workflows.openchat_validation import validate_openchat_session


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate whether LINE is currently open to the target OpenChat.")
    parser.add_argument("--customer-id", default=None)
    parser.add_argument("--community-id", default=None)
    args = parser.parse_args()

    result = validate_openchat_session(customer_id=args.customer_id, community_id=args.community_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
