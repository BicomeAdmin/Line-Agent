"""Auto-navigate the emulator into a target OpenChat by name.

Replaces "operator manually opens the chat" once a community is configured.
Run this before send / patrol / acceptance verification when you don't know
whether LINE is currently on the right room.

Example:
    python3 scripts/navigate_to_openchat.py customer_a openchat_002

Returns JSON with status (ok/blocked) and a step-by-step trace.
"""

from __future__ import annotations

import argparse
import json

import _bootstrap  # noqa: F401

from app.workflows.openchat_navigate import navigate_to_openchat


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("customer_id")
    parser.add_argument("community_id")
    parser.add_argument("--timeout-seconds", type=float, default=25.0)
    args = parser.parse_args()

    result = navigate_to_openchat(
        args.customer_id,
        args.community_id,
        overall_timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
