"""On-demand visibility into the autonomous reply pipeline.

For each currently-watched community (or a specific one), run the
decision pipeline read-only and report:
  - Who's in the recent chat
  - Each candidate's score + reasons
  - The selector's verdict (compose / skip + why)
  - If composing, which member's fingerprint was loaded for style

Mostly read-only — does NOT actually compose or push cards. Use to
audit "what would my bot do right now?" without waiting for the
next daemon tick.

Usage:
  python3 scripts/preview_autonomous.py                # all active watches
  python3 scripts/preview_autonomous.py --community openchat_003
  python3 scripts/preview_autonomous.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import _bootstrap  # noqa: F401

from app.adb.client import AdbClient
from app.storage.config_loader import load_community_config
from app.storage.paths import default_raw_xml_path
from app.storage.watches import list_active_watches_all_customers
from app.workflows.member_fingerprint import (
    get_member_fingerprint,
    load_member_fingerprints,
)
from app.workflows.openchat_navigate import navigate_to_openchat
from app.workflows.persona_context import get_persona_context
from app.workflows.read_chat import read_recent_chat
from app.workflows.reply_target_selector import select_reply_target


def preview_one(customer_id: str, community_id: str) -> dict[str, Any]:
    try:
        community = load_community_config(customer_id, community_id)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": f"community_lookup_failed:{exc}"}

    nav = navigate_to_openchat(customer_id, community_id, overall_timeout_seconds=20.0)
    if nav.get("status") != "ok":
        return {"status": "error", "reason": f"navigate_failed:{nav.get('reason')}"}

    try:
        messages = read_recent_chat(
            AdbClient(device_id=community.device_id),
            default_raw_xml_path(customer_id),
            limit=20,
        )
    except RuntimeError as exc:
        return {"status": "error", "reason": f"read_failed:{exc}"}

    persona = get_persona_context(customer_id, community_id)
    fingerprints_bundle = load_member_fingerprints(customer_id, community_id)
    decision = select_reply_target(
        messages,
        operator_persona=persona,
        member_fingerprints=fingerprints_bundle,
    )
    target_fp = None
    if decision.target is not None:
        target_fp = get_member_fingerprint(customer_id, community_id, decision.target.sender)

    return {
        "status": "ok",
        "community_id": community_id,
        "community_name": community.display_name,
        "messages_read": len(messages),
        "persona_summary": persona.get("summary_zh") if persona.get("status") == "ok" else None,
        "decision": decision.to_dict(),
        "target_fingerprint": target_fp,
        "messages_tail": [
            {"sender": m.get("sender"), "text": (m.get("text") or "")[:80]}
            for m in messages[-10:]
        ],
    }


def render_text(report: dict[str, Any]) -> str:
    out: list[str] = []
    cid = report.get("community_id")
    cname = report.get("community_name", "")
    out.append(f"━━━ {cid}  {cname} ━━━")
    if report.get("status") != "ok":
        out.append(f"  ❌ {report.get('reason')}")
        return "\n".join(out)

    out.append(f"  📊 讀到 {report.get('messages_read')} 則訊息")
    persona_summary = report.get("persona_summary")
    if persona_summary:
        out.append(f"  👤 {persona_summary}")
    out.append("")

    decision = report.get("decision") or {}
    target = decision.get("target")
    threshold = decision.get("threshold")

    out.append(f"  最近對話末段 (最後 10 則):")
    for m in (report.get("messages_tail") or [])[-10:]:
        sender = (m.get("sender") or "")[:20]
        text = (m.get("text") or "").replace("\n", " ")[:60]
        out.append(f"    · {sender:20} {text}")
    out.append("")

    if target:
        fp = report.get("target_fingerprint") or {}
        avg = fp.get("avg_length") or "?"
        ends = fp.get("top_ending_particles") or []
        emoji = fp.get("emoji_rate") or 0
        out.append(f"  ✅ 自動決策：回覆「{target.get('sender')}」")
        out.append(f"     信心 {target.get('score')} (門檻 {threshold})")
        out.append(f"     理由: {' / '.join(target.get('reasons', []))}")
        out.append(f"     對方說: 「{(target.get('text') or '')[:80]}」")
        if fp:
            out.append(f"     對方風格: 平均 {avg} 字、句尾「{'/'.join(ends[:3]) if ends else '無偏好'}」、emoji 率 {emoji}")
            out.append(f"     → 草稿應控制在 {int(float(avg) * 0.7) if isinstance(avg, (int,float)) else '?'}–{int(float(avg) * 1.3) if isinstance(avg, (int,float)) else '?'} 字之間")
    else:
        out.append(f"  ⏸  自動決策：略過（{decision.get('skip_reason')}）")
        considered = decision.get("considered") or []
        actionable_top = [c for c in considered if c.get("actionable")]
        if not actionable_top and considered:
            top3 = sorted(considered, key=lambda c: c.get("score", 0), reverse=True)[:3]
            out.append(f"     最高分但未過門檻 (門檻 {threshold}):")
            for c in top3:
                out.append(f"       · {c.get('sender'):20} {c.get('score'):>5.2f}  {c.get('reasons')}")

    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--customer-id", default="customer_a")
    parser.add_argument("--community", default=None, help="Specific community_id; default is all watched")
    parser.add_argument("--json", action="store_true", help="Raw JSON output")
    args = parser.parse_args()

    if args.community:
        targets = [(args.customer_id, args.community)]
    else:
        targets = []
        for w in list_active_watches_all_customers():
            cid = w.get("customer_id")
            community_id = w.get("community_id")
            if isinstance(cid, str) and isinstance(community_id, str):
                targets.append((cid, community_id))
        if not targets:
            print("沒有 active watch — 請先 start_watch 或用 --community 指定", file=sys.stderr)
            return 1

    reports = [preview_one(cid, comm) for cid, comm in targets]

    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
    else:
        print()
        print("🤖 Project Echo — 自動決策預覽")
        print("════════════════════════════════")
        for r in reports:
            print()
            print(render_text(r))
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
