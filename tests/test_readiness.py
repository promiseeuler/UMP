import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.cli import readiness_main
from ump.readiness import _validate_qualification_result, load_readiness_report


ROOT = Path(__file__).parents[1]


class ReadinessTests(unittest.TestCase):
    def test_matrix_covers_every_prd_requirement_and_evidence_exists(self):
        report = load_readiness_report(ROOT)
        self.assertEqual(report["total"], 41)
        self.assertEqual(sum(report["counts"].values()), 41)
        self.assertTrue(report["functional_ready"])
        self.assertFalse(report["production_ready"])
        self.assertFalse(report["ready"])
        self.assertEqual(report["counts"], {"implemented": 41})
        self.assertEqual(report["qualification"]["total"], 8)
        self.assertEqual(report["qualification"]["counts"], {"pending": 8})

    def test_matrix_drift_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            (root / "compliance").mkdir()
            (root / "docs" / "PRD.md").write_text("`AWR-01`\n")
            (root / "compliance" / "requirements.json").write_text(
                json.dumps({"requirements": []})
            )
            with self.assertRaisesRegex(ValueError, "matrix drift"):
                load_readiness_report(root)

    def test_cli_fails_closed_for_pending_production_gates(self):
        with patch("builtins.print") as output:
            exit_code = readiness_main([str(ROOT)])
        self.assertEqual(exit_code, 1)
        report = json.loads(output.call_args.args[0])
        self.assertTrue(report["functional_ready"])
        self.assertFalse(report["production_ready"])
        self.assertFalse(report["ready"])
        self.assertEqual(report["counts"], {"implemented": 41})

    def test_functional_only_mode_does_not_claim_production_readiness(self):
        with patch("builtins.print") as output:
            self.assertEqual(
                readiness_main([str(ROOT), "--functional-only"]),
                0,
            )
        self.assertFalse(json.loads(output.call_args.args[0])["ready"])

    def test_validation_only_mode_passes_for_complete_matrix(self):
        with patch("builtins.print"):
            self.assertEqual(readiness_main([str(ROOT), "--validate-only"]), 0)

    def test_passed_qualification_gate_requires_result_evidence(self):
        qualification = ROOT / "compliance" / "qualification.json"
        document = json.loads(qualification.read_text())
        document["gates"][0]["status"] = "passed"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            (root / "compliance").mkdir()
            (root / "docs" / "PRD.md").write_text(
                (ROOT / "docs" / "PRD.md").read_text()
            )
            (root / "compliance" / "requirements.json").write_text(
                (ROOT / "compliance" / "requirements.json").read_text()
            )
            (root / "compliance" / "qualification.json").write_text(
                json.dumps(document)
            )
            with self.assertRaisesRegex(ValueError, "lacks results"):
                load_readiness_report(root, strict_evidence=False)

    def test_qualification_evidence_paths_cannot_escape_project(self):
        qualification = ROOT / "compliance" / "qualification.json"
        document = json.loads(qualification.read_text())
        document["gates"][0]["implementation_evidence"] = ["../outside.json"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            (root / "compliance").mkdir()
            (root / "docs" / "PRD.md").write_text(
                (ROOT / "docs" / "PRD.md").read_text()
            )
            (root / "compliance" / "requirements.json").write_text(
                (ROOT / "compliance" / "requirements.json").read_text()
            )
            (root / "compliance" / "qualification.json").write_text(
                json.dumps(document)
            )
            with self.assertRaisesRegex(ValueError, "escapes the project root"):
                load_readiness_report(root, strict_evidence=False)

    def test_passed_gate_uses_domain_verifier_and_gate_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "evidence.json"
            evidence.write_text("{}")

            with patch(
                "ump.readiness.validate_pilot_bundle",
                return_value={"phase": "read_only"},
            ):
                with self.assertRaisesRegex(ValueError, "supervised assignment"):
                    _validate_qualification_result(
                        root, "QAL-HARDWARE-PILOT", "evidence.json"
                    )

            with patch(
                "ump.readiness.validate_adapter_evidence",
                return_value={"passed": True, "robot_id": None},
            ):
                with self.assertRaisesRegex(ValueError, "robot-bound"):
                    _validate_qualification_result(
                        root, "QAL-ADAPTER-CONFORMANCE", "evidence.json"
                    )

            with patch(
                "ump.readiness.validate_review_bundle",
                return_value={
                    "passed": True,
                    "review_type": "safety",
                },
            ):
                with self.assertRaisesRegex(ValueError, "security review"):
                    _validate_qualification_result(
                        root, "QAL-SECURITY-REVIEW", "evidence.json"
                    )

    def test_passed_gate_rejects_unrelated_existing_result_file(self):
        qualification = json.loads(
            (ROOT / "compliance" / "qualification.json").read_text()
        )
        qualification["gates"][0]["status"] = "passed"
        qualification["gates"][0]["result_evidence"] = ["evidence/result.json"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            (root / "compliance").mkdir()
            (root / "evidence").mkdir()
            (root / "docs" / "PRD.md").write_text(
                (ROOT / "docs" / "PRD.md").read_text()
            )
            (root / "compliance" / "requirements.json").write_text(
                (ROOT / "compliance" / "requirements.json").read_text()
            )
            (root / "compliance" / "qualification.json").write_text(
                json.dumps(qualification)
            )
            (root / "evidence" / "result.json").write_text("{}")

            with self.assertRaisesRegex(
                ValueError, "invalid qualification result for QAL-ROS2-NATIVE"
            ):
                load_readiness_report(root, strict_evidence=False)


if __name__ == "__main__":
    unittest.main()
