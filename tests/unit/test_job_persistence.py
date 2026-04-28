import unittest

from app.core.jobs import JobRecord


class JobPersistenceTests(unittest.TestCase):
    def test_job_record_to_dict(self) -> None:
        job = JobRecord(job_id="job-1", job_type="demo", payload={"x": 1})
        payload = job.to_dict()
        self.assertEqual(payload["job_id"], "job-1")
        self.assertEqual(payload["job_type"], "demo")
        self.assertEqual(payload["payload"], {"x": 1})


if __name__ == "__main__":
    unittest.main()
