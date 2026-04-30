"""Tests for the last-mile draft safety lint."""

import unittest

from app.ai.send_safety import audit_draft_for_send


class UrlBlockTests(unittest.TestCase):
    def test_https_url_blocks(self) -> None:
        v = audit_draft_for_send("看看這個 https://shopee.tw/abc 不錯")
        self.assertTrue(v.has_blocks)
        self.assertEqual(v.issues[0].code, "url_in_draft")

    def test_bare_domain_blocks(self) -> None:
        v = audit_draft_for_send("我都看 example.com 那個")
        self.assertTrue(v.has_blocks)

    def test_line_protocol_blocks(self) -> None:
        v = audit_draft_for_send("加我 line://ti/g2/abc")
        self.assertTrue(v.has_blocks)

    def test_no_url_passes(self) -> None:
        v = audit_draft_for_send("我覺得這個不錯啊")
        self.assertFalse(v.has_blocks)


class PhoneBlockTests(unittest.TestCase):
    def test_taiwan_mobile_blocks(self) -> None:
        v = audit_draft_for_send("打 0912-345-678 給我")
        self.assertTrue(v.has_blocks)
        self.assertTrue(any(i.code == "phone_in_draft" for i in v.issues))

    def test_taiwan_landline_blocks(self) -> None:
        v = audit_draft_for_send("傳真到 02-2345-6789")
        self.assertTrue(v.has_blocks)

    def test_intl_format_blocks(self) -> None:
        v = audit_draft_for_send("國際電話 +886-912-345-678")
        self.assertTrue(v.has_blocks)


class EmailBlockTests(unittest.TestCase):
    def test_email_blocks(self) -> None:
        v = audit_draft_for_send("寄到 hello@example.com 喔")
        self.assertTrue(v.has_blocks)


class PaymentBlockTests(unittest.TestCase):
    def test_payment_keyword_blocks(self) -> None:
        v = audit_draft_for_send("信用卡卡號借我用一下")
        self.assertTrue(v.has_blocks)
        self.assertTrue(any(i.code == "payment_reference" for i in v.issues))

    def test_crypto_blocks(self) -> None:
        v = audit_draft_for_send("可以付 BTC 嗎")
        self.assertTrue(v.has_blocks)


class WarnLevelTests(unittest.TestCase):
    def test_multiple_mentions_warns(self) -> None:
        v = audit_draft_for_send("@小明 @小華 來看一下")
        self.assertFalse(v.has_blocks)
        self.assertTrue(v.has_warns)
        self.assertTrue(any(i.code == "multiple_mentions" for i in v.issues))

    def test_single_mention_no_warn(self) -> None:
        v = audit_draft_for_send("@小明 你好")
        self.assertFalse(v.has_warns)

    def test_long_draft_warns(self) -> None:
        v = audit_draft_for_send("好" * 410)
        self.assertTrue(v.has_warns)
        self.assertTrue(any(i.code == "very_long_draft" for i in v.issues))


class CleanDraftTests(unittest.TestCase):
    def test_typical_chat_passes_clean(self) -> None:
        v = audit_draft_for_send("我覺得不用急啦 這個慢慢來都行的")
        self.assertFalse(v.has_blocks)
        self.assertFalse(v.has_warns)
        self.assertEqual(v.issues, ())

    def test_empty_draft_passes(self) -> None:
        v = audit_draft_for_send("")
        self.assertFalse(v.has_blocks)


if __name__ == "__main__":
    unittest.main()
