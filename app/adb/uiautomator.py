from __future__ import annotations

import time
from pathlib import Path

from app.adb.client import AdbClient


REMOTE_XML_PATH = "/sdcard/window_dump.xml"


def dump_ui_xml(client: AdbClient, output_path: str | Path) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    result = client.shell("uiautomator", "dump", REMOTE_XML_PATH)
    remote_path = _extract_remote_path(result.stdout) or REMOTE_XML_PATH
    _wait_for_remote_file(client, remote_path)
    client.pull(remote_path, str(target))
    return target


def _extract_remote_path(stdout: str) -> str | None:
    marker = "UI hierchary dumped to:"
    for line in stdout.splitlines():
        if marker in line:
            return line.split(marker, 1)[1].strip()
    return None


def _wait_for_remote_file(client: AdbClient, remote_path: str, attempts: int = 10, delay_seconds: float = 0.2) -> None:
    for _ in range(attempts):
        result = client.shell("ls", remote_path, check=False)
        if result.returncode == 0:
            return
        time.sleep(delay_seconds)
