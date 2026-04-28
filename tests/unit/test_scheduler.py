import unittest

from app.core.jobs import JobRegistry
from app.core.scheduler_state import SchedulerState


class SchedulerStateTests(unittest.TestCase):
    def test_scheduler_state_tracks_enqueue_and_complete(self) -> None:
        state = SchedulerState()
        state.mark_enqueued("customer_a:openchat_001", at=100.0)
        state.mark_completed("customer_a:openchat_001", at=120.0)
        snapshot = state.snapshot()
        self.assertEqual(snapshot["last_patrol_enqueued"]["customer_a:openchat_001"], 100.0)
        self.assertEqual(snapshot["last_patrol_completed"]["customer_a:openchat_001"], 120.0)

    def test_job_registry_supports_scheduled_job_type(self) -> None:
        registry = JobRegistry(persist=False)
        job = registry.enqueue("scheduled_patrol", {"device_id": "emulator-5554"})
        self.assertEqual(job.job_type, "scheduled_patrol")


if __name__ == "__main__":
    unittest.main()
