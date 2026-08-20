import json
from pathlib import Path
import sys
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.benchmark import (
    BenchmarkThresholds,
    TlsBenchmarkThresholds,
    _benchmark_credentials,
    percentile,
    run_reference_benchmark,
    run_tls_loopback_benchmark,
    run_tls_network_benchmark,
    serve_tls_network_benchmark,
)
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

    def test_tls_loopback_report_measures_authenticated_round_trips(self):
        result = run_tls_loopback_benchmark(samples=10)
        document = result.as_dict()
        self.assertTrue(document["passed"])
        self.assertEqual(document["profile"], "ump.reference.tls-loopback/v1")
        self.assertEqual(document["samples"], 10)
        self.assertGreater(document["round_trip_p95_ms"], 0)
        self.assertIn("mutual-TLS", document["environment"]["transport"])

    def test_tls_threshold_failure_changes_report_status(self):
        result = run_tls_loopback_benchmark(
            samples=10,
            thresholds=TlsBenchmarkThresholds(round_trip_p95_ms=0.0),
        )
        self.assertFalse(result.passed)

    def test_cli_selects_tls_loopback_profile(self):
        with patch("builtins.print") as output:
            exit_code = benchmark_main(
                ["--profile", "tls-loopback", "--samples", "10"]
            )
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            json.loads(output.call_args.args[0])["profile"],
            "ump.reference.tls-loopback/v1",
        )

    def test_two_host_harness_exercises_network_tls_path(self):
        with tempfile.TemporaryDirectory() as directory:
            credentials = _benchmark_credentials(Path(directory))
            ca, server_certificate, server_key, client_certificate, client_key = (
                credentials
            )
            ready = Event()
            endpoint = {}
            server_report = []

            def announce(host, port):
                endpoint.update(host=host, port=port)
                ready.set()

            def serve():
                server_report.append(
                    serve_tls_network_benchmark(
                        host="127.0.0.1",
                        port=0,
                        robot_id="benchmark-server",
                        certificate_path=server_certificate,
                        private_key_path=server_key,
                        ca_path=ca,
                        expected_messages=12,
                        timeout=5.0,
                        ready=announce,
                    )
                )

            thread = Thread(target=serve)
            thread.start()
            self.assertTrue(ready.wait(2.0))
            result = run_tls_network_benchmark(
                host=endpoint["host"],
                port=endpoint["port"],
                local_robot_id="benchmark-client",
                remote_robot_id="benchmark-server",
                certificate_path=client_certificate,
                private_key_path=client_key,
                ca_path=ca,
                samples=10,
                warmup_samples=2,
            )
            thread.join(5.0)
            self.assertFalse(thread.is_alive())
            self.assertTrue(result.passed)
            self.assertEqual(result.profile, "ump.reference.tls-network/v1")
            self.assertEqual(server_report[0]["received_messages"], 12)
            self.assertTrue(server_report[0]["passed"])


if __name__ == "__main__":
    unittest.main()
