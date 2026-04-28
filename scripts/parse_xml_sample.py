from __future__ import annotations

import json
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from app.parsing.line_chat_parser import parse_line_chat


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "samples/xml/line_chat_dump.sample.xml")
    messages = [message.to_dict() for message in parse_line_chat(path.read_text(encoding="utf-8"))]
    print(json.dumps(messages, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
