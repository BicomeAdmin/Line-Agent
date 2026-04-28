from __future__ import annotations

import argparse
import json

import _bootstrap  # noqa: F401

from app.workflows.play_store_install import open_line_in_play_store


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("device_id")
    args = parser.parse_args()
    result = open_line_in_play_store(args.device_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
