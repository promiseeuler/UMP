import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.benchmark import BenchmarkThresholds, percentile, run_reference_benchmark
from ump.cli import benchmark_main


class BenchmarkTests(unittest.TestCase):
    def test_nearest_rank_percentile_is_deterministic(self):
        values = [50, 10, 40, 20, 30]
        self.assertEqual(percentile(values, 0.0), 10)
        self.assertEqual(percentile(values, 0.5), 30)
        self.assertEqual(percentile(values, 0.95), 50)
        self.assertEqual(percentile(values, 1.0), 50)

    def test_reference_report_is_machine_readable_and_scoped(self):
        result = run_reference_benchmark(samples=20, participants=3)
        document = result.as_dict()
        self.assertEqual(document["profile"], "ump.reference.in-memory/v1")
        self.assertEqual(document["samples"], 20)
        self.assertEqual(document["participants"], 3)
        self.assertLessEqual(
            document["largest_observed_message_bytes"],
            document["maximum_message_bytes"],
        )
        self.assertIn("platform", document["environment"])
        json.dumps(document)

    def test_threshold_failure_changes_report_status(self):
        result = run_reference_benchmark(
            samples=10,
            participants=1,
            thresholds=BenchmarkThresholds(
                propagation_p95_ms=0.0,
                idle_heap_per_participant_bytes=0,
            ),
        )
        self.assertFalse(result.passed)

    def test_cli_emits_json_and_uses_report_status(self):
        with patch("builtins.print") as output:
            exit_code = benchmark_main(["--samples", "10", "--participants", "2"])
        self.assertEqual(exit_code, 0)
        document = json.loads(output.call_args.args[0])
        self.assertTrue(document["passed"])

    def test_invalid_sample_count_returns_usage_error(self):
        with patch("builtins.print"):
            self.assertEqual(benchmark_main(["--samples", "1"]), 2)


if __name__ == "__main__":
    unittest.main()
