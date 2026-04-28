from __future__ import annotations

from dataclasses import asdict, dataclass

from app.parsing.xml_cleaner import extract_text_nodes


@dataclass(frozen=True)
class ChatMessage:
    sender: str
    text: str
    position: int
    source: str = "uiautomator"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def parse_line_chat(xml_text: str, limit: int = 10) -> list[ChatMessage]:
    texts = extract_text_nodes(xml_text)
    messages = [
        ChatMessage(sender="unknown", text=text, position=index)
        for index, text in enumerate(texts)
        if _looks_like_chat_message(text)
    ]
    return messages[-limit:]


def _looks_like_chat_message(text: str) -> bool:
    if len(text) <= 1:
        return False
    if text in {"搜尋", "設定", "公告", "傳送", "送出"}:
        return False
    return True

