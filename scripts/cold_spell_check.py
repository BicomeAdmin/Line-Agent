from __future__ import annotations

import argparse
import json
import sys

import _bootstrap  # noqa: F401

from app.workflows.cold_spell_alert import run_heartbeat


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the cold-spell heartbeat: report which communities are silent and push a Lark alert.",
    )
    parser.add_argument(
        "--cooldown-hours",
        type=float,
        default=24.0,
        help="Skip alerting a community if we've already alerted within this many hours (default: 24)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Classify candidates and show what would be alerted, but do not push to Lark and do not write markers.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args()

    if args.dry_run:
        # Pre-classify only by patching push_lark off and ignoring marker writes.
        # We still want to inspect candidates, so call the full path with push_lark=False
        # and a cooldown of effectively zero (so nothing is skipped on cooldown grounds)
        # then we only print what the operator would see — but we must avoid writing
        # marker files. Easiest path: short-circuit by using push_lark=False AND a
        # very-large cooldown that prevents the marker check from passing... no, the
        # marker check writes ONLY when cooldown elapsed AND will_alert is True.
        # For a clean dry-run, run a minimal classification path manually:
        from datetime import datetime, timezone
        from app.workflows.cold_spell_alert import _classify_candidate
        from app.storage.config_loader import load_all_communities
        from app.core.audit import read_all_audit_events
        now = datetime.now(timezone.utc)
        candidates = []
        cache: dict[str, list[dict]] = {}
        for community in load_all_communities():
            if community.customer_id not in cache:
                try:
                    cache[community.customer_id] = read_all_audit_events(community.customer_id)
                except Exception:
                    cache[community.customer_id] = []
            candidates.append(_classify_candidate(community, cache[community.customer_id], now))
        result = {
            "dry_run": True,
            "candidates": [
                {
                    "community_id": c.community_id,
                    "display_name": c.display_name,
                    "state": c.state,
                    "hours_since_analyzed": c.hours_since_analyzed,
                    "would_alert": c.will_alert,
                }
                for c in candidates
            ],
        }
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            for c in candidates:
                marker = "🥶" if c.will_alert else ("📡" if c.state == "stale_signal" else ("❓" if c.state == "no_signal" else "✓"))
                hrs = f"{c.hours_since_analyzed:.1f}h" if c.hours_since_analyzed is not None else "  ?  "
                print(f"{marker} [{hrs:>6}] {c.community_id} — {c.display_name} ({c.state})")
        return 0

    result = run_heartbeat(alert_cooldown_hours=args.cooldown_hours)

    if args.json:
        print(json.dumps({
            "alerted": [c.community_id for c in result.alerted],
            "skipped_cooldown": [c.community_id for c in result.skipped_cooldown],
            "pushed_lark": result.pushed_lark,
            "candidates_count": len(result.candidates),
        }, ensure_ascii=False, indent=2))
        return 0

    if result.alerted:
        print(f"alerted: {', '.join(c.community_id for c in result.alerted)}")
    if result.skipped_cooldown:
        print(f"skipped (cooldown): {', '.join(c.community_id for c in result.skipped_cooldown)}")
    print(f"lark push: {'ok' if result.pushed_lark else 'skipped/failed'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
