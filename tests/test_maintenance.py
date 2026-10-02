"""Maintenance worker failure isolation and safe public diagnostics."""
import json
import unittest
from unittest import mock

from gpu_watch.maintenance import MaintenanceWorker


class MaintenanceWorkerTests(unittest.TestCase):
    def test_failure_reports_safe_category_then_recovers_on_next_cycle(self):
        calls = []
        failed_status = []
        def callback():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("token=must-never-leak /private/secret-file")
            failed_status.append(worker.status())
            return {"integrity": "ok"}
        worker = MaintenanceWorker(callback, initial_delay_seconds=0, interval_seconds=60)
        with mock.patch.object(worker.stop_event, "wait", side_effect=[False, False, True]):
            worker.run()
        self.assertEqual(len(calls), 2)
        self.assertEqual(failed_status[0]["last_error"], "RuntimeError")
        self.assertEqual(failed_status[0]["last_result"], {})
        self.assertNotIn("must-never-leak", json.dumps(failed_status))
        self.assertNotIn("/private", json.dumps(failed_status))
        recovered = worker.status()
        self.assertIsNone(recovered["last_error"])
        self.assertEqual(recovered["last_result"], {"integrity": "ok"})
        self.assertIsNotNone(recovered["last_completed_at"])

    def test_stopping_during_initial_wait_runs_no_maintenance(self):
        callback = mock.Mock()
        worker = MaintenanceWorker(callback)
        worker.stop_event.set()
        worker.run()
        callback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
