import json
import sys
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.operations import OperationsServer, RuntimeMonitor


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.monitor = RuntimeMonitor(clock_ms=lambda: 1_000)
        self.server = OperationsServer(self.monitor, "127.0.0.1", 0)
        self.host, self.port = self.server.start()

    def tearDown(self):
        self.server.close()

    def get(self, path):
        return urlopen(f"http://{self.host}:{self.port}{path}", timeout=2)

    def test_liveness_readiness_and_metrics_are_content_free(self):
        with self.get("/healthz") as response:
            self.assertEqual(json.loads(response.read()), {"healthy": True})
        with self.assertRaises(HTTPError) as failure:
            self.get("/readyz")
        self.assertEqual(failure.exception.code, 503)
        self.monitor.ready()
        self.monitor.check_passed()
        with self.get("/readyz") as response:
            self.assertEqual(json.loads(response.read()), {"ready": True})
        with self.get("/metrics") as response:
            body = response.read().decode()
        self.assertIn("ump_runtime_checks_total 1", body)
        self.assertNotIn("robot", body)

    def test_failed_runtime_check_removes_readiness(self):
        self.monitor.ready()
        self.monitor.check_failed(OSError("database unavailable"))
        status = self.monitor.snapshot()
        self.assertFalse(status.healthy)
        self.assertFalse(status.ready)
        self.assertEqual(status.failures, 1)
        with self.assertRaises(HTTPError) as failure:
            self.get("/healthz")
        self.assertEqual(failure.exception.code, 503)


if __name__ == "__main__":
    unittest.main()
