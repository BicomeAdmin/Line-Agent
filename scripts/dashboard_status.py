from __future__ import annotations

import argparse
import json

import _bootstrap  # noqa: F401

from app.workflows.dashboard_status import get_dashboard_status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-limit-per-customer", type=int, default=20)
    args = parser.parse_args()

    print(json.dumps(get_dashboard_status(audit_limit_per_customer=args.audit_limit_per_customer), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

