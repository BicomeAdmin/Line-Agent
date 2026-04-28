from __future__ import annotations

from app.adb.client import AdbClient
from app.adb.devices import foreground_package


LINE_PACKAGE = "jp.naver.line.android"


def check_current_app(client: AdbClient) -> bool:
    package = foreground_package(client)
    if package is not None:
        return package == LINE_PACKAGE
    return LINE_PACKAGE in current_window_dump(client)


def current_window_dump(client: AdbClient) -> str:
    result = client.shell("dumpsys", "window", "windows", check=False)
    return f"{result.stdout}\n{result.stderr}"


def open_line(client: AdbClient) -> None:
    # Prefer `am start` with MAIN/LAUNCHER intent so we don't depend on a fixed
    # activity class name — LINE renames its launcher component between versions.
    # Monkey returned non-zero on this emulator (no physical keys), so we avoid it.
    client.shell(
        "am", "start",
        "-a", "android.intent.action.MAIN",
        "-c", "android.intent.category.LAUNCHER",
        "-n", f"{LINE_PACKAGE}/.activity.SplashActivity",
        check=False,
    )
