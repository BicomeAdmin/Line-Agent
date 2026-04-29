from __future__ import annotations

import argparse
import json
import sys

import _bootstrap  # noqa: F401

from app.workflows.onboarding_status import build_onboarding_report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Show onboarding readiness for each enabled community.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    parser.add_argument(
        "--auto-watch-only",
        action="store_true",
        help="Only list communities with auto_watch enabled",
    )
    args = parser.parse_args()

    report = build_onboarding_report()
    rows = report.communities
    if args.auto_watch_only:
        rows = tuple(c for c in rows if c.auto_watch_enabled)

    if args.json:
        print(json.dumps({
            "communities": [
                {
                    "customer_id": c.customer_id,
                    "community_id": c.community_id,
                    "display_name": c.display_name,
                    "auto_watch_enabled": c.auto_watch_enabled,
                    "ready_for_auto_watch": c.ready_for_auto_watch,
                    "critical_gaps": list(c.critical_gaps),
                    "soft_gaps": list(c.soft_gaps),
                    "voice_profile_chars": c.voice_profile_chars,
                }
                for c in rows
            ],
            "critical_count": report.critical_count,
            "soft_count": report.soft_count,
        }, ensure_ascii=False, indent=2))
        return 0 if not report.auto_watch_with_gaps else 1

    has_gap = False
    for c in rows:
        marker = "✅" if c.ready_for_auto_watch else "⚠️ "
        aw = "auto_watch" if c.auto_watch_enabled else "manual    "
        print(f"{marker} [{aw}] {c.community_id} — {c.display_name}")
        if c.critical_gaps:
            print(f"     critical: {', '.join(c.critical_gaps)}")
            has_gap = True
        if c.soft_gaps:
            print(f"     soft:     {', '.join(c.soft_gaps)}")
        if c.has_voice_profile:
            print(f"     voice profile: {c.voice_profile_chars} chars")

    print()
    print(f"summary: {report.critical_count} community(ies) with critical gaps, {report.soft_count} with soft gaps")
    if report.auto_watch_with_gaps:
        print("⚠️  auto_watch is enabled on community(ies) with critical gaps:")
        for c in report.auto_watch_with_gaps:
            print(f"   - {c.community_id}: {', '.join(c.critical_gaps)}")
        return 1
    return 0 if not has_gap else 0


if __name__ == "__main__":
    raise SystemExit(main())
