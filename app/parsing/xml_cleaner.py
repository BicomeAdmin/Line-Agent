from __future__ import annotations

import re
import xml.etree.ElementTree as ET


SYSTEM_TEXT_PATTERNS = [
    re.compile(r"^\d{1,2}:\d{2}$"),
    re.compile(r"^\d{1,3}%$"),
    re.compile(r"^(LINE|OpenChat|Chats|Home|Today|昨天|今天)$", re.IGNORECASE),
]


def extract_text_nodes(xml_text: str) -> list[str]:
    root = ET.fromstring(xml_text)
    texts: list[str] = []
    for node in root.iter("node"):
        text = (node.attrib.get("text") or "").strip()
        if not text:
            continue
        if is_system_text(text):
            continue
        texts.append(text)
    return texts


def is_system_text(text: str) -> bool:
    normalized = text.strip()
    if len(normalized) == 1 and normalized in {"↵", "⌫"}:
        return True
    return any(pattern.search(normalized) for pattern in SYSTEM_TEXT_PATTERNS)


_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


def _parse_bounds(raw: str) -> tuple[int, int, int, int] | None:
    match = _BOUNDS_RE.match(raw.strip())
    if not match:
        return None
    return tuple(int(g) for g in match.groups())  # type: ignore[return-value]


def extract_clickable_nodes(xml_text: str) -> list[dict[str, object]]:
    """Yield clickable nodes with bounds, text, content-desc, and center coordinates.

    Used by search/navigation workflows to locate tap targets by text or desc.
    """

    root = ET.fromstring(xml_text)
    items: list[dict[str, object]] = []
    for node in root.iter("node"):
        attrs = node.attrib
        if attrs.get("clickable") != "true":
            continue
        bounds = _parse_bounds(attrs.get("bounds") or "")
        if bounds is None:
            continue
        cx = (bounds[0] + bounds[2]) // 2
        cy = (bounds[1] + bounds[3]) // 2
        items.append(
            {
                "text": (attrs.get("text") or "").strip(),
                "content_desc": (attrs.get("content-desc") or "").strip(),
                "resource_id": attrs.get("resource-id") or "",
                "class": attrs.get("class") or "",
                "bounds": list(bounds),
                "center": [cx, cy],
            }
        )
    return items


def extract_all_text_nodes_with_bounds(xml_text: str) -> list[dict[str, object]]:
    """Every node that has non-empty text or content-desc, with bounds.

    Useful when a target chat row's tappable parent is the row container, but the
    text is on a child TextView; the navigation workflow walks parents to find the
    tappable ancestor.
    """

    root = ET.fromstring(xml_text)
    items: list[dict[str, object]] = []
    for node in root.iter("node"):
        attrs = node.attrib
        text = (attrs.get("text") or "").strip()
        desc = (attrs.get("content-desc") or "").strip()
        if not text and not desc:
            continue
        bounds = _parse_bounds(attrs.get("bounds") or "")
        if bounds is None:
            continue
        cx = (bounds[0] + bounds[2]) // 2
        cy = (bounds[1] + bounds[3]) // 2
        items.append(
            {
                "text": text,
                "content_desc": desc,
                "resource_id": attrs.get("resource-id") or "",
                "class": attrs.get("class") or "",
                "clickable": attrs.get("clickable") == "true",
                "bounds": list(bounds),
                "center": [cx, cy],
            }
        )
    return items

