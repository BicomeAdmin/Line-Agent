"""Operator dashboard — unified read-only aggregator across the system.

Pulls from review_store, audit.jsonl, watches.json, scheduled_posts.json,
voice_profile files, and process state. Returns either:

  - a structured dict (for programmatic / Lark digest use), via
    `collect_dashboard_data(customer_id)`
  - a pretty multi-section text report, via `format_text_report(data)`

Used by:
  - scripts/dashboard.py (CLI)
  - MCP tool get_status_digest (on-demand from Lark)
  - scheduler_daemon daily push (proactive)

Read-only — never mutates state.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

from app.core.audit import read_recent_audit_events
from app.core.reviews import ACTIVE_REVIEW_STATUSES, review_store
from app.core.timezone import to_taipei_str
from app.storage.config_loader import load_all_communities
from app.storage.paths import customer_root, voice_profile_path
from app.storage.watches import list_active_watches_all_customers
from app.workflows.persona_context import get_persona_context
from app.workflows.send_metrics import get_send_metrics


PROC_GREP_NAMES = {
    "scheduler_daemon": "scheduler_daemon.py",
    "lark_bridge": "start_lark_long_connection.py",
}


def collect_dashboard_data(customer_id: str = "customer_a") -> dict[str, object]:
    """Gather everything in one pass and return a structured snapshot."""

    now = time.time()
    metrics = get_send_metrics(customer_id, since_hours=24.0)
    pending_reviews = [
        r for r in review_store.list_all()
        if r.customer_id == customer_id and r.status in ACTIVE_REVIEW_STATUSES
    ]
    watches = [w for w in list_active_watches_all_customers() if w.get("customer_id") == customer_id]

    summarized_watches = [_summarize_watch(w, now) for w in watches]
    communities = []
    for community in load_all_communities():
        if community.customer_id != customer_id:
            continue
        cid = community.community_id
        vp_path = voice_profile_path(customer_id, cid)
        vp_lines = _count_lines(vp_path)
        vp_harvested = _has_harvested_block(vp_path)
        community_pending = sum(1 for r in pending_reviews if r.community_id == cid)
        community_watch = next(
            (w for w in summarized_watches if w.get("community_id") == cid), None
        )
        # Persona summary — 1-line "你在這群是 X，最近講過 Y" — so the
        # operator sees at a glance which communities have a real persona
        # vs which still have a stub voice profile.
        try:
            persona = get_persona_context(customer_id, cid)
            persona_summary = persona.get("summary_zh") if persona.get("status") == "ok" else None
            persona_nickname = (persona.get("voice_profile") or {}).get("nickname") or ""
            recent_count = len(persona.get("recent_self_posts") or [])
        except Exception:  # noqa: BLE001
            persona_summary = None
            persona_nickname = ""
            recent_count = 0
        communities.append({
            "community_id": cid,
            "display_name": community.display_name,
            "voice_profile_lines": vp_lines,
            "voice_profile_harvested": vp_harvested,
            "pending_reviews": community_pending,
            "active_watch": community_watch,
            "persona_nickname": persona_nickname,
            "persona_recent_post_count": recent_count,
            "persona_summary_zh": persona_summary,
        })

    # Pending review aging — biggest age in hours, used by daemon for alerts.
    pending_review_summary = []
    for r in sorted(pending_reviews, key=lambda x: x.created_at):
        age_seconds = now - r.created_at
        pending_review_summary.append({
            "review_id": r.review_id,
            "community_id": r.community_id,
            "community_name": r.community_name,
            "draft_text": r.draft_text,
            "age_hours": round(age_seconds / 3600, 2),
            "age_seconds": int(age_seconds),
            "created_at_taipei": to_taipei_str(datetime.fromtimestamp(r.created_at)),
        })
    oldest_pending_hours = max((p["age_hours"] for p in pending_review_summary), default=0.0)

    recent_audit = []
    for event in list(read_recent_audit_events(customer_id, limit=10) or []):
        recent_audit.append({
            "ts_taipei": to_taipei_str(event.get("timestamp")),
            "event_type": event.get("event_type"),
            "summary": _summarize_audit_payload(event),
        })

    health = _process_health()

    return {
        "generated_at_taipei": to_taipei_str(datetime.fromtimestamp(now)),
        "customer_id": customer_id,
        "health": health,
        "send_metrics_24h": metrics,
        "communities": communities,
        "pending_reviews": pending_review_summary,
        "oldest_pending_hours": oldest_pending_hours,
        "active_watches": summarized_watches,
        "recent_auto_fires": (metrics.get("auto_fires") or [])[-5:],
        "recent_audit": recent_audit,
    }


# ──────────────────────────────────────────────────────────────────────
# Text formatter
# ──────────────────────────────────────────────────────────────────────

def format_text_report(data: dict[str, object], *, compact: bool = False) -> str:
    """Render the dashboard data as a multi-section text block.

    `compact=True` strips the recent_audit section — used for Lark digest
    where space matters.
    """

    out: list[str] = []
    out.append(f"📊 Project Echo 狀態 — {data.get('generated_at_taipei')}")
    out.append("")

    # Health
    health = data.get("health") or {}
    out.append("🩺 系統健康")
    for name, info in health.items():
        if info.get("running"):
            out.append(f"  ✅ {name}  PID {info.get('pid')}  ({info.get('etime', '?')})")
        else:
            out.append(f"  ❌ {name}  未在執行")
    out.append("")

    # Send metrics
    totals = (data.get("send_metrics_24h") or {}).get("totals") or {}
    by_source = totals.get("by_source") or {}
    out.append("📨 24h 送發統計（Asia/Taipei）")
    out.append(
        f"  drafts {totals.get('drafts_created', 0)}  "
        f"sent {totals.get('sent', 0)}  "
        f"ignored {totals.get('ignored', 0)}  "
        f"pending {totals.get('review_pending', 0)}"
    )
    if by_source:
        out.append("  by source: " + "  ".join(f"{k}={v}" for k, v in by_source.items()))
    out.append("")

    # Communities
    communities = data.get("communities") or []
    if communities:
        out.append(f"🌐 社群 ({len(communities)})")
        for c in communities:
            vp_badge = "✅ harvested" if c.get("voice_profile_harvested") else "📝 stub"
            watch = c.get("active_watch")
            watch_str = f"  watch ⏰{watch.get('remaining_minutes')}m" if watch else ""
            pending_str = f"  reviews:{c.get('pending_reviews')}" if c.get("pending_reviews") else ""
            out.append(
                f"  {c.get('community_id')}  {c.get('display_name'):20s}  "
                f"{c.get('voice_profile_lines'):>3} lines  {vp_badge}{watch_str}{pending_str}"
            )
        out.append("")

    # Active watches
    watches = data.get("active_watches") or []
    if watches:
        out.append(f"⏰ Active watches ({len(watches)})")
        for w in watches:
            out.append(
                f"  {w.get('community_id')}  剩 {w.get('remaining_minutes')}m  "
                f"上次 check {w.get('last_check_minutes_ago')}m 前"
            )
        out.append("")

    # Pending inbox
    pending = data.get("pending_reviews") or []
    if pending:
        out.append(f"📥 待審 inbox ({len(pending)})")
        for p in pending:
            age_str = (
                f"{int(p.get('age_hours'))}h"
                if p.get("age_hours", 0) >= 1
                else f"{int(p.get('age_seconds', 0) / 60)}m"
            )
            preview = (p.get("draft_text") or "")[:36]
            out.append(
                f"  {p.get('review_id')}  {p.get('community_name')}  「{preview}」  {age_str} 前"
            )
        out.append("")
    else:
        out.append("📥 待審 inbox：無")
        out.append("")

    # Recent auto-fires
    fires = data.get("recent_auto_fires") or []
    if fires:
        out.append(f"🛎  最近 auto-fire ({len(fires)})")
        for f in fires:
            summary = (f.get("codex_summary") or "")[:60]
            out.append(f"  {f.get('fired_at_taipei')}  {f.get('community_name')}  {summary}")
        out.append("")

    # Recent audit (skipped in compact)
    if not compact:
        audit = data.get("recent_audit") or []
        if audit:
            out.append(f"📋 最近事件 ({len(audit)})")
            for a in audit:
                out.append(f"  {a.get('ts_taipei')}  {a.get('event_type'):28s}  {a.get('summary')}")
            out.append("")

    return "\n".join(out).rstrip()


# ──────────────────────────────────────────────────────────────────────
# Internals
# ──────────────────────────────────────────────────────────────────────

def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return len(path.read_text(encoding="utf-8").splitlines())
    except OSError:
        return 0


def _has_harvested_block(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        return "BEGIN auto-harvested" in path.read_text(encoding="utf-8")
    except OSError:
        return False


def _summarize_watch(watch: dict[str, object], now: float) -> dict[str, object]:
    end_at = float(watch.get("end_at_epoch") or 0)
    last_check = float(watch.get("last_check_epoch") or 0)
    return {
        "watch_id": watch.get("watch_id"),
        "community_id": watch.get("community_id"),
        "remaining_minutes": max(0, int((end_at - now) / 60)) if end_at else None,
        "last_check_minutes_ago": int((now - last_check) / 60) if last_check else None,
        "status": watch.get("status"),
    }


def _summarize_audit_payload(event: dict[str, object]) -> str:
    payload = event.get("payload") or {}
    et = event.get("event_type") or ""
    cid = payload.get("community_id") or ""
    if et == "send_attempt":
        return f"{cid} status={payload.get('status')}"
    if et in ("mcp_compose_review_created",):
        return f"{cid} «{(payload.get('text_preview') or '')[:40]}»"
    if et == "watch_tick_fired":
        return f"{cid} {(payload.get('codex_summary') or '')[:40]}"
    if et == "review_status_changed":
        return f"review={payload.get('review_id')} → {payload.get('status')}"
    if et == "style_samples_harvested":
        return f"{cid} wrote={payload.get('samples_written')}"
    if et == "community_title_refreshed":
        return f"{cid} {payload.get('old_display_name')} → {payload.get('new_display_name')}"
    return cid or "—"


def _process_health() -> dict[str, dict[str, object]]:
    """Use `pgrep -f` to detect running daemon + bridge."""

    out: dict[str, dict[str, object]] = {}
    for label, needle in PROC_GREP_NAMES.items():
        try:
            import subprocess
            result = subprocess.run(
                ["pgrep", "-f", needle],
                capture_output=True,
                text=True,
                timeout=2,
            )
            pids = [int(p) for p in result.stdout.split() if p.isdigit()]
        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
            pids = []
        if pids:
            # Real worker process is the python one, not the shell wrapper.
            chosen = _pick_worker_pid(pids, needle)
            out[label] = {
                "running": True,
                "pid": chosen,
                "etime": _process_etime(chosen),
            }
        else:
            out[label] = {"running": False}
    return out


def _pick_worker_pid(pids: list[int], needle: str) -> int:
    """Among matched PIDs prefer the python interpreter line over a shell wrapper."""

    try:
        import subprocess
        result = subprocess.run(
            ["ps", "-o", "pid=,command=", "-p", ",".join(str(p) for p in pids)],
            capture_output=True, text=True, timeout=2,
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            pid_s, cmd = parts
            if "python" in cmd.lower() and needle in cmd:
                return int(pid_s)
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        pass
    return pids[0]


def _process_etime(pid: int) -> str:
    try:
        import subprocess
        result = subprocess.run(
            ["ps", "-o", "etime=", "-p", str(pid)],
            capture_output=True, text=True, timeout=2,
        )
        return result.stdout.strip() or "?"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "?"


# ──────────────────────────────────────────────────────────────────────
# Daily digest helpers (used by scheduler_daemon)
# ──────────────────────────────────────────────────────────────────────

def daily_digest_marker_path(customer_id: str) -> Path:
    return customer_root(customer_id) / "data" / "last_daily_digest.txt"


def should_send_daily_digest(
    customer_id: str,
    *,
    target_hour_taipei: int,
    now_epoch: float | None = None,
) -> bool:
    """Returns True iff (a) we're within 5 minutes after target_hour_taipei
    AND (b) we haven't already sent the digest today (Taipei date).

    Idempotent: scheduler_daemon can call this every cycle; we only fire once.
    """

    from datetime import timezone, timedelta

    tz = timezone(timedelta(hours=8))  # Asia/Taipei
    now = datetime.fromtimestamp(now_epoch or time.time(), tz=tz)

    # Window: target_hour:00–target_hour:05 to avoid missing if a cycle drifts.
    if now.hour != target_hour_taipei or now.minute >= 5:
        return False

    today_str = now.strftime("%Y-%m-%d")
    marker = daily_digest_marker_path(customer_id)
    if marker.exists():
        try:
            if marker.read_text(encoding="utf-8").strip() == today_str:
                return False
        except OSError:
            pass
    return True


def mark_daily_digest_sent(customer_id: str, now_epoch: float | None = None) -> None:
    from datetime import timezone, timedelta
    tz = timezone(timedelta(hours=8))
    now = datetime.fromtimestamp(now_epoch or time.time(), tz=tz)
    marker = daily_digest_marker_path(customer_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(now.strftime("%Y-%m-%d"), encoding="utf-8")


def aging_review_alert_threshold_hours() -> float:
    """Configurable via env var. Default 4 hours — anything older the daemon
    pings the operator about."""

    try:
        return float(os.getenv("OPERATOR_AGING_REVIEW_HOURS", "4"))
    except ValueError:
        return 4.0


def aging_alert_marker_path(customer_id: str) -> Path:
    return customer_root(customer_id) / "data" / "aging_review_alerts.json"


def should_alert_aging_review(customer_id: str, review_id: str) -> bool:
    """True if we haven't already alerted about this review_id."""

    marker = aging_alert_marker_path(customer_id)
    if not marker.exists():
        return True
    try:
        sent = json.loads(marker.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        return True
    return review_id not in sent


def mark_aging_alert_sent(customer_id: str, review_id: str) -> None:
    marker = aging_alert_marker_path(customer_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    try:
        sent = json.loads(marker.read_text(encoding="utf-8") or "{}") if marker.exists() else {}
    except json.JSONDecodeError:
        sent = {}
    sent[review_id] = int(time.time())
    marker.write_text(json.dumps(sent), encoding="utf-8")
