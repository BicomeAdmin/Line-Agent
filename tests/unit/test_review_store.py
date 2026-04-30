import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.core.reviews import (
    NEAR_DUPLICATE_WINDOW_MINUTES,
    ReviewRecord,
    ReviewStore,
    find_recent_duplicate_send,
)


class ReviewStoreTests(unittest.TestCase):
    def test_upsert_and_update_status(self) -> None:
        store = ReviewStore(persist=False)
        record = ReviewRecord(
            review_id="review-1",
            source_job_id="job-1",
            customer_id="customer_a",
            customer_name="客戶 A",
            community_id="openchat_001",
            community_name="測試群",
            device_id="emulator-5554",
            draft_text="原始草稿",
        )
        store.upsert(record)
        store.update_status("review-1", status="pending_reapproval", updated_from_action="edit", draft_text="修改後草稿")
        saved = store.get("review-1")
        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertEqual(saved.status, "pending_reapproval")
        self.assertEqual(saved.draft_text, "修改後草稿")

    def test_load_normalizes_legacy_edited_status(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "reviews.jsonl"
            state_path.write_text(
                "\n".join(
                    [
                        '{"review_id":"review-1","source_job_id":"job-1","customer_id":"customer_a","customer_name":"客戶 A","community_id":"openchat_001","community_name":"測試群","device_id":"emulator-5554","draft_text":"舊草稿","status":"edited"}'
                    ]
                ),
                encoding="utf-8",
            )
            store = ReviewStore(state_path=state_path, persist=True)
            saved = store.get("review-1")
            self.assertIsNotNone(saved)
            assert saved is not None
            self.assertEqual(saved.status, "pending_reapproval")


class NearDuplicateDetectionTests(unittest.TestCase):
    def _make(self, **overrides) -> ReviewRecord:
        defaults = dict(
            review_id="r-1",
            source_job_id="j-1",
            customer_id="c_a",
            customer_name="客戶 A",
            community_id="openchat_001",
            community_name="測試群",
            device_id="emulator-5554",
            draft_text="大家好，今天天氣不錯",
        )
        defaults.update(overrides)
        return ReviewRecord(**defaults)

    def test_no_warning_when_no_recent_sent(self) -> None:
        store = ReviewStore(persist=False)
        store.upsert(self._make(review_id="r-1", status="pending"))
        match = find_recent_duplicate_send(
            "openchat_001", "大家好，今天天氣不錯", store=store
        )
        self.assertIsNone(match)

    def test_warning_when_identical_sent_within_window(self) -> None:
        now = time.time()
        store = ReviewStore(persist=False)
        record = self._make(review_id="r-sent", status="sent")
        store.upsert(record)
        match = find_recent_duplicate_send(
            "openchat_001", "大家好，今天天氣不錯", store=store, now=now
        )
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.review_id, "r-sent")

    def test_no_warning_when_sent_outside_window(self) -> None:
        now = time.time()
        store = ReviewStore(persist=False)
        store.upsert(self._make(review_id="r-old", status="sent"))
        # Force the stored record's updated_at into the past, beyond window.
        old = store.get("r-old")
        assert old is not None
        old.updated_at = now - (NEAR_DUPLICATE_WINDOW_MINUTES + 1) * 60
        match = find_recent_duplicate_send(
            "openchat_001", "大家好，今天天氣不錯", store=store, now=now
        )
        self.assertIsNone(match)

    def test_no_warning_for_different_community(self) -> None:
        store = ReviewStore(persist=False)
        store.upsert(self._make(review_id="r-other", status="sent", community_id="openchat_002"))
        match = find_recent_duplicate_send(
            "openchat_001", "大家好，今天天氣不錯", store=store
        )
        self.assertIsNone(match)

    def test_no_warning_for_pending_status(self) -> None:
        store = ReviewStore(persist=False)
        store.upsert(self._make(review_id="r-pending", status="pending"))
        match = find_recent_duplicate_send(
            "openchat_001", "大家好，今天天氣不錯", store=store
        )
        self.assertIsNone(match)

    def test_whitespace_trimmed_match(self) -> None:
        store = ReviewStore(persist=False)
        store.upsert(self._make(review_id="r-sent", status="sent", draft_text="嗨大家"))
        match = find_recent_duplicate_send(
            "openchat_001", "  嗨大家  ", store=store
        )
        self.assertIsNotNone(match)


if __name__ == "__main__":
    unittest.main()
