import unittest

from app.workflows.openchat_navigate import (
    _find_result_row,
    _find_search_target,
    _shortest_query,
)


SEARCH_RESULTS_XML = """
<hierarchy rotation="0">
  <node clickable="true" text="" content-desc="搜尋" bounds="[40,180][1040,288]" class="EditText" />
  <node clickable="false" text="特殊支援群" bounds="[140,560][720,620]" class="TextView" />
  <node clickable="true" text="" content-desc="" bounds="[0,520][1080,720]" class="LinearLayout" />
  <node clickable="false" text="(3)" bounds="[740,560][800,620]" class="TextView" />
  <node clickable="false" text="另一群" bounds="[140,820][720,880]" class="TextView" />
  <node clickable="true" text="" bounds="[0,780][1080,980]" class="LinearLayout" />
</hierarchy>
"""


class OpenChatNavigateTests(unittest.TestCase):
    def test_shortest_query_picks_smallest_candidate(self) -> None:
        self.assertEqual(_shortest_query(["特殊支援群", "支援", ""]), "支援")
        self.assertIsNone(_shortest_query([]))

    def test_find_search_target_picks_clickable_with_search_hint(self) -> None:
        node = _find_search_target(SEARCH_RESULTS_XML)
        self.assertIsNotNone(node)
        self.assertEqual(node["content_desc"], "搜尋")

    def test_find_result_row_returns_clickable_ancestor(self) -> None:
        row = _find_result_row(SEARCH_RESULTS_XML, ["特殊支援群"])
        self.assertIsNotNone(row)
        # The row is the clickable LinearLayout that surrounds the matching text.
        self.assertEqual(row["bounds"], [0, 520, 1080, 720])
        # Center should be (540, 620).
        self.assertEqual(row["center"], [540, 620])

    def test_find_result_row_returns_none_when_no_match(self) -> None:
        row = _find_result_row(SEARCH_RESULTS_XML, ["不存在的群"])
        self.assertIsNone(row)

    def test_find_result_row_skips_search_bar(self) -> None:
        # Even if the candidate happens to be the search bar text itself,
        # we should not return the search bar as a result row.
        row = _find_result_row(SEARCH_RESULTS_XML, ["搜尋"])
        self.assertIsNone(row)


if __name__ == "__main__":
    unittest.main()
