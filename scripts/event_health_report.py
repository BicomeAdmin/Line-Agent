from __future__ import annotations

import argparse
import json

import _bootstrap  # noqa: F401

from app.workflows.event_health_report import (
    collect_digest_health,
    collect_watcher_health,
    render_text_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Health check for the day's two ignition events (09:00 digest, 10:00 watcher).")
    parser.add_argument("--customer-id", default="customer_a")
    parser.add_argument("--target-hour", type=int, default=9, help="TPE hour the daily digest fires at (default 9)")
    parser.add_argument("--scope", choices=["digest", "watcher", "all"], default="all")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    digest = collect_digest_health(args.customer_id, target_hour=args.target_hour) if args.scope in ("digest", "all") else None
    watcher = collect_watcher_health(args.customer_id) if args.scope in ("watcher", "all") else None

    if args.json:
        payload = {}
        if digest:
            payload["digest"] = {
                "target_hour": digest.target_hour,
                "today_str": digest.today_str,
                "marker_present": digest.marker_present,
                "marker_value": digest.marker_value,
                "sent_today": digest.sent_today,
                "log_push_count": len(digest.log_push_lines),
                "log_error_count": len(digest.log_error_lines),
                "rendered_char_count": digest.rendered_char_count,
                "sections_present": digest.sections_present,
            }
        if watcher:
            payload["watcher"] = {
                "active_watches": len(watcher.watches_active),
                "watch_tick_2h": len(watcher.recent_tick_events),
                "compose_review_2h": len(watcher.recent_compose_reviews),
                "review_card_pushed_2h": len(watcher.recent_review_cards),
            }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(render_text_report(digest, watcher))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
