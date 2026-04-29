"""Auto-start / auto-stop daily watches per opt-in community config.

Why this exists: Watcher Phase 2 only runs when an active watch exists. Watches
expire after their duration window (default 1h when started manually). Result:
every morning the operator must remember to /start_watch each community by hand,
which doesn't scale and breaks the autonomy loop.

This module lets each community.yaml opt in with an `auto_watch` block:

    auto_watch:
      enabled: true
      start_hour_tpe: 10
      end_hour_tpe: 22
      duration_minutes: 720
      cooldown_seconds: 600
      poll_interval_seconds: 60

The scheduler daemon calls run_auto_watch_cycle() every loop. At start_hour
(matched within the same 5-minute window as daily_digest), we add_watch the
community for `duration_minutes`. At end_hour we stop the watch, marking it
auto_stopped. Markers under data/auto_watches/ ensure idempotency.

HIL guarantee unchanged: this only controls *when watches run*, not the
operator approval gate. Every draft still goes through review_store.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.core.audit import append_audit_event
from app.storage import watches as watches_store
from app.storage.config_loader import CommunityConfig, load_all_communities
from app.storage.paths import customer_root


TPE = ZoneInfo("Asia/Taipei")
AUTO_NOTE_PREFIX = "auto_watch"


@dataclass
class AutoWatchCycleResult:
    started: list[dict[str, object]] = field(default_factory=list)
    stopped: list[dict[str, object]] = field(default_factory=list)
    skipped: list[dict[str, object]] = field(default_factory=list)


def _markers_dir(customer_id: str) -> Path:
    return customer_root(customer_id) / "data" / "auto_watches"


def _marker_path(customer_id: str, community_id: str, today_str: str) -> Path:
    return _markers_dir(customer_id) / f"{community_id}__{today_str}.txt"


def _today_str(now: datetime) -> str:
    return now.astimezone(TPE).strftime("%Y-%m-%d")


def _within_start_window(community: CommunityConfig, now: datetime) -> bool:
    tpe = now.astimezone(TPE)
    return tpe.hour == community.auto_watch_start_hour_tpe and tpe.minute < 5


def _past_end_window(community: CommunityConfig, now: datetime) -> bool:
    tpe = now.astimezone(TPE)
    return tpe.hour >= community.auto_watch_end_hour_tpe


def _has_active_auto_watch(customer_id: str, community_id: str) -> dict[str, object] | None:
    for w in watches_store.list_watches(customer_id, only_active=True):
        if w.get("community_id") != community_id:
            continue
        note = str(w.get("note") or "")
        if note.startswith(AUTO_NOTE_PREFIX):
            return w
    return None


def run_auto_watch_cycle(now: datetime | None = None) -> AutoWatchCycleResult:
    """Single cycle. Idempotent. Safe to call from the scheduler loop."""

    now = now or datetime.now(TPE)
    result = AutoWatchCycleResult()

    for community in load_all_communities():
        if not community.auto_watch_enabled:
            continue

        # === Start phase ===
        if _within_start_window(community, now):
            today = _today_str(now)
            marker = _marker_path(community.customer_id, community.community_id, today)
            if marker.exists():
                result.skipped.append({
                    "community_id": community.community_id,
                    "reason": "already_started_today",
                })
            else:
                watch = watches_store.add_watch(
                    customer_id=community.customer_id,
                    community_id=community.community_id,
                    duration_minutes=community.auto_watch_duration_minutes,
                    initiator_chat_id=None,
                    cooldown_seconds=community.auto_watch_cooldown_seconds,
                    poll_interval_seconds=community.auto_watch_poll_interval_seconds,
                    note=f"{AUTO_NOTE_PREFIX}: auto-started at {now.astimezone(TPE).strftime('%H:%M %Z')}",
                )
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text(today, encoding="utf-8")
                append_audit_event(
                    community.customer_id,
                    "watch_auto_started",
                    {
                        "watch_id": watch["watch_id"],
                        "community_id": community.community_id,
                        "duration_minutes": community.auto_watch_duration_minutes,
                        "today": today,
                    },
                )
                result.started.append(watch)

        # === Stop phase ===
        if _past_end_window(community, now):
            active = _has_active_auto_watch(community.customer_id, community.community_id)
            if active is not None:
                stopped_records = watches_store.stop_watch(
                    customer_id=community.customer_id,
                    watch_id=str(active["watch_id"]),
                    reason="auto_watch_end_of_day",
                )
                for rec in stopped_records:
                    append_audit_event(
                        community.customer_id,
                        "watch_auto_stopped",
                        {
                            "watch_id": rec["watch_id"],
                            "community_id": community.community_id,
                            "reason": "end_of_day_window",
                        },
                    )
                    result.stopped.append(rec)

    return result
