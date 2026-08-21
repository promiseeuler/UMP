from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from ump.cli import simulated_qualification_main
from ump.simulated_qualification import (
    SimulatedQualificationError,
    run_simulated_qualification,
    simulated_qualification_schema,
    validate_simulated_qualification,
)


ROOT = Path(__file__).resolve().parents[1]


class SimulatedQualificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = run_simulated_qualification(ROOT, tls_samples=10)

    def test_run_passes_without_claiming_production_qualification(self):
        self.assertTrue(self.report["passed"])
        self.assertFalse(self.report["scope"]["qualification_substitute"])
        self.assertEqual(len(self.report["checks"]), 10)
        self.assertTrue(self.report["scenarios"]["generated_local_network"]["passed"])
        validate_simulated_qualification(self.report)

    def test_validator_rejects_inconsistent_pass_claim(self):
        document = json.loads(json.dumps(self.report))
        document["checks"][0]["passed"] = False
        with self.assertRaisesRegex(SimulatedQualificationError, "pass flag"):
            validate_simulated_qualification(document)

    def test_validator_rejects_missing_profile_check(self):
        document = json.loads(json.dumps(self.report))
        document["checks"].pop()
        with self.assertRaisesRegex(SimulatedQualificationError, "profile"):
            validate_simulated_qualification(document)

    def test_cli_retains_and_validates_report(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "evidence" / "simulation.json"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                status = simulated_qualification_main(
                    [
                        "run",
                        "--project-root",
                        str(ROOT),
                        "--tls-samples",
                        "10",
                        "--output",
                        str(output),
                    ]
                )
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(stdout.getvalue())["profile"], self.report["profile"])
            self.assertTrue(output.is_file())
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    simulated_qualification_main(["validate", str(output)]), 0
                )

    def test_packaged_schema_has_expected_profile(self):
        schema = simulated_qualification_schema()
        self.assertEqual(
            schema["properties"]["profile"]["const"],
            "ump.simulated-qualification/v2",
        )


if __name__ == "__main__":
    unittest.main()
