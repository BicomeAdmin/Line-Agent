import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.core.reviews import ReviewRecord, ReviewStore


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


if __name__ == "__main__":
    unittest.main()
