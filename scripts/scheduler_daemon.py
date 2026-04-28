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

import _bootstrap  # noqa: F401  (must precede app.* imports — adds project root to sys.path)

from app.core.timezone import taipei_now_str
from app.workflows.job_runner import ensure_job_worker
from app.workflows.scheduler import enqueue_due_patrols, enqueue_due_scheduled_posts, tick_watches


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

        # Sleep in 1s slices so SIGTERM/SIGINT exits within a second.
        slept = 0
        while slept < args.interval_seconds and not _stopping:
            time.sleep(1)
            slept += 1

    print(f"[scheduler] stopped after {cycles} cycles", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
