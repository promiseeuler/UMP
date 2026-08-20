from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

import ump
from ump.adapter import RobotAdapter


ROOT = Path(__file__).parents[1]


class PublicApiTests(unittest.TestCase):
    def test_manufacturer_contract_is_available_from_supported_imports(self):
        self.assertIs(ump.RobotAdapter, RobotAdapter)
        for public_name in (
            "AdapterConformanceHarness",
            "AdapterEvidenceValidationError",
            "Assignment",
            "AssignmentStatus",
            "Availability",
            "CommunicationLossHandler",
            "LanEvidenceValidationError",
            "Coordinator",
            "CoordinatorService",
            "Mode",
            "Outcome",
            "Plan",
            "Planner",
            "PlanStep",
            "PlanValidationError",
            "PilotValidationError",
            "ParticipantService",
            "RobotManifest",
            "RobotState",
            "Safety",
            "RunSnapshot",
            "RunStatus",
            "RunSummary",
            "Ros2EvidenceValidationError",
            "ReviewValidationError",
            "ReleaseEvidenceValidationError",
            "StepStatus",
            "standard_capabilities",
            "adapter_evidence_schema",
            "standard_capability",
            "load_adapter",
            "goal_batch_schema",
            "goal_schema",
            "goal_validation_report",
            "inspect_adapter_evidence",
            "load_planner",
            "network_config_schema",
            "lan_evidence_schema",
            "pilot_schema",
            "read_run_summaries",
            "review_schema",
            "release_evidence_schema",
            "shared_goal_from_document",
            "shared_goals_from_document",
            "validate_pilot_bundle",
            "validate_lan_evidence_bundle",
            "validate_network_config",
            "validate_plan",
            "validate_adapter_evidence",
            "validate_ros2_smoke_report",
            "validate_review_bundle",
            "validate_release_evidence_bundle",
        ):
            self.assertIn(public_name, ump.__all__)
            self.assertTrue(hasattr(ump, public_name))

    def test_read_only_example_is_conformant_and_advertises_no_capabilities(self):
        from examples.read_only_adapter import ReadOnlyAdapter

        adapter = ReadOnlyAdapter()
        self.assertIsInstance(adapter, RobotAdapter)
        self.assertEqual(adapter.manifest().capabilities, ())
        report = ump.AdapterConformanceHarness().inspect(adapter)
        self.assertTrue(report.passed)

    def test_read_only_example_runs_from_source_checkout(self):
        completed = subprocess.run(
            [sys.executable, "examples/read_only_adapter.py"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn('"passed": true', completed.stdout)


if __name__ == "__main__":
    unittest.main()
