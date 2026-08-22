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
            "AuthorityLeaseSummary",
            "AuthorityReadError",
            "Availability",
            "CommunicationLossHandler",
            "InspectorStoreError",
            "LanEvidenceValidationError",
            "NetworkDiagnosticsError",
            "Coordinator",
            "CoordinatorService",
            "CredentialGenerationSummary",
            "default_local_topology",
            "deployment_topology_schema",
            "Mode",
            "Outcome",
            "Plan",
            "Planner",
            "PlanStep",
            "PlanValidationError",
            "ParticipantService",
            "ReadOnlyInspectorStore",
            "RobotManifest",
            "RobotState",
            "Safety",
            "RunSnapshot",
            "RunStatus",
            "RunSummary",
            "StepStatus",
            "standard_capabilities",
            "adapter_evidence_schema",
            "standard_capability",
            "load_adapter",
            "goal_batch_schema",
            "goal_schema",
            "goal_validation_report",
            "generate_deployment_bundle",
            "inspect_adapter_evidence",
            "inspect_network_databases",
            "load_planner",
            "network_config_schema",
            "lan_evidence_schema",
            "read_run_summaries",
            "read_authority_events",
            "read_authority_lease",
            "read_authority_leases",
            "read_credential_events",
            "read_credential_generation",
            "read_credential_generations",
            "shared_goal_from_document",
            "shared_goals_from_document",
            "validate_lan_evidence_bundle",
            "validate_network_config",
            "verify_local_awareness",
            "validate_plan",
            "validate_adapter_evidence",
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
