from __future__ import annotations

import json
from pathlib import Path

import _bootstrap  # noqa: F401

from app.lark.verification import handle_url_verification


def main() -> int:
    payload = json.loads(Path("samples/lark/event_url_verification.sample.json").read_text(encoding="utf-8"))
    print(json.dumps(handle_url_verification(payload), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

