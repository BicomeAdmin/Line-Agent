"""Dry-run the LLM composer on a community without writing review_store.

Use this BEFORE flipping `llm_compose_enabled: true` in a community
YAML — it runs the same selector + composer chain that watch_tick_inproc
runs, but doesn't stage a ReviewRecord, doesn't push a Lark card,
doesn't write any audit event. Pure observation of what the bot
WOULD have produced.

Three sources of "messages" to test against:
  --live     : take fresh chat from the device via ADB (requires
               LINE running, navigates to the community first)
  --import   : parse the latest chat_export under data/chat_exports/
               and use the last N
  --inline   : provide messages on stdin as JSON list of {sender, text}

Default is --import (offline, no device required).

Output: pretty summary of selector pick + composer decision/draft/
rationale. Prints to stdout, exit 0 if dry-run completed (regardless
of should_engage), exit 2 if composer was unavailable.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from app.ai.codex_compose import ComposerUnavailable, compose_via_codex
from app.ai.voice_profile_v2 import parse_voice_profile
from app.storage.config_loader import load_community_config
from app.storage.paths import customer_data_root, voice_profile_path
from app.workflows.member_fingerprint import get_member_fingerprint, load_member_fingerprints
from app.workflows.persona_context import get_persona_context
from app.workflows.reply_target_selector import select_reply_target


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run LLM composer (no review_store / no Lark / no audit).")
    parser.add_argument("--customer-id", default="customer_a")
    parser.add_argument("--community-id", required=True)
    parser.add_argument("--last-n", type=int, default=20)
    parser.add_argument("--source", choices=["live", "import", "inline"], default="import")
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()

    try:
        community = load_community_config(args.customer_id, args.community_id)
    except Exception as exc:  # noqa: BLE001
        print(f"❌ load_community_config failed: {exc}", file=sys.stderr)
        return 2

    print(f"=== dry_run_compose — {community.display_name} ({community.community_id}) ===")
    print(f"customer_id={args.customer_id}  community_id={args.community_id}  source={args.source}")
    print()

    # Voice profile
    vp_path = voice_profile_path(args.customer_id, args.community_id)
    vp = parse_voice_profile(args.customer_id, args.community_id, vp_path)
    print(f"voice_profile: {vp_path}")
    print(f"  is_complete={vp.is_complete}  missing={list(vp.missing_fields)}")
    print(f"  value_proposition: {vp.value_proposition or '(empty)'}")
    print(f"  route_mix: ip={vp.route_mix.ip:.0%} interest={vp.route_mix.interest:.0%} info={vp.route_mix.info:.0%}  → {vp.route_mix.dominant()}")
    print(f"  stage: {vp.stage or '(empty)'}  appetite: {vp.engagement_appetite}")
    print(f"  nickname: {vp.nickname or '(empty)'}")
    print()

    if not vp.is_complete:
        print(f"⚠️  voice_profile 未填完整，composer 會 refuse。先補：{list(vp.missing_fields)}")
        print("   仍會跑 selector 給你看，但不呼叫 codex。")
        print()

    # Source messages
    messages = _load_messages(args, community)
    if not messages:
        print("❌ no messages loaded — nothing to compose against.", file=sys.stderr)
        return 2
    print(f"loaded {len(messages)} messages (showing last {min(args.last_n, len(messages))}):")
    for m in messages[-args.last_n:]:
        s = str(m.get("sender") or "?")
        t = str(m.get("text") or "").strip()
        if t:
            print(f"  [{s}] {t[:80]}")
    print()

    # Selector
    persona = get_persona_context(args.customer_id, args.community_id)
    fingerprints = load_member_fingerprints(args.customer_id, args.community_id)
    decision = select_reply_target(
        messages,
        operator_persona=persona,
        member_fingerprints=fingerprints,
    )
    decision_dict = decision.to_dict()
    target = decision_dict.get("target")
    print("Selector:")
    if not target:
        print(f"  no target picked (skip_reason={decision_dict.get('skip_reason')})")
        print(f"  threshold={decision_dict.get('threshold')}")
        print()
        if decision_dict.get("considered"):
            top3 = sorted(decision_dict["considered"], key=lambda c: c.get("score", 0), reverse=True)[:3]
            print("  top 3 considered:")
            for c in top3:
                print(f"    score={c.get('score'):.2f} sender={c.get('sender')} text={(c.get('text') or '')[:50]}")
        return 0
    print(f"  picked: [{target.get('sender')}] \"{(target.get('text') or '')[:80]}\"")
    print(f"  score={target.get('score'):.2f}  threshold={decision_dict.get('threshold')}  reasons={target.get('reasons')}")
    print()

    if not vp.is_complete:
        print("→ composer skipped (voice_profile incomplete)")
        return 0

    # Composer
    target_fp = get_member_fingerprint(args.customer_id, args.community_id, str(target.get("sender") or ""))
    recent_self_posts = [
        str(p.get("text") or "")
        for p in (persona.get("recent_self_posts") or [])
        if isinstance(p, dict)
    ]
    print(f"Composer (codex, timeout={args.timeout}s):")
    try:
        out = compose_via_codex(
            voice_profile=vp,
            community_name=community.display_name,
            target_sender=str(target.get("sender") or ""),
            target_message=str(target.get("text") or ""),
            target_score=float(target.get("score") or 0.0),
            target_threshold=float(decision_dict.get("threshold") or 2.0),
            target_reasons=list(target.get("reasons") or []),
            target_fingerprint=target_fp,
            thread_excerpt=messages[-8:],
            recent_self_posts=recent_self_posts,
            timeout_seconds=args.timeout,
        )
    except ComposerUnavailable as exc:
        print(f"❌ ComposerUnavailable: {exc}")
        return 2

    print(f"  should_engage: {out.should_engage}")
    print(f"  rationale: {out.rationale}")
    print(f"  confidence: {out.confidence:.2f}")
    print(f"  off_limits_hit: {out.off_limits_hit}")
    if out.draft:
        print()
        print("  draft ↓")
        for line in out.draft.splitlines():
            print(f"    {line}")
        # Lint the draft against the Taiwan chat register cheat-sheet
        # so we can spot stiff / broadcast outputs before they hit
        # production.
        from app.ai.draft_linter import score_draft
        lint = score_draft(out.draft)
        print()
        emoji = {"natural": "🌿", "ok": "🆗", "stiff": "⚠️ ", "broadcast": "🚨"}.get(lint.verdict, "?")
        print(f"  lint: {emoji} score={lint.score}  verdict={lint.verdict}")
        if lint.issues:
            for i in lint.issues:
                print(f"    - {i}")
    print()
    print("(no review_store written, no Lark sent, no audit logged)")
    return 0


def _load_messages(args, community) -> list[dict]:
    if args.source == "inline":
        raw = sys.stdin.read().strip()
        if not raw:
            return []
        loaded = json.loads(raw)
        if not isinstance(loaded, list):
            raise SystemExit("inline source: stdin must be JSON list")
        return loaded
    if args.source == "live":
        from app.adb.client import AdbClient
        from app.storage.paths import default_raw_xml_path
        from app.workflows.openchat_navigate import navigate_to_openchat
        from app.workflows.read_chat import read_recent_chat
        nav = navigate_to_openchat(args.customer_id, args.community_id, overall_timeout_seconds=20.0)
        if nav.get("status") != "ok":
            raise SystemExit(f"navigate failed: {nav.get('reason')}")
        return read_recent_chat(
            AdbClient(device_id=community.device_id),
            default_raw_xml_path(args.customer_id),
            limit=args.last_n,
        )
    # import (default)
    from app.workflows.chat_export_import import parse_line_export
    export_dir = customer_data_root(args.customer_id) / "chat_exports"
    candidates = sorted(export_dir.glob(f"{args.community_id}__*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise SystemExit(f"no chat_export found at {export_dir}/{args.community_id}__*.txt")
    print(f"  (using import: {candidates[0].name})")
    parsed = parse_line_export(candidates[0])
    return [{"sender": m.sender, "text": m.text, "position": i} for i, m in enumerate(parsed[-args.last_n:])]


if __name__ == "__main__":
    sys.exit(main())
