from __future__ import annotations

import argparse
import json

import _bootstrap  # noqa: F401

from app.workflows.project_snapshot import get_project_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="Print a machine-friendly Project Echo snapshot for handoff and collaboration.")
    parser.add_argument("--customer-id", default=None)
    parser.add_argument("--community-id", default=None)
    args = parser.parse_args()

    print(json.dumps(get_project_snapshot(customer_id=args.customer_id, community_id=args.community_id), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
