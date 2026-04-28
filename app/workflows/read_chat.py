from __future__ import annotations

from pathlib import Path

from app.adb.client import AdbClient
from app.adb.line_app import check_current_app
from app.adb.uiautomator import dump_ui_xml
from app.parsing.line_chat_parser import parse_line_chat


def read_recent_chat(client: AdbClient, output_path: str | Path, limit: int = 10) -> list[dict[str, object]]:
    if not check_current_app(client):
        raise RuntimeError("Current emulator screen is not LINE. Open LINE OpenChat before reading chat.")
    xml_path = dump_ui_xml(client, output_path)
    messages = parse_line_chat(xml_path.read_text(encoding="utf-8"), limit=limit)
    return [message.to_dict() for message in messages]
