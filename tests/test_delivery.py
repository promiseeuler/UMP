import sys
import sqlite3
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.delivery import InboxConflictError, OutboxFullError, SqliteInbox, SqliteOutbox
from ump.transport import SAFETY_STREAM, encode_envelope, make_envelope


def envelope(sequence: int, *, session_id: str = "session-1"):
    return make_envelope(
        "state",
        "robot-1",
        session_id,
        sequence,
        1_000 + sequence,
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


class OutboxTests(unittest.TestCase):
    def test_operational_backpressure_preserves_configured_safety_capacity(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            outbox = SqliteOutbox(
                Path(temporary_directory) / "outbox.sqlite3",
                maximum_pending=2,
                safety_reserve=1,
            )
            outbox.enqueue_many((("peer-1", envelope(1)),), 1_000)
            with self.assertRaisesRegex(OutboxFullError, "preserving safety"):
                outbox.enqueue_many((("peer-1", envelope(2)),), 1_001)
            safety = replace(
                envelope(1),
                message_id="safety-message",
                stream=SAFETY_STREAM,
            )
            outbox.enqueue_many((("peer-1", safety),), 1_002)
            self.assertEqual(outbox.metrics().pending, 2)
            outbox.close()

    def test_legacy_outbox_rows_migrate_as_operational_and_remain_pending(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "legacy-outbox.sqlite3"
            message = envelope(7)
            connection = sqlite3.connect(path)
            connection.execute(
                """
                CREATE TABLE outbound_messages (
                    queue_order INTEGER PRIMARY KEY AUTOINCREMENT,
                    peer_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    envelope BLOB NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    next_attempt_ms INTEGER NOT NULL,
                    last_error TEXT,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    UNIQUE(peer_id, message_id),
                    UNIQUE(peer_id, session_id, sequence)
                )
                """
            )
            connection.execute(
                """
                INSERT INTO outbound_messages
                (peer_id, message_id, session_id, sequence, envelope, status,
                 attempts, next_attempt_ms, created_at_ms, updated_at_ms)
                VALUES (?, ?, ?, ?, ?, 'pending', 0, 1000, 1000, 1000)
                """,
                (
                    "peer-1",
                    message.message_id,
                    message.session_id,
                    message.sequence,
                    encode_envelope(message),
                ),
            )
            connection.commit()
            connection.close()

            outbox = SqliteOutbox(path)
            self.assertEqual(
                [item.envelope for item in outbox.ready(1_000)], [message]
            )
            safety = replace(
                envelope(7),
                message_id="safety-message",
                stream=SAFETY_STREAM,
            )
            outbox.enqueue_many((("peer-1", safety),), 1_001)
            self.assertEqual(
                outbox.ready(1_001, stream=SAFETY_STREAM)[0].envelope,
                safety,
            )
            outbox.close()

    def test_safety_stream_bypasses_operational_head_without_breaking_lane_fifo(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            outbox = SqliteOutbox(Path(temporary_directory) / "outbox.sqlite3")
            operational = envelope(1)
            safety = replace(
                envelope(1),
                message_id="safety-message",
                stream=SAFETY_STREAM,
                payload={**envelope(1).payload, "safety": "protective_stop"},
            )
            outbox.enqueue_many(
                (("peer-1", operational), ("peer-1", safety)), 1_000
            )
            self.assertEqual(
                [item.envelope.stream for item in outbox.ready(1_000)],
                [SAFETY_STREAM, "operational"],
            )
            self.assertEqual(
                [item.envelope for item in outbox.ready(1_000, stream=SAFETY_STREAM)],
                [safety],
            )
            outbox.delivered("peer-1", safety.message_id, 1_001)
            self.assertEqual(
                [item.envelope for item in outbox.ready(1_001, stream="operational")],
                [operational],
            )
            outbox.close()

    def test_pending_delivery_survives_restart_and_preserves_peer_fifo(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "outbox.sqlite3"
            first_message = envelope(9, session_id="old-session")
            second_message = envelope(1, session_id="new-session")
            first = SqliteOutbox(path)
            first.enqueue_many(
                (("peer-1", first_message), ("peer-1", second_message)), 1_000
            )
            first.close()

            reopened = SqliteOutbox(path)
            ready = reopened.ready(1_000)
            self.assertEqual([item.envelope for item in ready], [first_message])
            reopened.delivered("peer-1", first_message.message_id, 1_001)
            self.assertEqual(
                [item.envelope for item in reopened.ready(1_001)], [second_message]
            )
            reopened.close()

    def test_failed_attempt_uses_backoff_and_records_metrics(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            outbox = SqliteOutbox(Path(temporary_directory) / "outbox.sqlite3")
            message = envelope(1)
            outbox.enqueue_many((("peer-1", message),), 1_000)
            outbox.failed("peer-1", message.message_id, "network unavailable", 1_000)
            self.assertEqual(outbox.ready(1_249), ())
            self.assertEqual(len(outbox.ready(1_250)), 1)
            self.assertEqual(outbox.metrics().failed_attempts, 1)
            outbox.close()

    def test_capacity_failure_does_not_partially_enqueue_batch(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            outbox = SqliteOutbox(
                Path(temporary_directory) / "outbox.sqlite3", maximum_pending=1
            )
            with self.assertRaisesRegex(OutboxFullError, "full"):
                outbox.enqueue_many(
                    (("peer-1", envelope(1)), ("peer-2", envelope(1))), 1_000
                )
            self.assertEqual(outbox.metrics().pending, 0)
            outbox.close()


class InboxTests(unittest.TestCase):
    def test_safety_stream_is_not_blocked_by_same_source_operational_handler(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            operational_started = threading.Event()
            release_operational = threading.Event()
            safety_started = threading.Event()

            def handler(item):
                if item.stream == SAFETY_STREAM:
                    safety_started.set()
                else:
                    operational_started.set()
                    release_operational.wait(2.0)

            inbox = SqliteInbox(
                Path(temporary_directory) / "inbox.sqlite3", handler, worker_count=2
            )
            operational = envelope(1)
            safety = replace(
                envelope(1),
                message_id="safety-message",
                stream=SAFETY_STREAM,
                payload={**envelope(1).payload, "safety": "protective_stop"},
            )
            inbox.enqueue(operational, 1_000)
            self.assertTrue(operational_started.wait(2.0))
            inbox.enqueue(safety, 1_001)
            self.assertTrue(safety_started.wait(2.0))
            release_operational.set()
            deadline = time.monotonic() + 2.0
            while inbox.counts().get("processed", 0) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(inbox.counts(), {"processed": 2})
            inbox.close()
    def test_application_failure_is_retained_for_operator_inspection(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            def fail(_):
                raise ValueError("invalid application transition")

            inbox = SqliteInbox(
                Path(temporary_directory) / "inbox.sqlite3", fail, worker_count=1
            )
            message = envelope(1)
            inbox.enqueue(message, 1_000)
            deadline = time.monotonic() + 2.0
            while inbox.counts().get("failed", 0) != 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            failure = inbox.failures()[0]
            self.assertEqual(failure.message_id, message.message_id)
            self.assertIn("invalid application transition", failure.error)
            inbox.close()

    def test_staged_message_is_invisible_until_activated(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            processed = threading.Event()
            inbox = SqliteInbox(
                Path(temporary_directory) / "inbox.sqlite3",
                lambda _: processed.set(),
                worker_count=1,
            )
            message = envelope(1)
            inbox.enqueue(message, 1_000, ready=False)
            self.assertFalse(processed.wait(0.1))
            self.assertEqual(inbox.counts(), {"staged": 1})
            inbox.activate(message.message_id)
            self.assertTrue(processed.wait(2.0))
            inbox.close()

    def test_duplicate_message_is_idempotent_and_conflicting_content_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            processed = threading.Event()
            inbox = SqliteInbox(
                Path(temporary_directory) / "inbox.sqlite3",
                lambda _: processed.set(),
                worker_count=1,
            )
            message = envelope(1)
            self.assertTrue(inbox.enqueue(message, 1_000))
            self.assertFalse(inbox.enqueue(message, 1_001))
            changed = replace(message, payload={**message.payload, "summary": "Changed"})
            with self.assertRaisesRegex(InboxConflictError, "different content"):
                inbox.enqueue(changed, 1_002)
            self.assertTrue(processed.wait(2.0))
            inbox.close()

    def test_workers_preserve_source_order_while_allowing_concurrency(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            first_started = threading.Event()
            release_first = threading.Event()
            second_started = threading.Event()
            other_source_started = threading.Event()

            def handler(item):
                if item.source_id == "robot-1" and item.sequence == 1:
                    first_started.set()
                    release_first.wait(2.0)
                elif item.source_id == "robot-1":
                    second_started.set()
                else:
                    other_source_started.set()

            inbox = SqliteInbox(
                Path(temporary_directory) / "inbox.sqlite3", handler, worker_count=2
            )
            first = envelope(1)
            second = envelope(2)
            other = replace(
                envelope(1),
                message_id="other-message",
                source_id="robot-2",
                session_id="robot-2-session",
                payload={**envelope(1).payload, "robot_id": "robot-2"},
            )
            inbox.enqueue(first, 1_000)
            inbox.enqueue(second, 1_001)
            inbox.enqueue(other, 1_002)
            self.assertTrue(first_started.wait(2.0))
            self.assertTrue(other_source_started.wait(2.0))
            self.assertFalse(second_started.is_set())
            release_first.set()
            self.assertTrue(second_started.wait(2.0))
            deadline = time.monotonic() + 2.0
            while inbox.counts().get("processed", 0) < 3 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(inbox.counts(), {"processed": 3})
            inbox.close()


if __name__ == "__main__":
    unittest.main()
