"""Preview every draft branch the rule-based decision engine could emit.

Read-only dry-run. Shows what the system WOULD draft under each scenario,
using the real persona/playbook for the target community. Nothing is sent.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

import _bootstrap  # noqa: F401

from app.ai.context_bundle import load_context_bundle
from app.ai.decision import decide_reply
from app.storage.config_loader import load_community_config


SCENARIOS = [
    ("cold_room", []),
    (
        "user_question (奶瓶)",
        [
            {"text": "大家用過幾款奶瓶後最推薦哪一個？想問有沒有比較容易清洗的"},
        ],
    ),
    (
        "user_question (投資)",
        [
            {"text": "請問大家最近有沒有在看什麼投資標的？"},
        ],
    ),
    (
        "user_question (一般)",
        [
            {"text": "有人知道台南哪裡可以買到比較好的攝影燈嗎？"},
        ],
    ),
    (
        "light_prompt (3 筆無問題)",
        [
            {"text": "今天天氣不錯"},
            {"text": "下午要去拍照"},
            {"text": "好喔，記得補水"},
        ],
    ),
    (
        "active_conversation (>=6 筆)",
        [{"text": f"假訊息第 {i+1} 句"} for i in range(8)],
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--customer-id", default="customer_a")
    parser.add_argument("--community-id", default="openchat_001")
    args = parser.parse_args()

    community = load_community_config(args.customer_id, args.community_id)
    bundle = load_context_bundle(args.customer_id, args.community_id)

    print(f"目標社群     : {community.display_name}")
    print(f"persona      : {bundle.persona_name}")
    print(f"persona 摘要 : {bundle.persona_text.strip().splitlines()[0] if bundle.persona_text.strip() else '(空)'}")
    print()
    print("=" * 80)

    for label, fake_messages in SCENARIOS:
        decision = decide_reply(fake_messages, bundle.persona_text, community.display_name)
        print(f"\n[{label}]")
        print(f"  action     : {decision.action}")
        print(f"  reason     : {decision.reason}")
        print(f"  confidence : {decision.confidence}")
        print(f"  should_send: {decision.should_send}")
        print(f"  draft      : {decision.draft}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
