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
        self.assertTrue(report["ready"])
        self.assertEqual(report["counts"], {"implemented": 41})

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

    def test_cli_reports_complete_functional_matrix(self):
        with patch("builtins.print") as output:
            exit_code = readiness_main([str(ROOT)])
        self.assertEqual(exit_code, 0)
        report = json.loads(output.call_args.args[0])
        self.assertTrue(report["ready"])
        self.assertEqual(report["counts"], {"implemented": 41})

    def test_validation_only_mode_passes_for_complete_matrix(self):
        with patch("builtins.print"):
            self.assertEqual(readiness_main([str(ROOT), "--validate-only"]), 0)


if __name__ == "__main__":
    unittest.main()
