from __future__ import annotations

import argparse
import json

import _bootstrap  # noqa: F401

from app.workflows.play_store_install import wait_for_line_installed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("device_id")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--poll-seconds", type=int, default=5)
    args = parser.parse_args()
    result = wait_for_line_installed(
        args.device_id,
        timeout_seconds=args.timeout_seconds,
        poll_seconds=args.poll_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
