from __future__ import annotations

import sys

import _bootstrap  # noqa: F401

from app.adb.client import AdbClient, AdbError
from app.adb.line_app import check_current_app, current_window_dump


def main() -> int:
    device_id = sys.argv[1] if len(sys.argv) > 1 else None
    client = AdbClient(device_id=device_id)
    try:
        in_line = check_current_app(client)
    except AdbError as exc:
        print(f"Failed to inspect current app: {exc}")
        return 2

    print("LINE_ACTIVE" if in_line else "LINE_INACTIVE")
    if not in_line:
        preview = current_window_dump(client).splitlines()[:20]
        for line in preview:
            print(line)
    return 0 if in_line else 1


if __name__ == "__main__":
    raise SystemExit(main())
