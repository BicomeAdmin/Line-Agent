from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.config import settings


class AdbError(RuntimeError):
    pass


@dataclass(frozen=True)
class AdbResult:
    stdout: str
    stderr: str
    returncode: int


class AdbClient:
    def __init__(self, adb_path: str | None = None, device_id: str | None = None, timeout: int = 20) -> None:
        self.adb_path = adb_path or settings.adb_path
        self.device_id = device_id
        self.timeout = timeout

    def is_available(self) -> bool:
        return self.resolve_adb_path() is not None

    def resolve_adb_path(self) -> str | None:
        discovered = shutil.which(self.adb_path)
        if discovered:
            return discovered

        for path in _candidate_adb_paths():
            if path.exists():
                return str(path)
        return None

    def command(self, *args: str, check: bool = True) -> AdbResult:
        adb_path = self.resolve_adb_path()
        if adb_path is None:
            raise AdbError(f"ADB executable not found: {self.adb_path}")

        command = [adb_path]
        if self.device_id:
            command.extend(["-s", self.device_id])
        command.extend(args)

        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=self.timeout,
            check=False,
        )
        result = AdbResult(completed.stdout, completed.stderr, completed.returncode)
        if check and completed.returncode != 0:
            raise AdbError(completed.stderr.strip() or completed.stdout.strip() or f"ADB failed: {command}")
        return result

    def devices(self) -> list[str]:
        result = self.command("devices")
        lines = result.stdout.splitlines()[1:]
        return [line.split()[0] for line in lines if "\tdevice" in line]

    def shell(self, *args: str, check: bool = True) -> AdbResult:
        return self.command("shell", *args, check=check)

    def pull(self, remote: str, local: str) -> AdbResult:
        return self.command("pull", remote, local)

    def install(self, apk_path: str, replace: bool = True) -> AdbResult:
        args = ["install"]
        if replace:
            args.append("-r")
        args.append(apk_path)
        return self.command(*args)

    def uninstall(self, package_name: str, keep_data: bool = False) -> AdbResult:
        args = ["uninstall"]
        if keep_data:
            args.append("-k")
        args.append(package_name)
        return self.command(*args)


def _candidate_adb_paths() -> list[Path]:
    home = Path.home()
    return [
        home / "Library/Android/sdk/platform-tools/adb",
        Path("/opt/homebrew/bin/adb"),
        Path("/usr/local/bin/adb"),
    ]
