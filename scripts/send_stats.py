"""Pretty-print send-pipeline stats from the terminal.

Usage:
    python3 scripts/send_stats.py                      # last 24h, all communities
    python3 scripts/send_stats.py --hours 168          # last week
    python3 scripts/send_stats.py --community openchat_002
    python3 scripts/send_stats.py --json               # raw json
"""

from __future__ import annotations

import argparse
import json

import _bootstrap  # noqa: F401

from app.workflows.send_metrics import get_send_metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--customer-id", default="customer_a")
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--community", default=None, help="Optional filter (community_id).")
    parser.add_argument("--json", action="store_true", help="Print raw JSON instead of pretty.")
    args = parser.parse_args()

    metrics = get_send_metrics(args.customer_id, since_hours=args.hours, community_id=args.community)

    if args.json:
        print(json.dumps(metrics, ensure_ascii=False, indent=2))
        return 0

    window = metrics.get("window") or {}
    totals = metrics.get("totals") or {}
    by_community = metrics.get("by_community") or {}
    auto_fires = metrics.get("auto_fires") or []

    print(f"\n📊 Send pipeline — last {args.hours:g}h (Asia/Taipei)")
    print(f"   {window.get('since_taipei')} → {window.get('until_taipei')}")
    print()

    print("─── totals ───")
    print(f"  drafts created : {totals.get('drafts_created', 0)}")
    print(f"  sent           : {totals.get('sent', 0)}")
    print(f"  ignored        : {totals.get('ignored', 0)}")
    print(f"  review pending : {totals.get('review_pending', 0)}")
    src = totals.get("by_source") or {}
    if src:
        breakdown = "  ".join(f"{k}={v}" for k, v in src.items())
        print(f"  by source      : {breakdown}")
    print()

    if by_community:
        print("─── by community ───")
        for cid, bucket in sorted(by_community.items()):
            print(f"\n  ⌜ {cid} — {bucket.get('community_name')}")
            print(f"  │   drafts: {bucket.get('drafts_created')}, "
                  f"sent: {bucket.get('sent')}, "
                  f"ignored: {bucket.get('ignored')}, "
                  f"pending: {bucket.get('review_pending')}")
            src = bucket.get("by_source") or {}
            if src:
                print(f"  │   sources: {', '.join(f'{k}={v}' for k, v in src.items())}")
            avg = bucket.get("avg_compose_to_send_seconds")
            n = bucket.get("compose_to_send_count")
            if avg is not None:
                print(f"  │   avg compose→send: {avg:.1f}s  (n={n})")
            attempts = bucket.get("send_attempts") or []
            if attempts:
                print(f"  │   recent attempts:")
                for a in attempts[-5:]:
                    delay = a.get("delay_seconds")
                    delay_str = f" delay={delay:.1f}s" if isinstance(delay, (int, float)) else ""
                    print(f"  │     {a.get('ts_taipei')}  {a.get('status'):<8}{delay_str}")
            print(f"  ⌞")

    if auto_fires:
        print("\n─── recent auto-fires ───")
        for fire in auto_fires[-10:]:
            related = fire.get("related_review") or {}
            review_summary = ""
            if related:
                review_summary = f"  → review {related.get('review_id')} ({related.get('current_status')})"
            print(f"  {fire.get('fired_at_taipei')}  {fire.get('community_name')}{review_summary}")
            summary = (fire.get('codex_summary') or '')[:140]
            if summary:
                print(f"     {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
