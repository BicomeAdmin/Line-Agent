"""Watcher Phase 2 — daemon-side per-watch tick (in-process).

For each active watch the in-process tick (`watch_tick_inproc.tick_one_inprocess`)
runs: `navigate → read → select_reply_target → decide_reply → review_store +
Lark card push`. Heavy model singletons (BGE embedding, Chinese-Emotion) are
warmed at daemon boot so each tick is cheap.

Historical note: a `codex exec` spawn path lived here until 2026-04-29. It
was removed because every spawn forked a fresh MCP server that cold-loaded
the embedding + emotion models (~22 s), which the codex MCP client killed
as a transport timeout before the first tool call could return. The
in-process path eliminates the spawn entirely.
"""

from __future__ import annotations

import time

from app.adb.human_jitter import jittered_poll_interval
from app.storage.watches import (
    list_active_watches_all_customers,
    update_watch_state,
)
from app.workflows.watch_tick_inproc import tick_one_inprocess


def tick_all_watches() -> dict[str, object]:
    """Called once per scheduler cycle. Cheap when no active watches."""

    fired: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    now = time.time()
    for watch in list_active_watches_all_customers():
        watch_id = str(watch.get("watch_id") or "")
        last_check = float(watch.get("last_check_epoch") or 0)
        # Random ±25% jitter on poll interval so two daemons running the same
        # config don't poll in lockstep, and any single watch's cadence isn't
        # perfectly periodic. Anti-fingerprinting per roadmap Tier 1 #2.
        base_interval = int(watch.get("poll_interval_seconds") or 60)
        jittered_min = jittered_poll_interval(base_interval)
        if last_check and (now - last_check) < jittered_min:
            skipped.append({"watch_id": watch_id, "reason": "poll_interval"})
            continue
        outcome = tick_one_inprocess(watch)
        update_watch_state(
            str(watch.get("customer_id")),
            watch_id,
            last_check_epoch=now,
            last_seen_signature=outcome.get("new_signature"),
            last_draft_epoch=outcome.get("draft_epoch"),
        )
        (fired if outcome.get("acted") else skipped).append({"watch_id": watch_id, **outcome})
    return {"fired": fired, "skipped": skipped}
