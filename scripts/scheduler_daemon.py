"""Long-running scheduler daemon.

Polls `enqueue_due_patrols` on a loop, dispatches to the in-process job worker,
and prints one-line status per cycle. Designed to run in the background while
LINE / emulator are live, so AI can produce drafts on its own pacing.

Stop with Ctrl-C / SIGTERM. Quiet by default; pass --verbose for full JSON.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time

import os

import _bootstrap  # noqa: F401  (must precede app.* imports — adds project root to sys.path)

from app.core.timezone import taipei_now_str
from app.workflows.job_runner import ensure_job_worker
from app.workflows.scheduler import enqueue_due_patrols, enqueue_due_scheduled_posts, tick_watches
from app.workflows.dashboard import (
    aging_alert_marker_path,
    aging_review_alert_threshold_hours,
    collect_dashboard_data,
    format_text_report,
    mark_aging_alert_sent,
    mark_daily_digest_sent,
    should_alert_aging_review,
    should_send_daily_digest,
)


_stopping = False


def _request_stop(signum: int, frame: object) -> None:  # noqa: ARG001
    global _stopping
    _stopping = True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval-seconds", type=int, default=60, help="How often to call enqueue_due_patrols.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    ensure_job_worker()
    print(f"[scheduler] starting, interval={args.interval_seconds}s", flush=True)

    cycles = 0
    while not _stopping:
        cycles += 1
        try:
            patrol_result = enqueue_due_patrols()
            post_result = enqueue_due_scheduled_posts()
            watch_result = tick_watches()
            patrol_enq = len(patrol_result.get("enqueued") or [])
            patrol_skp = len(patrol_result.get("skipped") or [])
            post_enq = len(post_result.get("enqueued") or [])
            post_skp = len(post_result.get("skipped") or [])
            watch_fired = len(watch_result.get("fired") or [])
            watch_skipped = len(watch_result.get("skipped") or [])
            now = taipei_now_str()  # Asia/Taipei per CLAUDE.md §1.1
            if args.verbose:
                combined = {"patrol": patrol_result, "scheduled_post": post_result, "watches": watch_result}
                print(f"[scheduler] {now} cycle={cycles} {json.dumps(combined, ensure_ascii=False)}", flush=True)
            elif patrol_enq or patrol_skp or post_enq or post_skp or watch_fired:
                print(
                    f"[scheduler] {now} cycle={cycles} "
                    f"patrol(enq={patrol_enq},skp={patrol_skp}) "
                    f"posts(enq={post_enq},skp={post_skp}) "
                    f"watches(fired={watch_fired},skp={watch_skipped})",
                    flush=True,
                )
        except Exception as exc:  # noqa: BLE001 — daemon must not die from a single bad cycle
            print(f"[scheduler] cycle={cycles} error={exc!r}", flush=True, file=sys.stderr)

        # Dashboard notifications: daily digest + aging-review alerts.
        # Wrapped in its own try so a Lark hiccup doesn't poison the cycle.
        try:
            _maybe_push_dashboard_notifications()
        except Exception as exc:  # noqa: BLE001
            print(f"[scheduler] dashboard_push error={exc!r}", flush=True, file=sys.stderr)

        # Sleep in 1s slices so SIGTERM/SIGINT exits within a second.
        slept = 0
        while slept < args.interval_seconds and not _stopping:
            time.sleep(1)
            slept += 1

    print(f"[scheduler] stopped after {cycles} cycles", flush=True)
    return 0


def _maybe_push_dashboard_notifications() -> None:
    """Two scheduler-driven Lark notifications:

    1. Daily digest at OPERATOR_DAILY_DIGEST_HOUR_TAIPEI (default 09).
    2. Aging-review alert when any pending review crosses
       OPERATOR_AGING_REVIEW_HOURS (default 4) — once per review_id.

    Both require OPERATOR_DAILY_DIGEST_CHAT_ID env var; we no-op cleanly
    when it's not set so devs can run the daemon without Lark wired.
    """

    chat_id = os.getenv("OPERATOR_DAILY_DIGEST_CHAT_ID", "").strip()
    if not chat_id:
        return

    try:
        from app.lark.client import LarkClient, LarkClientError
    except ImportError:
        return

    customer_id = os.getenv("OPERATOR_CUSTOMER_ID", "customer_a")
    target_hour = int(os.getenv("OPERATOR_DAILY_DIGEST_HOUR_TAIPEI", "9"))

    # 1. Daily digest.
    if should_send_daily_digest(customer_id, target_hour_taipei=target_hour):
        try:
            data = collect_dashboard_data(customer_id)
            text = "🌅 今日 Project Echo 摘要\n\n" + format_text_report(data, compact=True)
            client = LarkClient()
            client.send_message(chat_id, "text", {"text": text}, receive_id_type="chat_id")
            mark_daily_digest_sent(customer_id)
            print(f"[scheduler] daily_digest pushed to {chat_id[:12]}…", flush=True)
        except LarkClientError as exc:
            print(f"[scheduler] daily_digest lark error={exc}", flush=True, file=sys.stderr)

    # 2. Aging review alerts (one ping per review_id, ever).
    threshold_hours = aging_review_alert_threshold_hours()
    data = collect_dashboard_data(customer_id)
    aged = [
        p for p in (data.get("pending_reviews") or [])
        if p.get("age_hours", 0) >= threshold_hours
        and should_alert_aging_review(customer_id, p["review_id"])
    ]
    if aged:
        try:
            client = LarkClient()
            for p in aged:
                msg = (
                    f"⚠️ 待審 review 積壓提醒\n\n"
                    f"  review_id: {p.get('review_id')}\n"
                    f"  社群: {p.get('community_name')}\n"
                    f"  草稿: 「{p.get('draft_text', '')[:60]}」\n"
                    f"  已等待: {p.get('age_hours')}h（門檻 {threshold_hours}h）\n"
                    f"  建立於: {p.get('created_at_taipei')}\n\n"
                    f"請決定 通過 / 修改 / 忽略。"
                )
                client.send_message(chat_id, "text", {"text": msg}, receive_id_type="chat_id")
                mark_aging_alert_sent(customer_id, p["review_id"])
            print(f"[scheduler] aging_alerts pushed: {len(aged)}", flush=True)
        except LarkClientError as exc:
            print(f"[scheduler] aging_alert lark error={exc}", flush=True, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
