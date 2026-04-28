"""Auto-navigate to a target OpenChat.

Three strategies, tried in order:

1. **Deep link** (`line://ti/g2/<group_id>`) — most deterministic. If `invite_url`
   or `group_id` is set in the community config, fire that intent and validate.
   For already-joined members LINE goes straight into the chat.
2. **Chat-list scan** — bring LINE to Chats tab, look for the row by exact
   display_name. Scroll a few times if not visible.
3. **Search** — tap search bar, type query (uses ADBKeyboard for non-ASCII),
   match a result row below the search bar.

After any strategy, validate via `validate_openchat_session`. Returns a step-by-step
trace so the operator can debug regressions when LINE redesigns its UI.
"""

from __future__ import annotations

import time
from pathlib import Path

from app.adb.client import AdbClient, AdbError
from app.adb.line_app import (
    LINE_PACKAGE,
    back_to_chat_list,
    check_current_app,
    is_inside_chat_history,
    open_line,
)
from app.adb.human_jitter import (
    jittered_sleep,
    jittered_swipe,
    jittered_tap,
    reading_pause,
)
from app.adb.text_input import TextInputError, send_text
from app.adb.uiautomator import dump_ui_xml
from app.core.audit import append_audit_event
from app.parsing.xml_cleaner import (
    extract_all_text_nodes_with_bounds,
    extract_clickable_nodes,
)
from app.storage.config_loader import load_community_config
from app.storage.paths import customer_root
from app.workflows.openchat_validation import validate_openchat_session


SEARCH_BAR_HINTS = ("搜尋", "搜索", "Search")
CHATS_TAB_HINTS = ("聊天", "Chats")


def navigate_to_openchat(
    customer_id: str,
    community_id: str,
    *,
    poll_seconds: float = 0.6,
    overall_timeout_seconds: float = 25.0,
) -> dict[str, object]:
    community = load_community_config(customer_id, community_id)
    device_id = community.device_id
    target_name = community.display_name
    # Operator policy: match the precise display_name as given. We deliberately do
    # NOT expand into split candidates (e.g. "客戶 A - 測試群" → ["客戶 A", "測試群"]),
    # because partial matches risk navigating into a sibling chat with overlapping name.
    candidates = [target_name]
    trace: list[dict[str, object]] = []

    client = AdbClient(device_id=device_id)

    # Strategy 1: deep link, when configured. This is the most deterministic
    # and bypasses the search/profile UX trap entirely.
    deep_link = _build_deep_link(community)
    if deep_link is not None:
        try:
            client.shell("am", "start", "-a", "android.intent.action.VIEW", "-d", deep_link, check=False)
            jittered_sleep(2.5)
        except AdbError as exc:
            trace.append({"step": "deep_link_intent_failed", "detail": str(exc)})
        else:
            trace.append({"step": "deep_link_dispatched", "url": deep_link})
            validation = validate_openchat_session(customer_id=customer_id, community_id=community_id)
            items = validation.get("items") or []
            spotlight = items[0] if items and isinstance(items[0], dict) else {}
            if spotlight.get("status") == "ok":
                trace.append({"step": "validation", "status": "ok"})
                return _ok(customer_id, community_id, device_id, target_name, spotlight, trace)
            trace.append({"step": "deep_link_validation_blocked", "reason": spotlight.get("reason")})

    if not check_current_app(client):
        try:
            open_line(client)
            jittered_sleep(2.0)
        except AdbError as exc:
            return _blocked(
                customer_id, community_id, "line_launch_failed", trace,
                {"detail": str(exc)},
            )
        if not check_current_app(client):
            return _blocked(
                customer_id, community_id, "line_not_foreground", trace,
            )
    trace.append({"step": "line_in_foreground"})

    # If LINE was left inside a previous chat (e.g. operator just navigated
    # to community A and now wants community B without an explicit "go
    # home" step), the subsequent chat-list scan / search operates on the
    # wrong view — we'd search within the open chat's content. Press BACK
    # until ChatHistoryActivity is no longer focused.
    if is_inside_chat_history(client):
        back_result = back_to_chat_list(client, max_attempts=3)
        trace.append({"step": "backed_out_of_chat_history", **back_result})
        if not back_result.get("success"):
            return _blocked(
                customer_id, community_id, "stuck_in_chat_history", trace,
                {"hint": "BACK 鍵未能離開 ChatHistoryActivity，可能 LINE 卡住或彈窗"},
            )
        jittered_sleep(0.5)  # let the chat list animate in

    xml_path = _navigate_xml_path(customer_id, community_id, "00_initial")
    initial_xml = _dump(client, xml_path)
    if initial_xml is None:
        return _blocked(customer_id, community_id, "uiautomator_dump_failed", trace)

    # Step 2: ensure on Chats tab.
    chats_tab = _find_clickable_with_text(initial_xml, CHATS_TAB_HINTS, prefer_bottom=True)
    if chats_tab is not None:
        jittered_tap(client, chats_tab["center"][0], chats_tab["center"][1])
        jittered_sleep(1.0)
        trace.append({"step": "tapped_chats_tab", "at": chats_tab["center"]})

    # Step 3a: scan the visible chat list directly. Most newly-active rooms are
    # near the top, so we can skip the search box (and its Chinese-input headache)
    # whenever a direct hit is visible without scrolling.
    chat_list_xml = _dump(client, _navigate_xml_path(customer_id, community_id, "01_chats"))
    if chat_list_xml is None:
        return _blocked(customer_id, community_id, "uiautomator_dump_failed", trace)
    matched = _find_result_row(chat_list_xml, candidates)
    if matched is not None:
        trace.append(
            {"step": "matched_in_chat_list", "matched_text": matched.get("text") or matched.get("content_desc"), "at": matched["center"]}
        )
    else:
        # Step 3b: try scrolling the list a few times before falling back to search.
        scroll_attempts = 0
        max_scrolls = 4
        while matched is None and scroll_attempts < max_scrolls:
            scroll_attempts += 1
            jittered_swipe(client, 540, 1800, 540, 900, 300)
            jittered_sleep(0.8)
            scrolled_xml = _dump(
                client,
                _navigate_xml_path(customer_id, community_id, f"01_chats_scroll_{scroll_attempts}"),
            )
            if scrolled_xml is None:
                continue
            matched = _find_result_row(scrolled_xml, candidates)
            if matched is not None:
                trace.append(
                    {
                        "step": "matched_after_scroll",
                        "scrolls": scroll_attempts,
                        "matched_text": matched.get("text") or matched.get("content_desc"),
                        "at": matched["center"],
                    }
                )

    # Step 3c: fall back to the search bar (Chinese typing limitation kicks in here).
    if matched is None:
        # Scroll back to top of list before searching, so the search bar is visible.
        for _ in range(scroll_attempts if "scroll_attempts" in dir() else 0):
            jittered_swipe(client, 540, 900, 540, 1800, 300)
            jittered_sleep(0.4)
        search_xml = _dump(client, _navigate_xml_path(customer_id, community_id, "01_chats_top"))
        search = _find_search_target(search_xml or chat_list_xml)
        if search is None:
            return _blocked(
                customer_id, community_id, "target_not_in_chat_list", trace,
                {"target_name": target_name, "hint": "did not find target by scanning + scrolling, and no search bar found"},
            )
        jittered_tap(client, search["center"][0], search["center"][1])
        jittered_sleep(0.8)
        trace.append({"step": "tapped_search_bar", "at": search["center"]})

        typed_query = _shortest_query(candidates) or target_name
        try:
            send_result = send_text(client, typed_query)
        except TextInputError as exc:
            return _blocked(
                customer_id, community_id, "text_input_unavailable", trace,
                {"typed_query": typed_query, "detail": str(exc)},
            )
        if send_result.get("status") != "ok":
            return _blocked(
                customer_id, community_id, "text_input_failed", trace,
                {"typed_query": typed_query, "send_result": send_result},
            )
        jittered_sleep(0.8)
        trace.append({"step": "typed_query", "query": typed_query, "method": send_result.get("method")})

        deadline = time.time() + overall_timeout_seconds
        iteration = 0
        while time.time() < deadline:
            iteration += 1
            results_xml = _dump(
                client, _navigate_xml_path(customer_id, community_id, f"02_search_{iteration:02d}")
            )
            if results_xml is None:
                time.sleep(poll_seconds)
                continue
            # Search results live below the search bar; filter out the bar's
            # own EditText echoing the typed query (which would otherwise match).
            search_bottom = int(search["bounds"][3]) if search else 0
            matched = _find_result_row(results_xml, candidates, min_y=search_bottom + 10)
            if matched is not None:
                trace.append(
                    {
                        "step": "matched_search_row",
                        "matched_text": matched.get("text") or matched.get("content_desc"),
                        "at": matched["center"],
                    }
                )
                break
            time.sleep(poll_seconds)
        if matched is None:
            return _blocked(
                customer_id, community_id, "target_not_in_search_results", trace,
                {"target_name": target_name, "candidates": candidates, "iterations": iteration},
            )
    trace.append(
        {
            "step": "matched_search_row",
            "matched_text": matched.get("text") or matched.get("content_desc"),
            "at": matched["center"],
        }
    )

    # Step 6: tap the matched row.
    jittered_tap(client, matched["center"][0], matched["center"][1])
    jittered_sleep(1.5)
    trace.append({"step": "tapped_result"})

    # Step 7: verify via existing OpenChat validation.
    validation = validate_openchat_session(customer_id=customer_id, community_id=community_id)
    items = validation.get("items") or []
    spotlight = items[0] if items and isinstance(items[0], dict) else {}
    ok = spotlight.get("status") == "ok"
    trace.append({"step": "validation", "status": spotlight.get("status")})

    if ok:
        return _ok(customer_id, community_id, device_id, target_name, spotlight, trace)

    return _blocked(
        customer_id, community_id, "validation_after_navigate_failed", trace,
        {"validation": spotlight},
    )


def _build_deep_link(community) -> str | None:  # noqa: ANN001 — dataclass with optional fields
    if community.invite_url and isinstance(community.invite_url, str):
        # Convert https://line.me/ti/g2/<id> → line://ti/g2/<id> so it routes
        # straight to the LINE app instead of bouncing through Chrome.
        url = community.invite_url
        if url.startswith("https://line.me/"):
            return "line://" + url[len("https://line.me/"):].split("?", 1)[0]
        if url.startswith("line://"):
            return url
    if community.group_id:
        return f"line://ti/g2/{community.group_id}"
    return None


def _ok(
    customer_id: str,
    community_id: str,
    device_id: str,
    target_name: str,
    spotlight: dict[str, object],
    trace: list[dict[str, object]],
) -> dict[str, object]:
    result = {
        "status": "ok",
        "customer_id": customer_id,
        "community_id": community_id,
        "device_id": device_id,
        "target_name": target_name,
        "matched_title": spotlight.get("matched_title"),
        "trace": trace,
        "validation": spotlight,
    }
    append_audit_event(
        customer_id,
        "openchat_navigate_attempted",
        {
            "community_id": community_id,
            "device_id": device_id,
            "status": "ok",
            "matched_title": result.get("matched_title"),
            "trace_steps": [step.get("step") for step in trace],
        },
    )
    return result


def _navigate_xml_path(customer_id: str, community_id: str, label: str) -> Path:
    target = customer_root(customer_id) / "data" / "raw_xml" / "navigate"
    target.mkdir(parents=True, exist_ok=True)
    return target / f"{community_id}-{label}.xml"


def _dump(client: AdbClient, xml_path: Path) -> str | None:
    try:
        dumped = dump_ui_xml(client, xml_path)
        return dumped.read_text(encoding="utf-8")
    except (AdbError, RuntimeError, OSError):
        return None


def _find_clickable_with_text(
    xml_text: str,
    hints: tuple[str, ...],
    *,
    prefer_bottom: bool = False,
) -> dict[str, object] | None:
    candidates: list[dict[str, object]] = []
    for node in extract_clickable_nodes(xml_text):
        if any(h in (node.get("text") or "") for h in hints) or any(
            h in (node.get("content_desc") or "") for h in hints
        ):
            candidates.append(node)
    if not candidates:
        return None
    if prefer_bottom:
        candidates.sort(key=lambda n: n["bounds"][1], reverse=True)
    return candidates[0]


def _find_search_target(xml_text: str) -> dict[str, object] | None:
    # Prefer clickable elements; fall back to any node with the hint text whose
    # bounds make sense (top of screen) when the search bar isn't itself marked
    # clickable but its container is.
    direct = _find_clickable_with_text(xml_text, SEARCH_BAR_HINTS)
    if direct is not None:
        return direct
    for node in extract_all_text_nodes_with_bounds(xml_text):
        if any(h in (node.get("text") or "") for h in SEARCH_BAR_HINTS) or any(
            h in (node.get("content_desc") or "") for h in SEARCH_BAR_HINTS
        ):
            # Prefer high-y (top of screen).
            if node["bounds"][1] < 600:
                return node
    return None


def _find_result_row(
    xml_text: str,
    candidates: list[str],
    *,
    min_y: int = 0,
    excluded_class_substrings: tuple[str, ...] = ("EditText",),
) -> dict[str, object] | None:
    """Find a clickable row whose visible text matches any candidate.

    `min_y` filters out nodes above the search bar / app header — when the user
    has just typed into a search EditText, the EditText itself echoes the query,
    so naïve matching would pick the search bar instead of the actual result row.
    """

    nodes = extract_all_text_nodes_with_bounds(xml_text)
    for candidate in candidates:
        normalized_candidate = _normalize(candidate)
        if not normalized_candidate:
            continue
        for node in nodes:
            text = node.get("text") or ""
            desc = node.get("content_desc") or ""
            if not (text or desc):
                continue
            if any(sub in (node.get("class") or "") for sub in excluded_class_substrings):
                continue
            if int(node["bounds"][1]) < min_y:
                continue
            normalized = _normalize(text or desc)
            if normalized_candidate not in normalized:
                continue
            # Skip the search bar itself / hint placeholders.
            if any(h in text or h in desc for h in SEARCH_BAR_HINTS):
                continue
            # Walk up to a clickable ancestor; uiautomator dump is flat with bounds, so
            # we use the closest clickable node whose bounds contain this node's center.
            cx, cy = node["center"]
            best_ancestor: dict[str, object] | None = None
            for clickable in extract_clickable_nodes(xml_text):
                bx0, by0, bx1, by1 = clickable["bounds"]
                if not (bx0 <= cx <= bx1 and by0 <= cy <= by1):
                    continue
                if any(sub in (clickable.get("class") or "") for sub in excluded_class_substrings):
                    continue
                if int(clickable["bounds"][1]) < min_y:
                    continue
                c_text = clickable.get("text") or ""
                c_desc = clickable.get("content_desc") or ""
                if any(h in c_text or h in c_desc for h in SEARCH_BAR_HINTS):
                    continue
                # Prefer the **widest** clickable that contains the text — chat rows
                # span the full screen width, while inner widgets like the avatar
                # bring up the wrong screen (e.g. group profile).
                width = bx1 - bx0
                if best_ancestor is None or width > (
                    best_ancestor["bounds"][2] - best_ancestor["bounds"][0]
                ):
                    best_ancestor = clickable
            if best_ancestor is not None:
                return best_ancestor
            # Fall back to the text node's bounds if no clickable ancestor matched.
            return node
    return None


def _shortest_query(candidates: list[str]) -> str | None:
    filtered = [c for c in candidates if c]
    if not filtered:
        return None
    return min(filtered, key=lambda c: len(c))


def _normalize(value: str) -> str:
    return "".join(value.lower().split())


def _blocked(
    customer_id: str,
    community_id: str,
    reason: str,
    trace: list[dict[str, object]],
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    payload = {
        "status": "blocked",
        "customer_id": customer_id,
        "community_id": community_id,
        "reason": reason,
        "trace": trace,
    }
    if extra:
        payload.update(extra)
    append_audit_event(
        customer_id,
        "openchat_navigate_attempted",
        {
            "community_id": community_id,
            "status": "blocked",
            "reason": reason,
            "trace_steps": [step.get("step") for step in trace],
        },
    )
    return payload
