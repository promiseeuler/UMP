from contextlib import redirect_stderr, redirect_stdout
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.cli import release_evidence_main
from ump.release_evidence import (
    ReleaseEvidenceValidationError,
    release_evidence_schema,
    validate_release_evidence_bundle,
)


ROOT = Path(__file__).parents[1]
VERSION = "0.1.0"
REVISION = "1" * 40
WHEEL = f"universal_machine_protocol-{VERSION}-py3-none-any.whl"
SOURCE = f"universal_machine_protocol-{VERSION}.tar.gz"


def write(path: Path, content: bytes) -> dict[str, object]:
    path.write_bytes(content)
    return {
        "artifact": path.name,
        "sha256": sha256(content).hexdigest(),
        "size_bytes": len(content),
    }


def write_json(path: Path, document: dict) -> dict[str, object]:
    return write(path, json.dumps(document, sort_keys=True).encode())


def bundle(directory: Path) -> Path:
    wheel = write(directory / WHEEL, b"wheel artifact")
    source = write(directory / SOURCE, b"source artifact")
    checksums = write(
        directory / "SHA256SUMS",
        (
            f"{wheel['sha256']}  {WHEEL}\n"
            f"{source['sha256']}  {SOURCE}\n"
        ).encode(),
    )
    install = write_json(
        directory / "install-report.json",
        {
            "profile": "ump.release-install-report/v1",
            "release_id": "ump-v0.1.0",
            "package_version": VERSION,
            "repository_revision": REVISION,
            "checks": {
                "wheel_installed": True,
                "import_smoke": True,
                "demo": True,
                "schema_commands": True,
            },
            "passed": True,
        },
    )
    provenance = write_json(
        directory / "provenance-verification.json",
        {
            "profile": "ump.github-attestation-verification/v1",
            "repository": "promiseeuler/UMP",
            "repository_revision": REVISION,
            "verified_at_ms": 1_776_729_600_000,
            "verification_command": "gh attestation verify dist/* --repo promiseeuler/UMP",
            "subjects": [
                {"filename": WHEEL, "sha256": wheel["sha256"]},
                {"filename": SOURCE, "sha256": source["sha256"]},
            ],
            "passed": True,
        },
    )
    manifest = {
        "profile": "ump.release-evidence/v1",
        "release_id": "ump-v0.1.0",
        "repository": "promiseeuler/UMP",
        "tag": "v0.1.0",
        "package_name": "universal-machine-protocol",
        "package_version": VERSION,
        "repository_revision": REVISION,
        "built_at_ms": 1_776_729_600_000,
        "artifacts": {
            "wheel": wheel,
            "source_distribution": source,
            "checksums": checksums,
            "install_report": install,
            "provenance_verification": provenance,
        },
    }
    path = directory / "release-evidence.json"
    path.write_text(json.dumps(manifest))
    return path


class ReleaseEvidenceTests(unittest.TestCase):
    def test_validates_release_artifact_and_receipt_bindings(self):
        with TemporaryDirectory() as name:
            report = validate_release_evidence_bundle(bundle(Path(name)))
        self.assertTrue(report["valid"])
        self.assertEqual(report["tag"], "v0.1.0")
        self.assertEqual(report["artifacts_verified"], 5)
        self.assertEqual(report["install_checks_verified"], 4)
        self.assertEqual(report["provenance_subjects_verified"], 2)

    def test_rejects_tampering_and_version_mismatch(self):
        with TemporaryDirectory() as name:
            directory = Path(name)
            path = bundle(directory)
            (directory / WHEEL).write_bytes(b"modified")
            with self.assertRaisesRegex(ReleaseEvidenceValidationError, "digest"):
                validate_release_evidence_bundle(path)

            path = bundle(directory)
            document = json.loads(path.read_text())
            document["tag"] = "v0.1.1"
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(ReleaseEvidenceValidationError, "tag"):
                validate_release_evidence_bundle(path)

    def test_rejects_bad_checksum_install_and_provenance_receipts(self):
        mutations = (
            ("checksums", lambda document: document.update(source_distribution="0" * 64), "checksum"),
            ("install", lambda document: document["checks"].update(demo=False), "install"),
            ("provenance", lambda document: document["subjects"].pop(), "provenance"),
        )
        for target, mutate, message in mutations:
            with self.subTest(target=target), TemporaryDirectory() as name:
                directory = Path(name)
                path = bundle(directory)
                manifest = json.loads(path.read_text())
                if target == "checksums":
                    checksum_path = directory / "SHA256SUMS"
                    content = checksum_path.read_text().replace(
                        manifest["artifacts"]["source_distribution"]["sha256"],
                        "0" * 64,
                    )
                    manifest["artifacts"]["checksums"] = write(
                        checksum_path, content.encode()
                    )
                else:
                    receipt_path = directory / (
                        "install-report.json"
                        if target == "install"
                        else "provenance-verification.json"
                    )
                    receipt = json.loads(receipt_path.read_text())
                    mutate(receipt)
                    role = "install_report" if target == "install" else "provenance_verification"
                    manifest["artifacts"][role] = write_json(receipt_path, receipt)
                path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ReleaseEvidenceValidationError, message):
                    validate_release_evidence_bundle(path)

    def test_cli_and_public_schema(self):
        with TemporaryDirectory() as name:
            path = bundle(Path(name))
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(release_evidence_main(["validate", str(path)]), 0)
            self.assertTrue(json.loads(output.getvalue())["valid"])

            path.write_text("{}")
            errors = StringIO()
            with redirect_stderr(errors):
                self.assertEqual(release_evidence_main(["validate", str(path)]), 2)
            self.assertIn("ump-release-evidence:", errors.getvalue())

        public = json.loads(
            (ROOT / "schemas" / "ump-release-evidence-v1.schema.json").read_text()
        )
        self.assertEqual(public, release_evidence_schema())


if __name__ == "__main__":
    unittest.main()
