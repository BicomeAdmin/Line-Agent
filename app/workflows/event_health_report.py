"""Health report for the day's two ignition events:
   - 09:00 daily digest push (scheduler_daemon → Lark)
   - 10:00 first watcher cycle (watch_tick → compose → review_card_push)

Read-only. Does not touch production paths."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.core.audit import read_recent_audit_events
from app.workflows.dashboard import (
    collect_dashboard_data,
    daily_digest_marker_path,
    format_text_report,
)


TPE = ZoneInfo("Asia/Taipei")
SCHEDULER_LOG = Path("/tmp/scheduler_daemon.log")
LARK_BRIDGE_LOG = Path("/tmp/lark_bridge.log")


@dataclass
class DigestHealth:
    target_hour: int
    today_str: str
    marker_present: bool
    marker_value: str | None
    sent_today: bool
    audit_sent_event: dict[str, Any] | None = None
    audit_failed_event: dict[str, Any] | None = None
    log_push_lines: list[str] = field(default_factory=list)
    log_error_lines: list[str] = field(default_factory=list)
    rendered_preview: str = ""
    rendered_char_count: int = 0
    sections_present: dict[str, bool] = field(default_factory=dict)


@dataclass
class WatcherHealth:
    watches_active: list[dict[str, Any]] = field(default_factory=list)
    recent_tick_events: list[dict[str, Any]] = field(default_factory=list)
    recent_review_cards: list[dict[str, Any]] = field(default_factory=list)
    recent_compose_reviews: list[dict[str, Any]] = field(default_factory=list)
    log_lark_push_lines: list[str] = field(default_factory=list)


def _today_tpe(now: datetime | None = None) -> str:
    return (now or datetime.now(TPE)).strftime("%Y-%m-%d")


def _grep_today(path: Path, today_str: str, pattern: re.Pattern[str]) -> list[str]:
    """Last 500 lines, filter for today's date prefix or matching pattern."""
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]
    return [ln for ln in lines if pattern.search(ln)]


def collect_digest_health(
    customer_id: str = "customer_a",
    *,
    target_hour: int = 9,
    now: datetime | None = None,
) -> DigestHealth:
    today_str = _today_tpe(now)
    marker = daily_digest_marker_path(customer_id)
    marker_value = marker.read_text(encoding="utf-8").strip() if marker.exists() else None
    sent_today = marker_value == today_str

    push_pattern = re.compile(r"daily_digest pushed", re.IGNORECASE)
    err_pattern = re.compile(r"daily_digest .*error", re.IGNORECASE)
    log_push = _grep_today(SCHEDULER_LOG, today_str, push_pattern)
    log_err = _grep_today(SCHEDULER_LOG, today_str, err_pattern)

    today_iso_prefix = (now or datetime.now(TPE)).astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%d")
    recent_events = read_recent_audit_events(customer_id, limit=200)
    audit_sent = next(
        (e for e in reversed(recent_events)
         if e.get("event_type") == "daily_digest_sent"
         and str(e.get("timestamp", "")).startswith(today_iso_prefix)),
        None,
    )
    audit_failed = next(
        (e for e in reversed(recent_events)
         if e.get("event_type") == "daily_digest_failed"
         and str(e.get("timestamp", "")).startswith(today_iso_prefix)),
        None,
    )

    # Re-render compact report against current state (does NOT send)
    data = collect_dashboard_data(customer_id)
    preview = "🌅 今日 Project Echo 摘要\n\n" + format_text_report(data, compact=True)

    section_markers = {
        "system_health": "🩺 系統健康",
        "send_stats_24h": "📨 24h 送發統計",
        "communities": "🌐 社群",
        "pending_reviews": "📥 待審 inbox",
        "recent_auto_fire": "🛎  最近 auto-fire",
    }
    sections_present = {key: (marker in preview) for key, marker in section_markers.items()}

    return DigestHealth(
        target_hour=target_hour,
        today_str=today_str,
        marker_present=marker.exists(),
        marker_value=marker_value,
        sent_today=sent_today,
        audit_sent_event=audit_sent,
        audit_failed_event=audit_failed,
        log_push_lines=log_push,
        log_error_lines=log_err,
        rendered_preview=preview,
        rendered_char_count=len(preview),
        sections_present=sections_present,
    )


def collect_watcher_health(
    customer_id: str = "customer_a",
    *,
    audit_window: int = 200,
    now: datetime | None = None,
) -> WatcherHealth:
    now = now or datetime.now(TPE)
    cutoff_utc = (now.astimezone(ZoneInfo("UTC")) - timedelta(hours=2)).isoformat()

    watches_path = Path(f"customers/{customer_id}/data/watches.json")
    watches_active: list[dict[str, Any]] = []
    if watches_path.exists():
        try:
            payload = json.loads(watches_path.read_text(encoding="utf-8"))
            entries = payload if isinstance(payload, list) else list(payload.values()) if isinstance(payload, dict) else []
            now_epoch = now.timestamp()
            for w in entries:
                if not isinstance(w, dict):
                    continue
                status = w.get("status", "active")
                end_at = w.get("end_at_epoch", 0) or 0
                if status == "active" and end_at > now_epoch:
                    watches_active.append(w)
        except json.JSONDecodeError:
            pass

    events = read_recent_audit_events(customer_id, limit=audit_window)
    recent = [e for e in events if str(e.get("timestamp", "")) >= cutoff_utc]

    recent_tick = [e for e in recent if e.get("event_type") == "watch_tick_fired"]
    recent_card = [e for e in recent if e.get("event_type") == "operator_review_card_pushed"]
    recent_compose = [e for e in recent if e.get("event_type") == "mcp_compose_review_created"]

    push_pattern = re.compile(r"review_card|compose|watch_tick", re.IGNORECASE)
    log_push = _grep_today(LARK_BRIDGE_LOG, _today_tpe(now), push_pattern)[-20:]

    return WatcherHealth(
        watches_active=watches_active,
        recent_tick_events=recent_tick,
        recent_review_cards=recent_card,
        recent_compose_reviews=recent_compose,
        log_lark_push_lines=log_push,
    )


def render_text_report(
    digest: DigestHealth | None,
    watcher: WatcherHealth | None,
    *,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(TPE)
    out: list[str] = [f"📊 Project Echo Event Health  ({now.strftime('%Y-%m-%d %H:%M:%S TPE')})"]

    if digest is not None:
        out.append("")
        out.append("──── 09:00 Daily Digest ────")
        out.append(f"  目標時段:    每天 {digest.target_hour:02d}:00-{digest.target_hour:02d}:05 TPE")
        out.append(f"  Marker 檔:   {'✅ 存在' if digest.marker_present else '❌ 缺'}  (值={digest.marker_value or 'N/A'})")
        out.append(f"  今日送出:    {'✅ YES' if digest.sent_today else '⏳ NOT YET (' + digest.today_str + ')'}")
        if digest.audit_sent_event:
            payload = digest.audit_sent_event.get("payload") or {}
            out.append(f"  Audit:       ✅ daily_digest_sent  ts={digest.audit_sent_event.get('timestamp', '')}  chars={payload.get('char_count')}")
        if digest.audit_failed_event:
            payload = digest.audit_failed_event.get("payload") or {}
            out.append(f"  Audit:       ⚠️ daily_digest_failed  err={payload.get('error', '')[:80]}")
        out.append(f"  Log push:    {len(digest.log_push_lines)} 筆")
        for ln in digest.log_push_lines[-3:]:
            out.append(f"    » {ln.strip()}")
        if digest.log_error_lines:
            out.append(f"  Log error:   ⚠️ {len(digest.log_error_lines)} 筆")
            for ln in digest.log_error_lines[-3:]:
                out.append(f"    » {ln.strip()}")
        out.append(f"  預覽長度:    {digest.rendered_char_count} 字")
        sections_status = " ".join(f"{k}={'✓' if v else '✗'}" for k, v in digest.sections_present.items())
        out.append(f"  4-bucket:    {sections_status}")

    if watcher is not None:
        out.append("")
        out.append("──── 10:00 Watcher Cycle ────")
        out.append(f"  Active watches:           {len(watcher.watches_active)}")
        for w in watcher.watches_active[:5]:
            cid = w.get("community_id", "?")
            last_tick = w.get("last_tick_at", "—")
            out.append(f"    » {cid}  last_tick={last_tick}")
        out.append(f"  watch_tick (2h):          {len(watcher.recent_tick_events)}")
        out.append(f"  compose_review (2h):      {len(watcher.recent_compose_reviews)}")
        out.append(f"  review_card_pushed (2h):  {len(watcher.recent_review_cards)}")
        if watcher.log_lark_push_lines:
            out.append("  Bridge log tail:")
            for ln in watcher.log_lark_push_lines[-5:]:
                out.append(f"    » {ln.strip()}")

    return "\n".join(out)
