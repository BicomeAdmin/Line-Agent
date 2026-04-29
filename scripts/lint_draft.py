"""CLI: lint a draft against the Taiwan chat register cheat-sheet.

Usage:
    python3 scripts/lint_draft.py "我以前也卡這個欸 後來改散盤就好多了"
    echo "我以前也卡這個欸" | python3 scripts/lint_draft.py
    python3 scripts/lint_draft.py --json "candidate text"

Exit codes:
    0  natural / ok    (verdict in {natural, ok})
    1  stiff           (50-79)
    2  broadcast       (< 50)
"""

from __future__ import annotations

import argparse
import json
import sys

import _bootstrap  # noqa: F401

from app.ai.draft_linter import score_draft


def main() -> int:
    parser = argparse.ArgumentParser(description="Lint a draft against the Taiwan chat register cheat-sheet.")
    parser.add_argument("text", nargs="?", help="The draft text. If omitted, read from stdin.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable.")
    args = parser.parse_args()

    if args.text is None:
        text = sys.stdin.read()
    else:
        text = args.text

    result = score_draft(text)

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        emoji = {"natural": "🌿", "ok": "🆗", "stiff": "⚠️ ", "broadcast": "🚨", "empty": "❌"}.get(result.verdict, "?")
        print(f"{emoji} score={result.score}  verdict={result.verdict}")
        print(f"   draft: {text.strip()}")
        bd = result.breakdown
        print(f"   stats: {bd.get('length')} chars · {bd.get('sentence_count')} 句 · particle_ratio={bd.get('particle_ratio')} · hedgers={bd.get('hedger_count')}")
        if result.issues:
            print("   issues:")
            for i in result.issues:
                print(f"     - {i}")
        if result.suggestions:
            print("   suggestions:")
            for s in result.suggestions:
                print(f"     - {s}")

    if result.verdict in ("natural", "ok"):
        return 0
    if result.verdict == "stiff":
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
