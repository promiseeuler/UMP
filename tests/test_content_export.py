import csv
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.content_export import EvidenceExportError, export_evidence
from ump.lab import run_scenario


class ContentExportTests(unittest.TestCase):
    def test_scenario_records_truthful_inspector_and_exports_content_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "inspector.sqlite3"
            result_path = root / "scenario.json"
            result = run_scenario(
                workspace=root / "run",
                inspector_database=database,
            ).as_dict()
            result_path.write_text(json.dumps(result), encoding="utf-8")

            report = export_evidence(
                database,
                result_path,
                root / "content",
                generated_at_ms=2_000,
            )

            self.assertTrue(report["passed"])
            self.assertEqual(report["robot_count"], 3)
            evidence = json.loads((root / "content/evidence.json").read_text())
            self.assertEqual(evidence["observed_robot_ids"], [
                "inspector-1", "manipulator-1", "mobile-1"
            ])
            self.assertGreaterEqual(evidence["message_counts"]["manifest"], 3)
            self.assertGreaterEqual(evidence["message_counts"]["outcome"], 3)
            self.assertIn("software simulation", evidence["claim_boundary"])
            rows = list(csv.DictReader(StringIO((root / "content/timeline.csv").read_text())))
            self.assertEqual(len(rows), evidence["event_count"])
            pitch = (root / "content/pitch-summary.md").read_text()
            self.assertIn("What this demonstrates", pitch)
            self.assertIn("Claim boundary", pitch)

    def test_export_refuses_to_replace_existing_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "content").mkdir()
            with self.assertRaisesRegex(EvidenceExportError, "already exists"):
                export_evidence(root / "missing.sqlite3", root / "missing.json", root / "content")


if __name__ == "__main__":
    unittest.main()
