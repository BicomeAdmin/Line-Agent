from __future__ import annotations

import argparse
import json

import _bootstrap  # noqa: F401

from app.lark.client import LarkClient, LarkClientError
from app.lark.status_cards import build_system_status_card
from app.workflows.system_status import get_system_status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("receive_id")
    parser.add_argument("--receive-id-type", default="chat_id")
    args = parser.parse_args()

    try:
        response = LarkClient().send_card(
            receive_id=args.receive_id,
            receive_id_type=args.receive_id_type,
            card=build_system_status_card(get_system_status()),
        )
    except LarkClientError as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    print(json.dumps({"status": "ok", "data": response.get("data", {})}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
