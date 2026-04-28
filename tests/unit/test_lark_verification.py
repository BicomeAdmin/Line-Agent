import unittest
from unittest.mock import patch

from app.lark.verification import handle_url_verification


class LarkVerificationTests(unittest.TestCase):
    def test_lark_url_verification_no_token_configured(self) -> None:
        with patch("app.lark.verification.settings") as mock_settings:
            mock_settings.lark_verification_token = None
            payload = {"type": "url_verification", "challenge": "abc"}
            self.assertEqual(handle_url_verification(payload), {"challenge": "abc"})

    def test_lark_url_verification_matching_token(self) -> None:
        with patch("app.lark.verification.settings") as mock_settings:
            mock_settings.lark_verification_token = "the-token"
            payload = {"type": "url_verification", "token": "the-token", "challenge": "abc"}
            self.assertEqual(handle_url_verification(payload), {"challenge": "abc"})

    def test_lark_url_verification_token_mismatch(self) -> None:
        with patch("app.lark.verification.settings") as mock_settings:
            mock_settings.lark_verification_token = "the-token"
            payload = {"type": "url_verification", "token": "wrong", "challenge": "abc"}
            self.assertEqual(
                handle_url_verification(payload),
                {"status": "error", "reason": "invalid_token"},
            )

    def test_non_verification_returns_none(self) -> None:
        self.assertIsNone(handle_url_verification({"type": "event_callback"}))


if __name__ == "__main__":
    unittest.main()
