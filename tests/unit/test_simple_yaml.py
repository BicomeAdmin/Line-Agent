import unittest

from app.core.simple_yaml import load_yaml


class SimpleYamlTests(unittest.TestCase):
    def test_load_mapping_and_list(self) -> None:
        payload = load_yaml(
            """
            devices:
              - device_id: emulator-5554
                enabled: true
            """
        )
        self.assertEqual(
            payload,
            {"devices": [{"device_id": "emulator-5554", "enabled": True}]},
        )

    def test_load_nested_mapping(self) -> None:
        payload = load_yaml(
            """
            activity_window:
              start: "09:00"
              end: "23:00"
            """
        )
        self.assertEqual(payload, {"activity_window": {"start": "09:00", "end": "23:00"}})


    def test_strips_inline_comment_after_int_value(self) -> None:
        payload = load_yaml(
            """
            cooldown_seconds: 1200     # 20 min — 道場群調性沉穩
            poll_interval_seconds: 90  # 90s — 對話節奏慢
            """
        )
        self.assertEqual(payload, {"cooldown_seconds": 1200, "poll_interval_seconds": 90})

    def test_preserves_hash_inside_quoted_strings(self) -> None:
        payload = load_yaml(
            """
            invite_url: "https://line.me/g2/abc?utm_source=invite#fragment"
            """
        )
        self.assertEqual(payload["invite_url"], "https://line.me/g2/abc?utm_source=invite#fragment")

    def test_no_strip_without_whitespace_before_hash(self) -> None:
        # YAML spec: `#` only starts a comment when preceded by whitespace.
        payload = load_yaml("color: red#blue")
        self.assertEqual(payload, {"color": "red#blue"})

    def test_strips_inline_comment_after_unquoted_string(self) -> None:
        payload = load_yaml("nickname: 妍   # operator alias in 004")
        self.assertEqual(payload, {"nickname": "妍"})


if __name__ == "__main__":
    unittest.main()
