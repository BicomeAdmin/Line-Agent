from __future__ import annotations

import argparse
import json

import _bootstrap  # noqa: F401

from app.workflows.audit_status import get_audit_status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("customer_id")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    print(json.dumps(get_audit_status(args.customer_id, limit=args.limit), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

