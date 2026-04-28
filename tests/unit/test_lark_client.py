import unittest

from app.lark.client import LarkAccessToken


class LarkClientTests(unittest.TestCase):
    def test_access_token_defaults(self) -> None:
        token = LarkAccessToken(token="abc", expire=7200)
        self.assertEqual(token.token_type, "tenant_access_token")

    def test_access_token_allows_custom_type(self) -> None:
        token = LarkAccessToken(token="abc", expire=7200, token_type="app_access_token")
        self.assertEqual(token.token_type, "app_access_token")


if __name__ == "__main__":
    unittest.main()
