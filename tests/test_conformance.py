import json
from contextlib import redirect_stderr, redirect_stdout
from hashlib import sha256
from io import StringIO
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.cli import adapter_conformance_main, conformance_main
from ump.conformance import (
    AdapterConformanceHarness,
    inspect_adapter_evidence,
    validate_vector_suite,
)
from ump.models import Assignment, PlanStep, RobotManifest
from ump.simulation import SimulatedRobot, capability


ROOT = Path(__file__).parents[1]


class InvalidIdentityAdapter(SimulatedRobot):
    def state(self):
        return replace(super().state(), robot_id="different-robot")


def create_invalid_adapter(_config_path=None):
    manifest = RobotManifest(
        "invalid-identity-adapter",
        "Example Robotics",
        "Invalid",
        "test",
        (),
    )
    return InvalidIdentityAdapter(manifest)


class ConformanceVectorTests(unittest.TestCase):
    def test_published_v01_vectors_pass_their_declared_expectations(self):
        report = validate_vector_suite(ROOT / "conformance" / "v0.1")
        self.assertTrue(report.passed)
        self.assertEqual(len(report.checks), 9)

    def test_cli_emits_machine_readable_success_report(self):
        from contextlib import redirect_stdout
        from io import StringIO

        output = StringIO()
        with redirect_stdout(output):
            status = conformance_main([str(ROOT / "conformance" / "v0.1")])
        document = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertTrue(document["passed"])


class AdapterHarnessTests(unittest.TestCase):
    def setUp(self):
        self.manifest = RobotManifest(
            "robot-adapter-1",
            "Example Robotics",
            "A1",
            "mobile_base",
            (capability("ump.conformance.echo/v1", "Echo structured input"),),
        )
        self.adapter = SimulatedRobot(self.manifest)
        self.assignment = Assignment(
            "assignment-fixture-1",
            "goal-fixture-1",
            "plan-fixture-1",
            PlanStep(
                "echo",
                "Execute conformance fixture",
                self.manifest.robot_id,
                "ump.conformance.echo/v1",
                {"value": "hello"},
                "Fixture returns a terminal result",
            ),
        )

    def test_read_only_inspection_passes_without_native_execution(self):
        report = AdapterConformanceHarness().inspect(self.adapter)
        self.assertTrue(report.passed)
        self.assertEqual(len(report.checks), 3)

    def test_identity_mismatch_is_reported_without_raising(self):
        report = AdapterConformanceHarness().inspect(
            InvalidIdentityAdapter(self.manifest)
        )
        self.assertFalse(report.passed)
        failed = next(check for check in report.checks if not check.passed)
        self.assertEqual(failed.check_id, "adapter.identity")

    def test_native_execution_requires_explicit_gate(self):
        with self.assertRaisesRegex(PermissionError, "allow_native_execution"):
            AdapterConformanceHarness().exercise(
                self.adapter, (self.assignment,)
            )

    def test_opted_in_fixture_execution_validates_terminal_outcome(self):
        report = AdapterConformanceHarness().exercise(
            self.adapter,
            (self.assignment,),
            allow_native_execution=True,
        )
        self.assertTrue(report.passed)

    def test_read_only_evidence_binds_adapter_source_and_metadata(self):
        document = inspect_adapter_evidence(
            self.adapter,
            "tests.test_conformance:create_invalid_adapter",
            observed_at_ms=1_000,
        )
        implementation = document["adapter"]["implementation"]
        path = Path(implementation["path"])
        self.assertEqual(implementation["sha256"], sha256(path.read_bytes()).hexdigest())
        self.assertEqual(document["mode"], "read_only")
        self.assertEqual(document["observed_at_ms"], 1_000)
        self.assertTrue(document["passed"])
        self.assertEqual(document["robot"]["robot_id"], self.manifest.robot_id)

    def test_adapter_cli_writes_evidence_and_reports_conformance_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "evidence" / "adapter.json"
            output = StringIO()
            with redirect_stdout(output):
                status = adapter_conformance_main(
                    [
                        "inspect",
                        "--adapter",
                        "examples.read_only_adapter:create_adapter",
                        "--output",
                        str(output_path),
                    ]
                )
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(output.getvalue()), json.loads(output_path.read_text()))
            self.assertTrue(json.loads(output.getvalue())["passed"])

            output = StringIO()
            with redirect_stdout(output):
                status = adapter_conformance_main(
                    [
                        "inspect",
                        "--adapter",
                        "tests.test_conformance:create_invalid_adapter",
                    ]
                )
            self.assertEqual(status, 1)
            self.assertFalse(json.loads(output.getvalue())["passed"])

    def test_adapter_cli_returns_controlled_error_for_unloadable_factory(self):
        errors = StringIO()
        with redirect_stderr(errors):
            status = adapter_conformance_main(
                ["inspect", "--adapter", "missing.module:create_adapter"]
            )
        self.assertEqual(status, 2)
        self.assertIn("cannot be loaded", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
