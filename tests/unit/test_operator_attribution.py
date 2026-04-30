"""Tests for the shared operator-attribution helpers."""

import unittest
from unittest.mock import MagicMock

from app.workflows.operator_attribution import (
    OPERATOR_SENTINEL,
    is_operator_message,
    is_operator_sender,
    operator_name_set,
    operator_names_for_community,
)


class OperatorNameSetTests(unittest.TestCase):
    def test_combines_nickname_and_aliases(self) -> None:
        self.assertEqual(
            operator_name_set("阿樂2", ["阿樂 本尊"]),
            {"阿樂2", "阿樂 本尊"},
        )

    def test_strips_whitespace(self) -> None:
        self.assertEqual(
            operator_name_set("  alice  ", ("  alice2  ",)),
            {"alice", "alice2"},
        )

    def test_drops_empty_and_none(self) -> None:
        self.assertEqual(
            operator_name_set("alice", ["", "  ", None]),
            {"alice"},
        )

    def test_empty_nickname_no_aliases(self) -> None:
        self.assertEqual(operator_name_set("", ()), set())

    def test_none_nickname_works(self) -> None:
        self.assertEqual(operator_name_set(None, ["alice"]), {"alice"})


class IsOperatorSenderTests(unittest.TestCase):
    def test_exact_nickname_matches(self) -> None:
        self.assertTrue(is_operator_sender("alice", {"alice"}))

    def test_alias_match(self) -> None:
        # "阿樂 本尊" alias should match
        self.assertTrue(is_operator_sender("阿樂 本尊", {"阿樂 本尊"}))

    def test_substring_match(self) -> None:
        # nickname "比利" matches "比利 本尊" (LINE OpenChat role-badge)
        self.assertTrue(is_operator_sender("比利 本尊", {"比利"}))

    def test_sentinel_matches(self) -> None:
        self.assertTrue(is_operator_sender(OPERATOR_SENTINEL, set()))

    def test_unrelated_sender_does_not_match(self) -> None:
        self.assertFalse(is_operator_sender("Lee", {"alice", "alice 本尊"}))

    def test_empty_sender_returns_false(self) -> None:
        self.assertFalse(is_operator_sender("", {"alice"}))
        self.assertFalse(is_operator_sender(None, {"alice"}))

    def test_empty_name_set_only_sentinel_works(self) -> None:
        self.assertFalse(is_operator_sender("alice", set()))
        self.assertTrue(is_operator_sender(OPERATOR_SENTINEL, set()))


class IsOperatorMessageTests(unittest.TestCase):
    def test_is_self_flag_wins(self) -> None:
        # Even with empty sender / unknown name, is_self=True is enough
        self.assertTrue(is_operator_message({"is_self": True, "sender": ""}, set()))

    def test_sentinel_via_message_dict(self) -> None:
        self.assertTrue(is_operator_message({"sender": OPERATOR_SENTINEL}, set()))

    def test_alias_via_message_dict(self) -> None:
        msg = {"sender": "阿樂 本尊", "is_self": False}
        self.assertTrue(is_operator_message(msg, {"阿樂 本尊"}))

    def test_unrelated_sender_returns_false(self) -> None:
        self.assertFalse(is_operator_message({"sender": "Lee"}, {"alice"}))


class OperatorNamesForCommunityTests(unittest.TestCase):
    def test_pulls_nickname_and_aliases_off_community(self) -> None:
        community = MagicMock(operator_nickname="阿樂2", operator_aliases=["阿樂 本尊"])
        self.assertEqual(operator_names_for_community(community), {"阿樂2", "阿樂 本尊"})

    def test_no_aliases_attribute_tolerated(self) -> None:
        # Some communities may not declare operator_aliases — getattr fallback
        community = MagicMock(spec=["operator_nickname"])
        community.operator_nickname = "alice"
        self.assertEqual(operator_names_for_community(community), {"alice"})


if __name__ == "__main__":
    unittest.main()
