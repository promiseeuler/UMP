import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.cli import credentials_main
from ump.credentials import (
    CredentialError,
    SqliteCredentialStore,
    read_active_credential,
)


def openssl(*arguments: str, directory: Path) -> None:
    subprocess.run(
        ["openssl", *arguments], cwd=directory, check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def issue(directory: Path, robot_id: str, name: str) -> tuple[Path, Path, Path]:
    ca_key, ca = directory / "ca.key", directory / "ca.pem"
    if not ca.exists():
        openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(ca_key),
                "-out", str(ca), "-days", "2", "-subj", "/CN=UMP Test CA", directory=directory)
    key, request, certificate = directory / f"{name}.key", directory / f"{name}.csr", directory / f"{name}.pem"
    extensions = directory / f"{name}.ext"
    extensions.write_text(f"subjectAltName=URI:urn:ump:robot:{robot_id}\n")
    openssl("req", "-new", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key),
            "-out", str(request), "-subj", f"/CN={robot_id}", directory=directory)
    openssl("x509", "-req", "-in", str(request), "-CA", str(ca), "-CAkey", str(ca_key),
            "-CAcreateserial", "-out", str(certificate), "-days", "1", "-sha256",
            "-extfile", str(extensions), directory=directory)
    return certificate, key, ca


class CredentialStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = SqliteCredentialStore("robot-1", self.root / "credentials.db", self.root / "managed")

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def test_enroll_activate_rotate_and_revoke_are_durable(self):
        first = self.store.enroll(*issue(self.root, "robot-1", "first"), int(time.time() * 1_000))
        self.assertEqual(first.status, "staged")
        self.assertEqual(first.private_key_path.stat().st_mode & 0o777, 0o600)
        self.store.activate(first.generation, int(time.time() * 1_000))
        second = self.store.enroll(*issue(self.root, "robot-1", "second"), int(time.time() * 1_000))
        self.store.activate(second.generation, int(time.time() * 1_000))
        self.assertEqual(self.store.get(first.generation).status, "retired")
        self.assertEqual(self.store.active().generation, second.generation)
        self.store.revoke(second.fingerprint_sha256, "compromised", int(time.time() * 1_000))
        self.assertTrue(self.store.is_revoked(second.fingerprint_sha256))
        self.assertIsNone(self.store.active())
        self.assertEqual([event["event_type"] for event in self.store.events()],
                         ["enrolled", "activated", "enrolled", "activated", "revoked"])

    def test_wrong_identity_and_mismatched_key_fail_closed(self):
        certificate, key, ca = issue(self.root, "robot-2", "wrong")
        with self.assertRaisesRegex(CredentialError, "configured UMP robot identity"):
            self.store.enroll(certificate, key, ca, int(time.time() * 1_000))
        right_certificate, _, _ = issue(self.root, "robot-1", "right")
        with self.assertRaisesRegex(CredentialError, "does not match"):
            self.store.enroll(right_certificate, key, ca, int(time.time() * 1_000))

    def test_revoked_generation_cannot_be_reactivated(self):
        generation = self.store.enroll(*issue(self.root, "robot-1", "leaf"), int(time.time() * 1_000))
        self.store.revoke(generation.fingerprint_sha256, "retired", int(time.time() * 1_000))
        with self.assertRaisesRegex(CredentialError, "revoked"):
            self.store.activate(generation.generation, int(time.time() * 1_000))

    def test_runtime_requires_configured_files_from_active_generation(self):
        generation = self.store.enroll(
            *issue(self.root, "robot-1", "runtime"), int(time.time() * 1_000)
        )
        with self.assertRaisesRegex(CredentialError, "no active"):
            self.store.require_active_bundle(
                generation.certificate_path,
                generation.private_key_path,
                generation.ca_path,
            )
        self.store.activate(generation.generation, int(time.time() * 1_000))
        self.assertEqual(
            self.store.require_active_bundle(
                generation.certificate_path,
                generation.private_key_path,
                generation.ca_path,
            ).generation,
            generation.generation,
        )
        with self.assertRaisesRegex(CredentialError, "do not match"):
            self.store.require_active_bundle(
                self.root / "different.pem",
                generation.private_key_path,
                generation.ca_path,
            )
        self.store.revoke(
            generation.fingerprint_sha256,
            "runtime revocation",
            int(time.time() * 1_000),
        )
        with self.assertRaisesRegex(CredentialError, "no active"):
            self.store.require_active_bundle(
                generation.certificate_path,
                generation.private_key_path,
                generation.ca_path,
            )

    def test_runtime_rejects_active_generation_outside_validity_window(self):
        generation = self.store.enroll(
            *issue(self.root, "robot-1", "expiry"), int(time.time() * 1_000)
        )
        self.store.activate(generation.generation, int(time.time() * 1_000))
        with self.assertRaisesRegex(CredentialError, "not yet valid"):
            self.store.require_active_bundle(
                generation.certificate_path,
                generation.private_key_path,
                generation.ca_path,
                now_ms=generation.not_before_ms - 1,
            )
        with self.assertRaisesRegex(CredentialError, "expired"):
            self.store.require_active_bundle(
                generation.certificate_path,
                generation.private_key_path,
                generation.ca_path,
                now_ms=generation.not_after_ms,
            )

    def test_read_only_active_credential_inspection_matches_runtime_check(self):
        generation = self.store.enroll(
            *issue(self.root, "robot-1", "preflight"), int(time.time() * 1_000)
        )
        self.store.activate(generation.generation, int(time.time() * 1_000))
        inspected = read_active_credential(
            self.root / "credentials.db",
            "robot-1",
            generation.certificate_path,
            generation.private_key_path,
            generation.ca_path,
            now_ms=generation.not_before_ms,
        )
        self.assertEqual(inspected.generation, generation.generation)

        missing = self.root / "missing.db"
        with self.assertRaisesRegex(CredentialError, "cannot be inspected"):
            read_active_credential(
                missing,
                "robot-1",
                generation.certificate_path,
                generation.private_key_path,
                generation.ca_path,
            )
        self.assertFalse(missing.exists())

    def test_duplicate_enrollment_fails_with_domain_error(self):
        bundle = issue(self.root, "robot-1", "duplicate")
        self.store.enroll(*bundle, int(time.time() * 1_000))
        with self.assertRaisesRegex(CredentialError, "already enrolled"):
            self.store.enroll(*bundle, int(time.time() * 1_000))

    def test_cli_reports_staged_generation(self):
        certificate, key, ca = issue(self.root, "robot-1", "cli")
        with patch("builtins.print") as output:
            result = credentials_main([
                "--robot-id", "robot-1", "--database", str(self.root / "cli.db"),
                "--directory", str(self.root / "cli-managed"), "enroll",
                "--certificate", str(certificate), "--private-key", str(key), "--ca", str(ca),
            ])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.call_args.args[0])["status"], "staged")


if __name__ == "__main__":
    unittest.main()
