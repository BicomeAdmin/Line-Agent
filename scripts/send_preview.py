from __future__ import annotations

import argparse
import json

import _bootstrap  # noqa: F401

from app.workflows.send_preview import preview_send


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("customer_id")
    parser.add_argument("community_id")
    parser.add_argument("text")
    args = parser.parse_args()
    print(
        json.dumps(
            preview_send(args.customer_id, args.community_id, args.text),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
