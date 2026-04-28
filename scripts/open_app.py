from __future__ import annotations

import argparse

import _bootstrap  # noqa: F401

from app.adb.client import AdbClient, AdbError
from app.adb.devices import open_package, wake_and_unlock


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package_name")
    parser.add_argument("--device-id", default=None)
    args = parser.parse_args()

    client = AdbClient(device_id=args.device_id)
    try:
        wake_and_unlock(client)
        target = open_package(client, args.package_name)
    except AdbError as exc:
        print(f"Failed to open app: {exc}")
        return 2

    print(f"OPENED {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
