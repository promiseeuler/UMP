import json
from hashlib import sha256
from pathlib import Path
import subprocess
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from io import StringIO
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.cli import credentials_main
from ump.credentials import (
    CredentialGenerationSummary,
    CredentialError,
    SqliteCredentialStore,
    read_active_credential,
    read_credential_events,
    read_credential_generation,
    read_credential_generations,
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

    def test_read_only_inventory_reports_effective_status_and_is_non_mutating(self):
        database = self.root / "inventory.db"
        managed = self.root / "inventory-managed"
        store = SqliteCredentialStore("robot-1", database, managed)
        first_bundle = issue(self.root, "robot-1", "inventory-first")
        second_bundle = issue(self.root, "robot-1", "inventory-second")
        now_ms = int(time.time() * 1_000)
        first = store.enroll(*first_bundle, now_ms)
        store.activate(first.generation, now_ms)
        second = store.enroll(*second_bundle, now_ms)
        store.activate(second.generation, now_ms)
        store.revoke(second.fingerprint_sha256, "operator request", now_ms)
        store.close()
        before = sha256(database.read_bytes()).hexdigest()
        files_before = sorted(item.name for item in self.root.iterdir())

        summaries = read_credential_generations(database, "robot-1", now_ms)
        self.assertTrue(all(isinstance(item, CredentialGenerationSummary) for item in summaries))
        self.assertEqual(
            {item.generation.generation: item.effective_status for item in summaries},
            {first.generation: "retired", second.generation: "revoked"},
        )
        self.assertEqual(
            read_credential_generation(
                database, "robot-1", second.generation, now_ms
            ).effective_status,
            "revoked",
        )
        self.assertEqual(
            [item.generation.generation for item in read_credential_generations(
                database,
                "robot-1",
                now_ms,
                effective_status="retired",
                limit=1,
            )],
            [first.generation],
        )
        self.assertEqual(before, sha256(database.read_bytes()).hexdigest())
        self.assertEqual(files_before, sorted(item.name for item in self.root.iterdir()))
        with self.assertRaises(CredentialError):
            read_credential_generation(database, "robot-2", first.generation, now_ms)

        missing = self.root / "missing" / "credentials.db"
        with self.assertRaisesRegex(CredentialError, "does not exist"):
            read_credential_generations(missing, "robot-1", now_ms)
        self.assertFalse(missing.parent.exists())

    def test_events_are_robot_scoped_and_latest_limit_remains_chronological(self):
        database = self.root / "shared-credentials.db"
        first_store = SqliteCredentialStore(
            "robot-1", database, self.root / "robot-1-managed"
        )
        first = first_store.enroll(
            *issue(self.root, "robot-1", "event-first"),
            int(time.time() * 1_000),
        )
        now_ms = int(time.time() * 1_000)
        first_store.activate(first.generation, now_ms)
        first_store.revoke(first.fingerprint_sha256, "operator request", now_ms)
        first_store.close()
        second_store = SqliteCredentialStore(
            "robot-2", database, self.root / "robot-2-managed"
        )
        second_store.enroll(
            *issue(self.root, "robot-2", "event-second"),
            int(time.time() * 1_000),
        )
        second_store.close()

        first_events = read_credential_events(database, "robot-1", limit=2)
        second_events = read_credential_events(database, "robot-2")
        self.assertEqual(
            [item["event_type"] for item in first_events], ["activated", "revoked"]
        )
        self.assertEqual(
            [item["event_type"] for item in second_events], ["enrolled"]
        )

    def test_legacy_events_migrate_only_when_robot_ownership_is_provable(self):
        database = self.root / "legacy-credentials.db"
        connection = sqlite3.connect(database)
        connection.executescript(
            """
            CREATE TABLE credential_generations (
                generation INTEGER PRIMARY KEY AUTOINCREMENT,
                robot_id TEXT NOT NULL,
                fingerprint_sha256 TEXT NOT NULL UNIQUE,
                certificate_path TEXT NOT NULL,
                private_key_path TEXT NOT NULL,
                ca_path TEXT NOT NULL,
                not_before_ms INTEGER NOT NULL,
                not_after_ms INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                activated_at_ms INTEGER
            );
            CREATE TABLE revoked_certificates (
                fingerprint_sha256 TEXT PRIMARY KEY,
                reason TEXT NOT NULL,
                revoked_at_ms INTEGER NOT NULL
            );
            CREATE TABLE credential_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                occurred_at_ms INTEGER NOT NULL,
                detail_json TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO credential_generations VALUES "
            "(1, 'robot-1', ?, 'cert', 'key', 'ca', 1, 10000, 'staged', 1, NULL)",
            ("a" * 64,),
        )
        connection.execute(
            "INSERT INTO credential_events(event_type, occurred_at_ms, detail_json) "
            "VALUES ('enrolled', 1, ?)",
            (json.dumps({"generation": 1, "fingerprint_sha256": "a" * 64}),),
        )
        connection.commit()
        connection.close()

        migrated = SqliteCredentialStore(
            "robot-1", database, self.root / "legacy-managed"
        )
        migrated.close()
        self.assertEqual(
            read_credential_events(database, "robot-1")[0]["event_type"],
            "enrolled",
        )

    def test_legacy_event_migration_rolls_back_when_ownership_is_unknown(self):
        database = self.root / "ambiguous-legacy-credentials.db"
        connection = sqlite3.connect(database)
        connection.executescript(
            """
            CREATE TABLE credential_generations (
                generation INTEGER PRIMARY KEY AUTOINCREMENT,
                robot_id TEXT NOT NULL,
                fingerprint_sha256 TEXT NOT NULL UNIQUE,
                certificate_path TEXT NOT NULL,
                private_key_path TEXT NOT NULL,
                ca_path TEXT NOT NULL,
                not_before_ms INTEGER NOT NULL,
                not_after_ms INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                activated_at_ms INTEGER
            );
            CREATE TABLE revoked_certificates (
                fingerprint_sha256 TEXT PRIMARY KEY,
                reason TEXT NOT NULL,
                revoked_at_ms INTEGER NOT NULL
            );
            CREATE TABLE credential_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                occurred_at_ms INTEGER NOT NULL,
                detail_json TEXT NOT NULL
            );
            INSERT INTO credential_events(event_type, occurred_at_ms, detail_json)
            VALUES ('enrolled', 1, '{}');
            """
        )
        connection.close()

        with self.assertRaisesRegex(CredentialError, "ownership is unknown"):
            SqliteCredentialStore(
                "robot-1", database, self.root / "ambiguous-legacy-managed"
            )

        connection = sqlite3.connect(database)
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(credential_events)")
        }
        connection.close()
        self.assertNotIn("robot_id", columns)

    def test_cli_reports_staged_generation(self):
        certificate, key, ca = issue(self.root, "robot-1", "cli")
        with patch("builtins.print") as output:
            result = credentials_main([
                "--robot-id", "robot-1", "--database", str(self.root / "cli.db"),
                "--directory", str(self.root / "cli-managed"), "enroll",
                "--certificate", str(certificate), "--private-key", str(key), "--ca", str(ca),
            ])
        self.assertEqual(result, 0)
        enrolled = json.loads(output.call_args.args[0])
        self.assertEqual(enrolled["status"], "staged")

        common = [
            "--robot-id",
            "robot-1",
            "--database",
            str(self.root / "cli.db"),
        ]
        with patch("builtins.print") as output:
            self.assertEqual(credentials_main(common + ["list"]), 0)
        inventory = json.loads(output.call_args.args[0])
        self.assertEqual(inventory[0]["generation"], enrolled["generation"])
        self.assertEqual(inventory[0]["effective_status"], "staged")

        with patch("builtins.print") as output:
            self.assertEqual(
                credentials_main(
                    common + ["show", "--generation", str(enrolled["generation"])]
                ),
                0,
            )
        self.assertEqual(json.loads(output.call_args.args[0])["stored_status"], "staged")

        with patch("builtins.print") as output:
            self.assertEqual(credentials_main(common + ["events", "--limit", "1"]), 0)
        self.assertEqual(json.loads(output.call_args.args[0])[0]["event_type"], "enrolled")

        with patch("builtins.print") as output:
            self.assertEqual(credentials_main(common + ["active"]), 0)
        self.assertIsNone(json.loads(output.call_args.args[0]))

    def test_cli_read_commands_do_not_create_paths_and_mutations_require_directory(self):
        missing = self.root / "missing" / "credentials.db"
        errors = StringIO()
        with redirect_stderr(errors):
            result = credentials_main(
                [
                    "--robot-id",
                    "robot-1",
                    "--database",
                    str(missing),
                    "list",
                ]
            )
        self.assertEqual(result, 2)
        self.assertIn("does not exist", errors.getvalue())
        self.assertFalse(missing.parent.exists())

        errors = StringIO()
        with redirect_stderr(errors):
            result = credentials_main(
                [
                    "--robot-id",
                    "robot-1",
                    "--database",
                    str(self.root / "new.db"),
                    "activate",
                    "--generation",
                    "1",
                ]
            )
        self.assertEqual(result, 2)
        self.assertIn("--directory is required", errors.getvalue())
        self.assertFalse((self.root / "new.db").exists())


if __name__ == "__main__":
    unittest.main()
