from contextlib import redirect_stderr, redirect_stdout
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ump.cli import review_main
from ump.review import ReviewValidationError, validate_review_bundle


ROOT = Path(__file__).parents[1]
REVISION = "a" * 40


def evidence(directory: Path, name: str, content: str) -> dict[str, str]:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {"artifact": name, "sha256": sha256(content.encode()).hexdigest()}


def manifest(directory: Path, *, conclusion: str = "approved") -> dict:
    return {
        "protocol": "ump.independent-review/v1",
        "review_id": "security-review-1",
        "review_type": "security",
        "subject": {
            "repository": "https://github.com/promiseeuler/UMP",
            "repository_revision": REVISION,
            "scope": "Protocol, network transport, identity, authorization, and audit",
        },
        "reviewer": {
            "name": "External Reviewer",
            "organization": "Independent Robotics Assurance Ltd",
            "independent": True,
            "conflict_disclosure": "No financial or development relationship",
        },
        "conducted_at_ms": 1_776_729_600_000,
        "report_evidence": evidence(directory, "review.pdf", "review report"),
        "independence_attestation": evidence(
            directory, "independence.txt", "signed independence attestation"
        ),
        "findings": [
            {
                "finding_id": "SEC-001",
                "title": "Credential rotation documentation clarification",
                "severity": "low",
                "status": "resolved",
            }
        ],
        "conclusion": conclusion,
    }


def write_manifest(directory: Path, document: dict) -> Path:
    path = directory / "review.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


class ReviewTests(unittest.TestCase):
    def test_approved_review_verifies_independence_and_artifacts(self):
        with TemporaryDirectory() as name:
            directory = Path(name)
            result = validate_review_bundle(
                write_manifest(directory, manifest(directory))
            )

        self.assertTrue(result["valid"])
        self.assertTrue(result["passed"])
        self.assertEqual(result["repository_revision"], REVISION)
        self.assertEqual(result["evidence_artifacts_verified"], 2)

    def test_rejects_unresolved_blocking_and_undocumented_accepted_findings(self):
        with TemporaryDirectory() as name:
            directory = Path(name)
            document = manifest(directory, conclusion="approved_with_conditions")
            document["findings"][0].update(severity="high", status="open")
            with self.assertRaisesRegex(ReviewValidationError, "critical or high"):
                validate_review_bundle(write_manifest(directory, document))

            document["findings"][0]["status"] = "accepted"
            with self.assertRaisesRegex(ReviewValidationError, "disposition"):
                validate_review_bundle(write_manifest(directory, document))

            document["findings"][0]["disposition_evidence"] = evidence(
                directory, "risk-acceptance.txt", "owner accepted residual risk"
            )
            self.assertTrue(
                validate_review_bundle(write_manifest(directory, document))["passed"]
            )

    def test_rejects_false_independence_duplicate_findings_and_tampering(self):
        with TemporaryDirectory() as name:
            directory = Path(name)
            document = manifest(directory)
            document["reviewer"]["independent"] = False
            with self.assertRaisesRegex(ReviewValidationError, "independent"):
                validate_review_bundle(write_manifest(directory, document))

            document = manifest(directory)
            document["findings"].append(dict(document["findings"][0]))
            with self.assertRaisesRegex(ReviewValidationError, "unique"):
                validate_review_bundle(write_manifest(directory, document))

            path = write_manifest(directory, manifest(directory))
            (directory / "review.pdf").write_text("changed")
            with self.assertRaisesRegex(ReviewValidationError, "digest"):
                validate_review_bundle(path)

    def test_rejected_review_is_valid_but_does_not_pass_cli(self):
        with TemporaryDirectory() as name:
            directory = Path(name)
            path = write_manifest(directory, manifest(directory, conclusion="rejected"))
            output = StringIO()
            with redirect_stdout(output):
                status = review_main(["validate", str(path)])
            self.assertEqual(status, 1)
            result = json.loads(output.getvalue())
            self.assertTrue(result["valid"])
            self.assertFalse(result["passed"])

            (directory / "review.pdf").write_text("changed")
            errors = StringIO()
            with redirect_stderr(errors):
                status = review_main(["validate", str(path)])
            self.assertEqual(status, 2)
            self.assertIn("digest", errors.getvalue())

    def test_public_and_packaged_schemas_are_identical(self):
        public = json.loads(
            (ROOT / "schemas" / "ump-independent-review-v1.schema.json").read_text()
        )
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(review_main(["schema"]), 0)
        self.assertEqual(public, json.loads(output.getvalue()))


if __name__ == "__main__":
    unittest.main()
