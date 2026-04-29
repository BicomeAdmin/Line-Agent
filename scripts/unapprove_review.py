"""Unapprove (recall) a review the operator regrets.

Two scenarios:
  - Active review (pending / edit_required / pending_reapproval): mark as
    recalled before any send happens.
  - Already-sent review: recall is audit-only (we cannot un-send via LINE
    API) — the script will warn that the message in the room is unchanged.

Example:
    python3 scripts/unapprove_review.py review-1f848721330e --reason "wrong tone"
"""

from __future__ import annotations

import argparse
import json
import sys

import _bootstrap  # noqa: F401

from app.workflows.unapprove import UnapproveError, unapprove_review


def main() -> int:
    parser = argparse.ArgumentParser(description="Mark a review as recalled (operator regret).")
    parser.add_argument("review_id")
    parser.add_argument("--reason", help="Free-text reason recorded in audit log")
    parser.add_argument("--json", action="store_true", help="Emit JSON result")
    args = parser.parse_args()

    try:
        result = unapprove_review(args.review_id, reason=args.reason)
    except UnapproveError as exc:
        print(f"unapprove failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({
            "review_id": result.review_id,
            "previous_status": result.previous_status,
            "new_status": result.new_status,
            "sent_message_irreversible": result.sent_message_irreversible,
            "reason": result.reason,
        }, ensure_ascii=False, indent=2))
        return 0

    print(f"recalled: {result.review_id}")
    print(f"  previous status: {result.previous_status} → {result.new_status}")
    if result.reason:
        print(f"  reason: {result.reason}")
    if result.sent_message_irreversible:
        print()
        print("⚠️  this review was already 'sent' — the message is in the LINE room.")
        print("    LINE has no API to delete it; recall is audit-only.")
        print("    To remove from the room, long-press the message in LINE app and 收回.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
