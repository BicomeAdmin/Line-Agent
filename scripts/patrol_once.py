from __future__ import annotations

import argparse
import json

import _bootstrap  # noqa: F401

from app.workflows.patrol import patrol_device


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("device_id")
    args = parser.parse_args()

    result = patrol_device(args.device_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
