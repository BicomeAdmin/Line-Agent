from __future__ import annotations

import argparse
import json

import _bootstrap  # noqa: F401

from app.workflows.prepare_line_session import prepare_line_session


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("device_id")
    parser.add_argument("--boot-timeout", type=int, default=120)
    args = parser.parse_args()
    result = prepare_line_session(args.device_id, boot_timeout_seconds=args.boot_timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"ok", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
