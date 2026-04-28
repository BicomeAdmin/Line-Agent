import unittest

from app.core.jobs import JobRegistry


class JobRegistryTests(unittest.TestCase):
    def test_enqueue_and_complete_job(self) -> None:
        registry = JobRegistry(persist=False)
        job = registry.enqueue("demo", {"hello": "world"}, event_id="evt-1")
        self.assertEqual(job.status, "queued")
        popped = registry.pop(timeout_seconds=0.01)
        self.assertIsNotNone(popped)
        registry.complete(job.job_id, {"status": "ok"})
        saved = registry.get(job.job_id)
        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertEqual(saved.status, "completed")

    def test_deduplicates_same_event(self) -> None:
        registry = JobRegistry(persist=False)
        first = registry.enqueue("demo", {}, event_id="evt-1")
        second = registry.enqueue("demo", {}, event_id="evt-1")
        self.assertEqual(first.job_id, second.job_id)


if __name__ == "__main__":
    unittest.main()
