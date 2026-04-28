from __future__ import annotations

import argparse
import json

import _bootstrap  # noqa: F401

from app.workflows.device_recovery import ensure_device_ready


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("device_id")
    parser.add_argument("--wait-timeout", type=int, default=60)
    args = parser.parse_args()
    result = ensure_device_ready(args.device_id, wait_timeout_seconds=args.wait_timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
