from __future__ import annotations

import time

from app.adb.client import AdbClient, AdbError


def boot_completed(client: AdbClient) -> bool:
    return getprop(client, "sys.boot_completed") == "1"


def getprop(client: AdbClient, key: str) -> str:
    result = client.shell("getprop", key, check=False)
    return result.stdout.strip()


def wait_for_boot(client: AdbClient, timeout_seconds: int = 120, poll_interval: float = 2.0) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if boot_completed(client):
            return True
        time.sleep(poll_interval)
    return False


def wake_and_unlock(client: AdbClient) -> None:
    client.shell("input", "keyevent", "KEYCODE_WAKEUP", check=False)
    client.shell("wm", "dismiss-keyguard", check=False)
    client.shell("input", "keyevent", "82", check=False)


def foreground_package(client: AdbClient) -> str | None:
    result = client.shell("dumpsys", "activity", "activities", check=False)
    for line in result.stdout.splitlines():
        candidate = _extract_package_from_activity_line(line)
        if candidate is not None:
            return candidate
    return None


def list_packages(client: AdbClient, substring: str | None = None) -> list[str]:
    result = client.shell("pm", "list", "packages", check=False)
    packages = [line.removeprefix("package:").strip() for line in result.stdout.splitlines() if line.startswith("package:")]
    if substring:
        needle = substring.lower()
        packages = [package for package in packages if needle in package.lower()]
    return packages


def package_installed(client: AdbClient, package_name: str) -> bool:
    return package_name in list_packages(client)


def launcher_activity(client: AdbClient, package_name: str) -> str | None:
    result = client.shell(
        "cmd",
        "package",
        "resolve-activity",
        "--brief",
        "-c",
        "android.intent.category.LAUNCHER",
        package_name,
        check=False,
    )
    candidates = [line.strip() for line in result.stdout.splitlines() if "/" in line]
    return candidates[-1] if candidates else None


def open_package(client: AdbClient, package_name: str) -> str:
    component = launcher_activity(client, package_name)
    if component is not None:
        client.shell("am", "start", "-n", component)
        return component

    client.shell("monkey", "-p", package_name, "-c", "android.intent.category.LAUNCHER", "1")
    return package_name


def _extract_package_from_activity_line(line: str) -> str | None:
    markers = ("topResumedActivity=", "mResumedActivity:", "ResumedActivity:")
    for marker in markers:
        if marker not in line:
            continue
        tail = line.split(marker, 1)[1]
        for token in tail.split():
            if "/" in token:
                package = token.split("/", 1)[0]
                if "." in package:
                    return package
    return None
