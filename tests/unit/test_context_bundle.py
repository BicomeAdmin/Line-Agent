import unittest

from app.ai.context_bundle import build_prompt_context, load_context_bundle


class ContextBundleTests(unittest.TestCase):
    def test_load_context_bundle(self) -> None:
        # community display_name is operator-controlled; assert structural fields only.
        bundle = load_context_bundle("customer_a", "openchat_001")
        self.assertEqual(bundle.customer_name, "客戶 A")
        self.assertTrue(bundle.community_name)
        self.assertIn("人工審核", bundle.playbook_text)

    def test_build_prompt_context_limits_messages(self) -> None:
        bundle = load_context_bundle("customer_a", "openchat_001")
        messages = [{"text": f"msg-{index}"} for index in range(25)]
        payload = build_prompt_context(bundle, messages)
        recent = payload["recent_messages"]
        self.assertEqual(len(recent), 20)
        self.assertEqual(recent[0], "msg-5")


if __name__ == "__main__":
    unittest.main()
