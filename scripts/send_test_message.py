from __future__ import annotations

import argparse

import _bootstrap  # noqa: F401

from app.adb.client import AdbClient, AdbError
from app.adb.input import tap_type_send


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("text")
    parser.add_argument("--device-id")
    parser.add_argument("--input-x", type=int, required=True)
    parser.add_argument("--input-y", type=int, required=True)
    parser.add_argument("--send-x", type=int, required=True)
    parser.add_argument("--send-y", type=int, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        result = tap_type_send(
            AdbClient(device_id=args.device_id),
            args.text,
            input_x=args.input_x,
            input_y=args.input_y,
            send_x=args.send_x,
            send_y=args.send_y,
            dry_run=args.dry_run,
        )
    except AdbError as exc:
        print(f"Failed to send test message: {exc}")
        return 2

    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
