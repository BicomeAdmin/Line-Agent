import unittest

from app.parsing.xml_cleaner import (
    extract_all_text_nodes_with_bounds,
    extract_clickable_nodes,
    extract_text_nodes,
    is_system_text,
)


class XmlCleanerTests(unittest.TestCase):
    def test_filters_status_text(self) -> None:
        self.assertTrue(is_system_text("10:28"))
        self.assertTrue(is_system_text("100%"))
        self.assertTrue(is_system_text("LINE"))

    def test_extract_text_nodes_filters_system_nodes(self) -> None:
        xml = """<hierarchy><node text="10:28" /><node text="hello" /><node text="100%" /></hierarchy>"""
        self.assertEqual(extract_text_nodes(xml), ["hello"])

    def test_extract_clickable_nodes(self) -> None:
        xml = (
            '<hierarchy>'
            '<node clickable="true" text="搜尋" bounds="[0,100][1080,200]" class="EditText" />'
            '<node clickable="false" text="ignored" bounds="[0,300][100,400]" />'
            '<node clickable="true" content-desc="聊天" bounds="[0,2300][270,2400]" class="ImageView" />'
            '<node clickable="true" text="bad-bounds" bounds="invalid" />'
            '</hierarchy>'
        )
        nodes = extract_clickable_nodes(xml)
        texts = [n.get("text") or n.get("content_desc") for n in nodes]
        self.assertEqual(texts, ["搜尋", "聊天"])
        # Search bar centre should be (540, 150).
        self.assertEqual(nodes[0]["center"], [540, 150])

    def test_extract_all_text_nodes_with_bounds(self) -> None:
        xml = (
            '<hierarchy>'
            '<node text="" bounds="[0,0][10,10]" />'  # ignored: no text/desc
            '<node text="特殊支援群" bounds="[100,400][800,460]" clickable="false" />'
            '<node content-desc="未讀訊息" bounds="[900,400][1000,460]" clickable="true" />'
            '</hierarchy>'
        )
        nodes = extract_all_text_nodes_with_bounds(xml)
        self.assertEqual(len(nodes), 2)
        self.assertEqual(nodes[0]["text"], "特殊支援群")
        self.assertFalse(nodes[0]["clickable"])
        self.assertTrue(nodes[1]["clickable"])


if __name__ == "__main__":
    unittest.main()
