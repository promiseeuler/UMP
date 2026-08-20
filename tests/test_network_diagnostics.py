from contextlib import redirect_stderr, redirect_stdout
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.cli import network_diagnostics_main
from ump.delivery import SqliteInbox, SqliteOutbox
from ump.network_diagnostics import NetworkDiagnosticsError, inspect_network_databases
from ump.transport import make_envelope


def envelope():
    return make_envelope(
        "state",
        "robot-1",
        "session-1",
        1,
        1_000,
        {
            "robot_id": "robot-1",
            "mode": "idle",
            "safety": "normal",
            "activity": "Waiting",
            "intent": "Observe",
            "progress": 0.0,
            "summary": "Robot is idle.",
            "fresh_for_ms": 2_000,
            "blockers": [],
            "resources": [],
            "assignment_id": None,
        },
    )


def create_stores(directory: Path, *, failed: bool = False) -> tuple[Path, Path]:
    inbox_path = directory / "inbox.sqlite3"
    outbox_path = directory / "outbox.sqlite3"
    outbox = SqliteOutbox(outbox_path, maximum_pending=10, safety_reserve=2)

    def handler(_):
        if failed:
            raise ValueError("handler rejected message")

    inbox = SqliteInbox(inbox_path, handler, worker_count=1)
    if failed:
        message = envelope()
        outbox.enqueue_many((("peer-1", message),), 1_000)
        outbox.failed("peer-1", message.message_id, "peer unavailable", 1_000)
        inbox.enqueue(message, 1_000)
        deadline = time.monotonic() + 2
        while inbox.counts().get("failed", 0) != 1 and time.monotonic() < deadline:
            time.sleep(0.01)
    inbox.close()
    outbox.close()
    return inbox_path, outbox_path


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


class NetworkDiagnosticsTests(unittest.TestCase):
    def test_empty_stores_are_healthy_and_inspection_is_read_only(self):
        with TemporaryDirectory() as name:
            inbox, outbox = create_stores(Path(name))
            before = (digest(inbox), digest(outbox))
            report = inspect_network_databases(
                inbox,
                outbox,
                maximum_pending=10,
                reserved_safety=2,
                observed_at_ms=2_000,
            )
            after = (digest(inbox), digest(outbox))
        self.assertTrue(report["healthy"])
        self.assertEqual(report["profile"], "ump.network-diagnostics/v1")
        self.assertEqual(report["outbox"]["counts"], {})
        self.assertEqual(report["inbox"]["counts"], {})
        self.assertEqual(before, after)

    def test_retry_backlog_and_dead_letter_are_content_free_and_degraded(self):
        with TemporaryDirectory() as name:
            inbox, outbox = create_stores(Path(name), failed=True)
            report = inspect_network_databases(
                inbox,
                outbox,
                maximum_pending=10,
                reserved_safety=2,
                observed_at_ms=62_000,
                maximum_pending_age_ms=60_000,
            )
        self.assertFalse(report["healthy"])
        self.assertFalse(report["checks"]["pending_age_within_limit"])
        self.assertFalse(report["checks"]["inbox_has_no_dead_letters"])
        self.assertEqual(report["outbox"]["pending_by_peer"], {"peer-1": 1})
        self.assertEqual(report["outbox"]["pending_attempts"], 1)
        self.assertEqual(len(report["inbox"]["failures"]), 1)
        encoded = json.dumps(report)
        self.assertNotIn("Robot is idle", encoded)
        self.assertNotIn('"payload"', encoded)

    def test_missing_or_wrong_database_schema_fails_closed(self):
        with TemporaryDirectory() as name:
            directory = Path(name)
            inbox, outbox = create_stores(directory)
            wrong = directory / "wrong.sqlite3"
            wrong.write_bytes(b"not sqlite")
            with self.assertRaises(NetworkDiagnosticsError):
                inspect_network_databases(
                    wrong,
                    outbox,
                    maximum_pending=10,
                    reserved_safety=2,
                    observed_at_ms=1_000,
                )
            with self.assertRaisesRegex(NetworkDiagnosticsError, "does not exist"):
                inspect_network_databases(
                    inbox,
                    directory / "missing.sqlite3",
                    maximum_pending=10,
                    reserved_safety=2,
                    observed_at_ms=1_000,
                )

    def test_cli_uses_network_policy_and_monitoring_exit_codes(self):
        with TemporaryDirectory() as name:
            directory = Path(name)
            inbox, outbox = create_stores(directory)
            for filename in ("cert.pem", "key.pem", "ca.pem"):
                (directory / filename).write_text("fixture")
            config = directory / "network.json"
            config.write_text(
                json.dumps(
                    {
                        "robot_id": "robot-1",
                        "bind_host": "127.0.0.1",
                        "bind_port": 7443,
                        "certificate_path": "cert.pem",
                        "private_key_path": "key.pem",
                        "ca_path": "ca.pem",
                        "replay_database_path": "replay.sqlite3",
                        "inbox_database_path": inbox.name,
                        "outbox_database_path": outbox.name,
                        "maximum_pending_deliveries": 10,
                        "reserved_safety_deliveries": 2,
                        "peers": [],
                    }
                )
            )
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    network_diagnostics_main(["--network", str(config)]), 0
                )
            self.assertEqual(json.loads(output.getvalue())["robot_id"], "robot-1")

            def fail(_):
                raise ValueError("application failure")

            active_inbox = SqliteInbox(inbox, fail, worker_count=1)
            active_inbox.enqueue(envelope(), 1_000)
            deadline = time.monotonic() + 2
            while (
                active_inbox.counts().get("failed", 0) != 1
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            active_inbox.close()
            with redirect_stdout(StringIO()):
                self.assertEqual(
                    network_diagnostics_main(["--network", str(config)]), 1
                )

            errors = StringIO()
            with redirect_stderr(errors):
                self.assertEqual(
                    network_diagnostics_main(
                        ["--network", str(directory / "absent.json")]
                    ),
                    2,
                )
            self.assertIn("ump-network-diagnostics:", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
