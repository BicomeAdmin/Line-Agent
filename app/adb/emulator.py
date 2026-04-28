from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from app.config import settings


class EmulatorError(RuntimeError):
    pass


def resolve_emulator_binary() -> str:
    discovered = shutil.which("emulator")
    if discovered:
        return discovered

    candidate = Path(settings.android_sdk_root) / "emulator" / "emulator"
    if candidate.exists():
        return str(candidate)
    raise EmulatorError("Android emulator binary not found.")


def list_avds() -> list[str]:
    completed = subprocess.run(
        [resolve_emulator_binary(), "-list-avds"],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise EmulatorError(completed.stderr.strip() or "Failed to list AVDs.")
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def running_avd_names() -> list[str]:
    completed = subprocess.run(
        ["ps", "-ax", "-o", "command="],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise EmulatorError(completed.stderr.strip() or "Failed to inspect running processes.")
    avds: list[str] = []
    for line in completed.stdout.splitlines():
        parts = line.split()
        if "emulator" not in line or "-avd" not in parts:
            continue
        index = parts.index("-avd")
        if index + 1 < len(parts):
            avds.append(parts[index + 1])
    return avds


def start_avd(avd_name: str, no_snapshot: bool = True) -> str:
    if avd_name not in list_avds():
        raise EmulatorError(f"AVD not found: {avd_name}")

    command = [resolve_emulator_binary(), "-avd", avd_name]
    if no_snapshot:
        command.append("-no-snapshot")
    command.append("-no-metrics")
    environment = {**os.environ, "ANDROID_SDK_ROOT": settings.android_sdk_root}
    subprocess.Popen(
        command,
        env=environment,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )  # noqa: S603
    return avd_name
