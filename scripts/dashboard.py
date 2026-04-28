"""Operator dashboard CLI.

Usage:
    python3 scripts/dashboard.py              # one-shot snapshot
    python3 scripts/dashboard.py --watch      # refresh every 5s
    python3 scripts/dashboard.py --json       # raw structured data
    python3 scripts/dashboard.py --compact    # skip recent_audit section
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import _bootstrap  # noqa: F401

from app.workflows.dashboard import collect_dashboard_data, format_text_report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--customer-id", default="customer_a")
    parser.add_argument("--watch", action="store_true", help="Refresh every 5 seconds.")
    parser.add_argument("--interval", type=int, default=5, help="Watch refresh interval seconds.")
    parser.add_argument("--json", action="store_true", help="Print raw JSON instead of pretty text.")
    parser.add_argument("--compact", action="store_true", help="Skip recent_audit section.")
    args = parser.parse_args()

    if args.watch and args.json:
        print("error: --watch and --json don't make sense together", file=sys.stderr)
        return 2

    if not args.watch:
        data = collect_dashboard_data(args.customer_id)
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(format_text_report(data, compact=args.compact))
        return 0

    try:
        while True:
            os.system("clear" if os.name != "nt" else "cls")
            data = collect_dashboard_data(args.customer_id)
            print(format_text_report(data, compact=args.compact))
            print(f"\n(每 {args.interval}s 刷新；Ctrl-C 結束)")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
