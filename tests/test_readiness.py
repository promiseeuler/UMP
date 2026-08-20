import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.cli import readiness_main
from ump.readiness import load_readiness_report


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


if __name__ == "__main__":
    unittest.main()
