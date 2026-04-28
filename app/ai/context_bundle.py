from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from app.storage.config_loader import load_community_config, load_customer_config
from app.storage.paths import customer_root


@dataclass(frozen=True)
class ContextBundle:
    customer_id: str
    customer_name: str
    community_id: str
    community_name: str
    persona_name: str
    persona_text: str
    playbook_text: str
    safety_rules: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def load_context_bundle(customer_id: str, community_id: str) -> ContextBundle:
    customer = load_customer_config(customer_id)
    community = load_community_config(customer_id, community_id)
    persona_text = _read_optional(customer_root(customer_id) / "souls" / f"{community.persona}.md")
    playbook_text = _read_optional(customer_root(customer_id) / "playbooks" / "review_rules.md")
    safety_rules = (
        "禁止跨客戶引用任何聊天內容或 persona 資訊。",
        "避免醫療、投資、法律等高風險斷言。",
        "若對話自然熱絡，優先不介入。",
        "所有正式發送都需經過人工審核。",
    )
    return ContextBundle(
        customer_id=customer_id,
        customer_name=customer.display_name,
        community_id=community.community_id,
        community_name=community.display_name,
        persona_name=community.persona,
        persona_text=persona_text,
        playbook_text=playbook_text,
        safety_rules=safety_rules,
    )


def build_prompt_context(bundle: ContextBundle, messages: list[dict[str, object]]) -> dict[str, object]:
    recent_messages = [str(item.get("text", "")).strip() for item in messages if str(item.get("text", "")).strip()]
    return {
        "customer": bundle.customer_name,
        "community": bundle.community_name,
        "persona": bundle.persona_text,
        "playbook": bundle.playbook_text,
        "safety_rules": list(bundle.safety_rules),
        "recent_messages": recent_messages[-20:],
    }


def _read_optional(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")
