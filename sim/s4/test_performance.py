import json
import os
from pathlib import Path
import tempfile
import unittest

from performance import report


class PerformanceReportTests(unittest.TestCase):
    def test_report_computes_runtime_metrics_and_gates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            samples = root / "samples.jsonl"
            records = []
            for index in range(3):
                records.append(
                    {
                        "monotonic_ns": 1_000_000_000 + index * 100_000_000,
                        "processes": {
                            name: {"rss_kib": 3000 + index, "cpu_ticks": 10 + index}
                            for name in ("mobile", "arm", "zone")
                        },
                    }
                )
            samples.write_text("".join(json.dumps(item) + "\n" for item in records))
            invariants = root / "invariants.json"
            invariants.write_text(
                json.dumps({"passed": True, "task_count": 6, "record_count": 22})
            )
            (root / "ump").write_bytes(b"ump")
            (root / "umpd").write_bytes(b"umpd")
            output = root / "report.json"
            previous = os.environ.get("UMP_BINARY_DIR")
            os.environ["UMP_BINARY_DIR"] = str(root)
            try:
                result = report(samples, invariants, output)
            finally:
                if previous is None:
                    os.environ.pop("UMP_BINARY_DIR", None)
                else:
                    os.environ["UMP_BINARY_DIR"] = previous
            self.assertTrue(result["passed"])
            self.assertEqual(result["mission"]["duration_seconds"], 0.2)
            self.assertEqual(result["processes"]["mobile"]["peak_rss_kib"], 3002)
            self.assertTrue(output.exists())

    def test_report_fails_memory_and_invariant_gates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            samples = root / "samples.jsonl"
            samples.write_text(
                json.dumps(
                    {
                        "monotonic_ns": 1,
                        "processes": {
                            name: {"rss_kib": 70 * 1024, "cpu_ticks": 1}
                            for name in ("mobile", "arm", "zone")
                        },
                    }
                )
                + "\n"
            )
            invariants = root / "invariants.json"
            invariants.write_text(json.dumps({"passed": False}))
            (root / "ump").write_bytes(b"ump")
            (root / "umpd").write_bytes(b"umpd")
            previous = os.environ.get("UMP_BINARY_DIR")
            os.environ["UMP_BINARY_DIR"] = str(root)
            try:
                result = report(samples, invariants, root / "report.json")
            finally:
                if previous is None:
                    os.environ.pop("UMP_BINARY_DIR", None)
                else:
                    os.environ["UMP_BINARY_DIR"] = previous
            self.assertFalse(result["passed"])
            self.assertIn("runtime_peak_rss_below_64_mib", result["failed_checks"])
            self.assertIn("mission_invariants_passed", result["failed_checks"])
