from __future__ import annotations

from app.adb.client import AdbClient
from app.adb.devices import boot_completed, foreground_package, package_installed
from app.adb.line_app import LINE_PACKAGE, check_current_app


def get_device_status(device_id: str) -> dict[str, object]:
    client = AdbClient(device_id=device_id)
    return {
        "device_id": device_id,
        "boot_completed": boot_completed(client),
        "foreground_package": foreground_package(client),
        "line_installed": package_installed(client, LINE_PACKAGE),
        "line_active": check_current_app(client),
    }
