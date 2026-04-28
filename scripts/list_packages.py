from __future__ import annotations

import argparse

import _bootstrap  # noqa: F401

from app.adb.client import AdbClient, AdbError
from app.adb.devices import list_packages


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("device_id", nargs="?", default=None)
    parser.add_argument("--contains", default=None)
    args = parser.parse_args()

    client = AdbClient(device_id=args.device_id)
    try:
        packages = list_packages(client, substring=args.contains)
    except AdbError as exc:
        print(f"Failed to list packages: {exc}")
        return 2

    for package in packages:
        print(package)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

