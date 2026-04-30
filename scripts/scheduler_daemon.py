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

    # HIL state audit — single most important invariant in the system.
    # If require_human_approval is OFF, scheduled_post pre_approved
    # drafts can auto-send without operator review. Print a prominent
    # warning so accidental misconfiguration is caught immediately.
    from app.config import settings as _hil_settings
    from app.core.audit import append_audit_event as _audit
    _hil_on = _hil_settings.require_human_approval
    _audit(
        _hil_settings.default_customer_id,
        "daemon_started",
        {
            "require_human_approval": _hil_on,
            "interval_seconds": args.interval_seconds,
        },
    )
    if _hil_on:
        print("[scheduler] ✅ HIL gate ENABLED (require_human_approval=true)", flush=True)
    else:
        print(
            "[scheduler] " + "⚠️ " * 6,
            flush=True, file=sys.stderr,
        )
        print(
            "[scheduler] ⚠️  HIL gate is DISABLED (ECHO_REQUIRE_HUMAN_APPROVAL=false).\n"
            "[scheduler] ⚠️  Pre-approved scheduled_post drafts WILL auto-send WITHOUT operator review.\n"
            "[scheduler] ⚠️  This is the project's most sensitive invariant — verify this is intentional.\n"
            "[scheduler] ⚠️  To restore: unset the env var or set ECHO_REQUIRE_HUMAN_APPROVAL=true.",
            flush=True, file=sys.stderr,
        )
        print("[scheduler] " + "⚠️ " * 6, flush=True, file=sys.stderr)
        # Brief pause so the warning isn't lost in the boot scroll. If
        # the operator really meant it, 3 seconds is nothing; if they
        # mistyped, this is the moment they catch it.
        time.sleep(3.0)

    # Onboarding readiness: warn (don't block) when a community has auto_watch
    # enabled but is missing critical setup (operator_nickname, voice profile,
    # invite_url/group_id). Otherwise the watcher would compose drafts for a
    # community where it doesn't even know who the operator is.
    try:
        from app.workflows.onboarding_status import build_onboarding_report
        _onboarding = build_onboarding_report()
        for _row in _onboarding.auto_watch_with_gaps:
            print(
                f"[scheduler] WARNING: auto_watch is on for {_row.community_id} "
                f"({_row.display_name}) but missing: {', '.join(_row.critical_gaps)}. "
                f"Run scripts/onboarding_status.py for details.",
                flush=True,
                file=sys.stderr,
            )
    except Exception as exc:  # noqa: BLE001 — diagnostic only, never block boot
        print(f"[scheduler] onboarding check skipped: {exc!r}", flush=True, file=sys.stderr)

    # Catch a common misconfiguration: ECHO_LLM_ENABLED=true but no API key.
    # is_enabled() silently returns False in that case, so every draft falls
    # back to the rule-based template without any signal to the operator.
    from app.config import settings as _echo_settings
    if _echo_settings.llm_enabled and not _echo_settings.anthropic_api_key:
        print(
            "[scheduler] WARNING: ECHO_LLM_ENABLED=true but ANTHROPIC_API_KEY is unset; "
            "drafts will silently fall back to rule-based templates. "
            "Set the key or unset the flag to silence this warning.",
            flush=True,
            file=sys.stderr,
        )

    # Audit log size check — append-only log can grow forever. Warn
    # operator when crossing 50 MB / 200 MB so they can rotate or
    # archive (e.g. `scripts/export_audit_redacted.py` for sharing).
    try:
        from app.core.audit import audit_log_stats
        _stats = audit_log_stats(_hil_settings.default_customer_id)
        if _stats["severity"] == "critical":
            print(
                f"[scheduler] ⚠️  audit log {_stats['size_human']} (>200 MB) — "
                f"reads will be slow, consider archiving",
                flush=True, file=sys.stderr,
            )
        elif _stats["severity"] == "warn":
            print(
                f"[scheduler] audit log {_stats['size_human']} — "
                f"consider periodic rotation",
                flush=True,
            )
        else:
            print(
                f"[scheduler] audit log {_stats['size_human']} ({_stats['line_count']} events)",
                flush=True,
            )
    except Exception as exc:  # noqa: BLE001 — diagnostic only
        print(f"[scheduler] audit_log_stats skipped: {exc!r}", flush=True, file=sys.stderr)

    # Operator identity sanity check — print a per-community table of
    # operator_nickname × chat_export hits so a typo can't silently
    # corrupt 6 months of fingerprint data the way 翊→妍 did on 004.
    # 0 export hits is legitimate for fan/broadcast groups; we only
    # surface visually-confusable Han characters as ⚠️.
    try:
        from app.workflows.operator_identity import audit_all_communities
        _identity = audit_all_communities(_hil_settings.default_customer_id)
        print("[scheduler] operator identity check:", flush=True)
        for _row in _identity.get("rows", []):
            cid = _row["community_id"]
            nick = _row.get("operator_nickname") or "(unset)"
            hits = _row.get("export_hits")
            confusable = _row.get("confusable_chars") or []
            tag = ""
            if _row["status"] == "missing":
                tag = "  ❌ operator_nickname MISSING"
            elif confusable:
                tag = f"  ⚠️ contains confusable char(s) {''.join(confusable)} — verify against LINE UI"
            elif _row["status"] == "low_activity":
                tag = "  · 0 export hits (OK if fan/broadcast group)"
            else:
                tag = "  ✓"
            print(f"[scheduler]   {cid}  {nick:<10}  hits={hits}{tag}", flush=True)
        _audit(
            _hil_settings.default_customer_id,
            "daemon_startup_operator_identity_audit",
            {
                "rows": _identity.get("rows", []),
                "warning_count": _identity.get("warning_count", 0),
            },
        )
        # If any community has missing nickname, emit the same ⚠️×6
        # banner as the HIL gate so it can't be lost in boot scroll.
        if any(r.get("status") == "missing" for r in _identity.get("rows", [])):
            print("[scheduler] " + "⚠️ " * 6, flush=True, file=sys.stderr)
            print(
                "[scheduler] ⚠️  One or more communities have NO operator_nickname.\n"
                "[scheduler] ⚠️  Self-detection in those communities will fail; chat_export\n"
                "[scheduler] ⚠️  derived data (lifecycle / kpi / relationship_graph) will\n"
                "[scheduler] ⚠️  treat the operator as an ordinary member.\n"
                "[scheduler] ⚠️  Run set_operator_nickname for each missing community.",
                flush=True, file=sys.stderr,
            )
            print("[scheduler] " + "⚠️ " * 6, flush=True, file=sys.stderr)
            time.sleep(3.0)
    except Exception as exc:  # noqa: BLE001 — diagnostic only
        print(f"[scheduler] operator identity check skipped: {exc!r}", flush=True, file=sys.stderr)

    # Crash-recovery sweep: scheduled_post / review state may have entries
    # mid-flight from a previous daemon process. Reset orphans before the
    # main loop so they re-fire / surface to operator. Idempotent.
    try:
        from app.workflows.orphan_recovery import recover_orphan_state
        rec = recover_orphan_state()
        if rec.due_orphans_reset or rec.reviewing_orphans_marked or rec.stale_pending_reviews:
            print(
                f"[scheduler] orphan recovery: due_reset={rec.due_orphans_reset} "
                f"reviewing_skipped={rec.reviewing_orphans_marked} "
                f"stale_pending={rec.stale_pending_reviews}",
                flush=True,
            )
        if rec.errors:
            print(f"[scheduler] orphan recovery errors: {rec.errors}", flush=True, file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 — recovery never blocks boot
        print(f"[scheduler] orphan recovery skipped: {exc!r}", flush=True, file=sys.stderr)

    # Warm up heavy AI singletons (BGE embedding + Chinese-Emotion) so the
    # in-process watch tick path doesn't pay the cold-load tax on first use.
    # Skip with ECHO_SKIP_WARMUP=1 (e.g. for fast restarts during dev).
    if not os.getenv("ECHO_SKIP_WARMUP"):
        try:
            from app.workflows.model_warmup import warm_up_models
            warm_t = time.time()
            stats = warm_up_models()
            print(f"[scheduler] models warmed in {time.time() - warm_t:.1f}s: {stats}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[scheduler] model warmup error={exc!r}", flush=True, file=sys.stderr)

    cycles = 0
    while not _stopping:
        cycles += 1
        try:
            # Detect operator edits to voice_profile.md between ticks.
            # Cheap (one stat() per community); on change → audit
            # `voice_profile_changed` so the dashboard alert layer
            # surfaces "your edit landed" feedback to the operator.
            try:
                from app.workflows.voice_profile_watcher import detect_voice_profile_changes
                detect_voice_profile_changes()
            except Exception as exc:  # noqa: BLE001 — non-fatal diagnostic
                print(f"[scheduler] voice_profile watch skipped: {exc!r}", flush=True, file=sys.stderr)

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

        # Cold-spell heartbeat: low-frequency check (every ~60 cycles ≈ 1h
        # at default 60s loop). Pushes a Lark alert when a community has
        # been silent for >12h since last analyze. Internal cooldown means
        # repeated calls within 24h don't spam the operator.
        if cycles % 60 == 0:
            try:
                from app.workflows.cold_spell_alert import run_heartbeat
                hb = run_heartbeat()
                if hb.alerted:
                    ids = ",".join(c.community_id for c in hb.alerted)
                    print(
                        f"[scheduler] cold_spell_alert pushed for: {ids} (lark={'ok' if hb.pushed_lark else 'skip'})",
                        flush=True,
                    )
            except Exception as exc:  # noqa: BLE001
                print(f"[scheduler] cold_spell_heartbeat error={exc!r}", flush=True, file=sys.stderr)

        # Self-detection health (24h cadence + once on startup): catches
        # operator_nickname drift / typo where startup invariant said the
        # value was set but no posts in the chat actually match. See
        # post-2026-04-30 incident defense layer 3.
        cycles_per_day = max(1, int(86400 // max(1, args.interval_seconds)))
        if cycles == 1 or cycles % cycles_per_day == 0:
            try:
                from app.workflows.self_detection_health import run_health_check
                health = run_health_check(_hil_settings.default_customer_id)
                if health.get("failed_count"):
                    ids = ",".join(r.get("community_id") or "?" for r in health.get("failed", []))
                    print(
                        f"[scheduler] self_detection_health: {health['failed_count']} community(s) "
                        f"with low operator self-ratio ({ids}) — see alerts panel",
                        flush=True,
                    )
            except Exception as exc:  # noqa: BLE001
                print(f"[scheduler] self_detection_health error={exc!r}", flush=True, file=sys.stderr)

        # Auto-watch: opt-in per community.yaml.
        try:
            _maybe_run_auto_watch()
        except Exception as exc:  # noqa: BLE001
            print(f"[scheduler] auto_watch error={exc!r}", flush=True, file=sys.stderr)

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
        from app.core.audit import append_audit_event
        try:
            data = collect_dashboard_data(customer_id)
            text = "🌅 今日 Project Echo 摘要\n\n" + format_text_report(data, compact=True)
            client = LarkClient()
            client.send_message(chat_id, "text", {"text": text}, receive_id_type="chat_id")
            mark_daily_digest_sent(customer_id)
            print(f"[scheduler] daily_digest pushed to {chat_id[:12]}…", flush=True)
            append_audit_event(customer_id, "daily_digest_sent", {
                "chat_id_prefix": chat_id[:12],
                "target_hour_taipei": target_hour,
                "char_count": len(text),
            })
        except LarkClientError as exc:
            print(f"[scheduler] daily_digest lark error={exc}", flush=True, file=sys.stderr)
            append_audit_event(customer_id, "daily_digest_failed", {
                "chat_id_prefix": chat_id[:12],
                "target_hour_taipei": target_hour,
                "error": str(exc)[:200],
            })

    # 2. Aging review alerts (one ping per review_id, ever).
    threshold_hours = aging_review_alert_threshold_hours()
    data = collect_dashboard_data(customer_id)
    aged = [
        p for p in (data.get("pending_reviews") or [])
        if p.get("age_hours", 0) >= threshold_hours
        and should_alert_aging_review(customer_id, p["review_id"])
    ]
    if aged:
        from app.core.audit import append_audit_event
        try:
            client = LarkClient()
            sent_ids: list[str] = []
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
                sent_ids.append(str(p["review_id"]))
            print(f"[scheduler] aging_alerts pushed: {len(aged)}", flush=True)
            append_audit_event(customer_id, "aging_review_alerts_sent", {
                "count": len(sent_ids),
                "review_ids": sent_ids,
                "threshold_hours": threshold_hours,
            })
        except LarkClientError as exc:
            print(f"[scheduler] aging_alert lark error={exc}", flush=True, file=sys.stderr)
            append_audit_event(customer_id, "aging_review_alerts_failed", {
                "attempted": len(aged),
                "error": str(exc)[:200],
            })


def _maybe_run_auto_watch() -> None:
    """Per-community opt-in auto-watch (start at start_hour_tpe, stop at end_hour_tpe).

    Reads each community.yaml's `auto_watch` block. Default OFF — no community
    auto-starts unless explicitly enabled. HIL gate is unaffected; this only
    decides *when watches run*, never bypassing review_store.
    """

    from app.workflows.auto_watch import run_auto_watch_cycle
    result = run_auto_watch_cycle()
    if result.started:
        names = ",".join(str(w.get("community_id")) for w in result.started)
        print(f"[scheduler] auto_watch_started: {names}", flush=True)
    if result.stopped:
        names = ",".join(str(w.get("community_id")) for w in result.stopped)
        print(f"[scheduler] auto_watch_stopped: {names}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
