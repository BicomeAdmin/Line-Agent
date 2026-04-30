"""Single source of truth for "is this sender the operator?".

Multiple downstream modules (selector, lifecycle tagging, relationship
graph, KPI tracker, etc.) need to recognize the operator across all
the names they appear under: their `operator_nickname`, any
`operator_aliases`, the synthetic `__operator__` sentinel that
parse_line_chat emits, and the `is_self` flag from right-aligned
bubble detection.

Before this module existed, each downstream re-implemented the check
inconsistently — some compared only `operator_nickname`, missing
aliased names like 「阿樂 本尊」 (LINE OpenChat appends role badges
to display names). That mis-classified the operator's own messages as
member activity in lifecycle, relationship_graph, and KPI counts —
inflating engagement metrics and recommending the operator as their
own KOC. The selector was the only module doing it right.

Use this module from anywhere that needs to filter operator from
member-flow analysis. The semantics here are deliberately the same
as the historical selector implementation, so behavior on the watcher
hot path is unchanged.
"""

from __future__ import annotations

from typing import Iterable


OPERATOR_SENTINEL = "__operator__"


def operator_name_set(nickname: str | None, aliases: Iterable[str] | None = ()) -> set[str]:
    """Build the canonical set of operator-identifying names.

    Includes the primary nickname plus any aliases declared on the
    community config. Empty strings are dropped so we don't degenerate
    to "match everything".
    """

    names: set[str] = set()
    if nickname:
        n = str(nickname).strip()
        if n:
            names.add(n)
    for a in aliases or ():
        if a is None:
            continue
        s = str(a).strip()
        if s:
            names.add(s)
    return names


def is_operator_message(msg: dict, operator_names: set[str]) -> bool:
    """Decide whether a chat-message dict belongs to the operator.

    A message is operator-attributed if any of:
      - `is_self=True` (right-aligned bubble detection in live UI)
      - sender == "__operator__" (synthetic sentinel)
      - sender substring-contains any operator-identifying name. The
        substring check handles "比利 本尊" matching nickname "比利",
        which is the LINE OpenChat role-badge convention.
    """

    if msg.get("is_self"):
        return True
    sender = str(msg.get("sender") or "")
    return is_operator_sender(sender, operator_names)


def is_operator_sender(sender: str | None, operator_names: set[str]) -> bool:
    """Same logic as `is_operator_message` but takes a bare sender
    name (used by lifecycle / relationship / KPI tagging where we
    only have the sender string, not a full message dict).
    """

    if not sender:
        return False
    s = str(sender)
    if s == OPERATOR_SENTINEL:
        return True
    for name in operator_names:
        if name and name in s:
            return True
    return False


def operator_names_for_community(community) -> set[str]:
    """Convenience: pull (nickname, aliases) off a CommunityConfig and
    return the resolved name set. Tolerates communities without
    aliases configured (returns just the nickname).
    """

    nickname = getattr(community, "operator_nickname", None) or ""
    aliases = getattr(community, "operator_aliases", None) or ()
    return operator_name_set(nickname, aliases)
