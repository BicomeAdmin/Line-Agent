from __future__ import annotations

from dataclasses import dataclass


@dataclass
class _Line:
    indent: int
    content: str


def load_yaml(text: str) -> object:
    lines = [_Line(_indent_of(raw), raw.strip()) for raw in text.splitlines() if raw.strip() and not raw.lstrip().startswith("#")]
    if not lines:
        return {}
    value, index = _parse_block(lines, 0, lines[0].indent)
    if index != len(lines):
        raise ValueError("Unexpected trailing YAML content.")
    return value


def _parse_block(lines: list[_Line], index: int, indent: int) -> tuple[object, int]:
    if lines[index].content.startswith("- "):
        return _parse_list(lines, index, indent)
    return _parse_mapping(lines, index, indent)


def _parse_list(lines: list[_Line], index: int, indent: int) -> tuple[list[object], int]:
    items: list[object] = []
    while index < len(lines):
        line = lines[index]
        if line.indent < indent:
            break
        if line.indent != indent or not line.content.startswith("- "):
            raise ValueError("Invalid YAML list indentation.")

        remainder = line.content[2:].strip()
        if not remainder:
            child, index = _parse_block(lines, index + 1, indent + 2)
            items.append(child)
            continue

        if ":" in remainder:
            key, value = _split_key_value(remainder)
            mapping: dict[str, object] = {key: _parse_scalar(value)} if value else {}
            index += 1
            while index < len(lines) and lines[index].indent > indent:
                child = lines[index]
                if child.content.startswith("- "):
                    child_value, index = _parse_block(lines, index, child.indent)
                    items.append(child_value)
                    break
                child_key, child_raw = _split_key_value(child.content)
                if child_raw:
                    mapping[child_key] = _parse_scalar(child_raw)
                    index += 1
                    continue
                child_value, index = _parse_block(lines, index + 1, child.indent + 2)
                mapping[child_key] = child_value
            else:
                items.append(mapping)
                continue

            if items and items[-1] is not mapping:
                continue
            items.append(mapping)
            continue

        items.append(_parse_scalar(remainder))
        index += 1
    return items, index


def _parse_mapping(lines: list[_Line], index: int, indent: int) -> tuple[dict[str, object], int]:
    mapping: dict[str, object] = {}
    while index < len(lines):
        line = lines[index]
        if line.indent < indent:
            break
        if line.indent != indent or line.content.startswith("- "):
            raise ValueError("Invalid YAML mapping indentation.")

        key, raw_value = _split_key_value(line.content)
        if raw_value:
            mapping[key] = _parse_scalar(raw_value)
            index += 1
            continue

        if index + 1 >= len(lines) or lines[index + 1].indent <= indent:
            mapping[key] = {}
            index += 1
            continue

        child, index = _parse_block(lines, index + 1, lines[index + 1].indent)
        mapping[key] = child
    return mapping, index


def _split_key_value(content: str) -> tuple[str, str]:
    key, _, value = content.partition(":")
    return key.strip(), _strip_inline_comment(value.strip())


def _strip_inline_comment(value: str) -> str:
    """Drop YAML-style inline comments. Per YAML spec a comment marker `#`
    must be preceded by whitespace; leave fragments inside quoted strings
    (e.g. URLs with `#anchor`) untouched.
    """

    if not value:
        return value
    if value[0] in {'"', "'"}:
        quote = value[0]
        end = value.find(quote, 1)
        if end == -1:
            return value
        return value[: end + 1]
    for marker in (" #", "\t#"):
        idx = value.find(marker)
        if idx != -1:
            return value[:idx].rstrip()
    return value


def _parse_scalar(value: str) -> object:
    normalized = value.strip()
    if not normalized:
        return ""
    if normalized[0] == normalized[-1] and normalized[0] in {'"', "'"}:
        return normalized[1:-1]
    lowered = normalized.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    if _is_int(normalized):
        return int(normalized)
    if _is_float(normalized):
        return float(normalized)
    return normalized


def _indent_of(text: str) -> int:
    return len(text) - len(text.lstrip(" "))


def _is_int(value: str) -> bool:
    if value.startswith("-"):
        return value[1:].isdigit()
    return value.isdigit()


def _is_float(value: str) -> bool:
    if value.count(".") != 1:
        return False
    left, right = value.split(".", 1)
    if not right:
        return False
    if left.startswith("-"):
        left = left[1:]
    return left.isdigit() and right.isdigit()
