from __future__ import annotations

import argparse
import json

import _bootstrap  # noqa: F401

from app.workflows.calibration_update import save_community_calibration


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("customer_id")
    parser.add_argument("community_id")
    parser.add_argument("--input-x", type=int, required=True)
    parser.add_argument("--input-y", type=int, required=True)
    parser.add_argument("--send-x", type=int, required=True)
    parser.add_argument("--send-y", type=int, required=True)
    parser.add_argument("--note", default=None)
    args = parser.parse_args()

    print(
        json.dumps(
            save_community_calibration(
                args.customer_id,
                args.community_id,
                input_x=args.input_x,
                input_y=args.input_y,
                send_x=args.send_x,
                send_y=args.send_y,
                note=args.note,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
