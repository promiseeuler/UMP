from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
import hashlib
from pathlib import Path
import sqlite3
from threading import Event, RLock, Thread
import time

from .transport import (
    OPERATIONAL_STREAM,
    SAFETY_STREAM,
    Envelope,
    decode_envelope,
    encode_envelope,
)


class OutboxFullError(RuntimeError):
    pass


class InboxConflictError(RuntimeError):
    pass


class DeliveryStatus(str, Enum):
    PENDING = "pending"
    DELIVERED = "delivered"


@dataclass(frozen=True)
class PendingDelivery:
    peer_id: str
    envelope: Envelope
    attempts: int
    next_attempt_ms: int


@dataclass(frozen=True)
class DeliveryMetrics:
    pending: int
    delivered: int
    failed_attempts: int


@dataclass(frozen=True)
class InboxFailure:
    message_id: str
    source_id: str
    session_id: str
    sequence: int
    error: str
    updated_at_ms: int


class SqliteOutbox:
    """Durable, sequence-ordered outbound queue."""

    def __init__(
        self,
        path: str | Path,
        maximum_pending: int = 10_000,
        safety_reserve: int | None = None,
    ) -> None:
        if maximum_pending < 1:
            raise ValueError("maximum_pending must be positive")
        database_path = Path(path)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.maximum_pending = maximum_pending
        self.safety_reserve = (
            min(64, maximum_pending // 10)
            if safety_reserve is None
            else safety_reserve
        )
        if not 0 <= self.safety_reserve < maximum_pending:
            raise ValueError("safety_reserve must be smaller than maximum_pending")
        self._lock = RLock()
        self._connection = sqlite3.connect(
            database_path, isolation_level=None, check_same_thread=False
        )
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS outbound_messages (
                queue_order INTEGER PRIMARY KEY AUTOINCREMENT,
                peer_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                stream TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                priority INTEGER NOT NULL,
                envelope BLOB NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                next_attempt_ms INTEGER NOT NULL,
                last_error TEXT,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                UNIQUE(peer_id, message_id),
                UNIQUE(peer_id, session_id, stream, sequence)
            )
            """
        )
        self._migrate_outbox_streams()

    def _migrate_outbox_streams(self) -> None:
        columns = {
            row[1] for row in self._connection.execute("PRAGMA table_info(outbound_messages)")
        }
        if "stream" in columns:
            return
        self._connection.executescript(
            """
            BEGIN IMMEDIATE;
            ALTER TABLE outbound_messages RENAME TO outbound_messages_legacy;
            CREATE TABLE outbound_messages (
                queue_order INTEGER PRIMARY KEY AUTOINCREMENT,
                peer_id TEXT NOT NULL,
                message_id TEXT NOT NULL UNIQUE,
                session_id TEXT NOT NULL,
                stream TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                priority INTEGER NOT NULL,
                envelope BLOB NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                next_attempt_ms INTEGER NOT NULL,
                last_error TEXT,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                UNIQUE(peer_id, message_id),
                UNIQUE(peer_id, session_id, stream, sequence)
            );
            INSERT INTO outbound_messages
            (queue_order, peer_id, message_id, session_id, stream, sequence,
             priority, envelope, status, attempts, next_attempt_ms, last_error,
             created_at_ms, updated_at_ms)
            SELECT queue_order, peer_id, message_id, session_id, 'operational',
                   sequence, 0, envelope, status, attempts, next_attempt_ms,
                   last_error, created_at_ms, updated_at_ms
            FROM outbound_messages_legacy;
            DROP TABLE outbound_messages_legacy;
            COMMIT;
            """
        )

    def enqueue_many(
        self, deliveries: Iterable[tuple[str, Envelope]], now_ms: int
    ) -> None:
        items = tuple(deliveries)
        if not items:
            return
        encoded = tuple((peer_id, envelope, encode_envelope(envelope)) for peer_id, envelope in items)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                pending = self._connection.execute(
                    "SELECT COUNT(*) FROM outbound_messages WHERE status = ?",
                    (DeliveryStatus.PENDING.value,),
                ).fetchone()[0]
                new_items = []
                new_item_keys: set[tuple[str, str]] = set()
                seen: dict[tuple[str, str], bytes] = {}
                for peer_id, envelope, body in encoded:
                    key = (peer_id, envelope.message_id)
                    prior = seen.get(key)
                    if prior is not None and prior != body:
                        raise ValueError("outbox message ID is bound to different content")
                    seen[key] = body
                    row = self._connection.execute(
                        """
                        SELECT envelope FROM outbound_messages
                        WHERE peer_id = ? AND message_id = ?
                        """,
                        key,
                    ).fetchone()
                    if row is not None:
                        if bytes(row[0]) != body:
                            raise ValueError(
                                "outbox message ID is bound to different content"
                            )
                        continue
                    if key not in new_item_keys:
                        new_items.append((peer_id, envelope, body))
                        new_item_keys.add(key)
                if pending + len(new_items) > self.maximum_pending:
                    raise OutboxFullError("durable outbound queue is full")
                operational_pending = self._connection.execute(
                    """
                    SELECT COUNT(*) FROM outbound_messages
                    WHERE status = ? AND stream = ?
                    """,
                    (DeliveryStatus.PENDING.value, OPERATIONAL_STREAM),
                ).fetchone()[0]
                new_operational = sum(
                    envelope.stream == OPERATIONAL_STREAM
                    for _, envelope, _ in new_items
                )
                if (
                    operational_pending + new_operational
                    > self.maximum_pending - self.safety_reserve
                ):
                    raise OutboxFullError(
                        "durable outbound queue is preserving safety capacity"
                    )
                for peer_id, envelope, body in new_items:
                    self._connection.execute(
                        """
                        INSERT INTO outbound_messages
                        (peer_id, message_id, session_id, stream, sequence, priority,
                         envelope, status, attempts, next_attempt_ms, last_error,
                         created_at_ms, updated_at_ms)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, NULL, ?, ?)
                        """,
                        (
                            peer_id,
                            envelope.message_id,
                            envelope.session_id,
                            envelope.stream,
                            envelope.sequence,
                            int(envelope.stream == SAFETY_STREAM),
                            body,
                            DeliveryStatus.PENDING.value,
                            now_ms,
                            now_ms,
                            now_ms,
                        ),
                    )
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def ready(
        self, now_ms: int, limit: int = 256, stream: str | None = None
    ) -> tuple[PendingDelivery, ...]:
        if limit < 1:
            raise ValueError("delivery limit must be positive")
        with self._lock:
            stream_clause = "AND candidate.stream = ?" if stream is not None else ""
            parameters: tuple = (
                DeliveryStatus.PENDING.value,
                DeliveryStatus.PENDING.value,
            )
            if stream is not None:
                parameters += (stream,)
            parameters += (now_ms, limit)
            rows = self._connection.execute(
                f"""
                SELECT peer_id, envelope, attempts, next_attempt_ms
                FROM outbound_messages AS candidate
                WHERE status = ?
                  AND queue_order = (
                    SELECT MIN(queue_order) FROM outbound_messages AS pending
                    WHERE pending.peer_id = candidate.peer_id
                      AND pending.stream = candidate.stream
                      AND pending.status = ?
                  )
                  {stream_clause}
                  AND next_attempt_ms <= ?
                ORDER BY priority DESC, created_at_ms, peer_id
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        return tuple(
            PendingDelivery(peer_id, decode_envelope(bytes(body)), attempts, retry_at)
            for peer_id, body, attempts, retry_at in rows
        )

    def delivered(self, peer_id: str, message_id: str, now_ms: int) -> None:
        with self._lock:
            self._connection.execute(
                """
                UPDATE outbound_messages SET status = ?, updated_at_ms = ?
                WHERE peer_id = ? AND message_id = ? AND status = ?
                """,
                (
                    DeliveryStatus.DELIVERED.value,
                    now_ms,
                    peer_id,
                    message_id,
                    DeliveryStatus.PENDING.value,
                ),
            )

    def failed(
        self,
        peer_id: str,
        message_id: str,
        error: str,
        now_ms: int,
        *,
        base_delay_ms: int = 250,
        maximum_delay_ms: int = 30_000,
    ) -> None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT attempts FROM outbound_messages
                WHERE peer_id = ? AND message_id = ? AND status = ?
                """,
                (peer_id, message_id, DeliveryStatus.PENDING.value),
            ).fetchone()
            if row is None:
                return
            attempts = row[0] + 1
            delay = min(base_delay_ms * (2 ** min(attempts - 1, 16)), maximum_delay_ms)
            self._connection.execute(
                """
                UPDATE outbound_messages
                SET attempts = ?, next_attempt_ms = ?, last_error = ?, updated_at_ms = ?
                WHERE peer_id = ? AND message_id = ?
                """,
                (attempts, now_ms + delay, error[:1_024], now_ms, peer_id, message_id),
            )

    def metrics(self) -> DeliveryMetrics:
        with self._lock:
            pending = self._connection.execute(
                "SELECT COUNT(*) FROM outbound_messages WHERE status = ?",
                (DeliveryStatus.PENDING.value,),
            ).fetchone()[0]
            delivered = self._connection.execute(
                "SELECT COUNT(*) FROM outbound_messages WHERE status = ?",
                (DeliveryStatus.DELIVERED.value,),
            ).fetchone()[0]
            failed_attempts = self._connection.execute(
                "SELECT COALESCE(SUM(attempts), 0) FROM outbound_messages"
            ).fetchone()[0]
        return DeliveryMetrics(pending, delivered, failed_attempts)

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class SqliteInbox:
    """Durable receiver queue with bounded worker concurrency."""

    def __init__(
        self,
        path: str | Path,
        handler: Callable[[Envelope], None],
        worker_count: int = 4,
    ) -> None:
        if worker_count < 1 or worker_count > 64:
            raise ValueError("inbox worker_count must be between 1 and 64")
        database_path = Path(path)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._handler = handler
        self._lock = RLock()
        self._connection = sqlite3.connect(
            database_path, isolation_level=None, check_same_thread=False
        )
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS inbound_messages (
                inbox_order INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT NOT NULL UNIQUE,
                source_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                stream TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                priority INTEGER NOT NULL,
                envelope BLOB NOT NULL,
                digest TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                received_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                UNIQUE(source_id, session_id, stream, sequence)
            )
            """
        )
        self._migrate_inbox_streams()
        self._connection.execute(
            "UPDATE inbound_messages SET status = 'received' WHERE status = 'processing'"
        )
        self._stop = Event()
        self._wake = Event()
        self._workers = tuple(
            Thread(target=self._worker, name=f"ump-inbox-{index}", daemon=True)
            for index in range(worker_count)
        )
        for worker in self._workers:
            worker.start()

    def _migrate_inbox_streams(self) -> None:
        columns = {
            row[1] for row in self._connection.execute("PRAGMA table_info(inbound_messages)")
        }
        if "stream" in columns:
            return
        self._connection.executescript(
            """
            BEGIN IMMEDIATE;
            ALTER TABLE inbound_messages RENAME TO inbound_messages_legacy;
            CREATE TABLE inbound_messages (
                inbox_order INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT NOT NULL UNIQUE,
                source_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                stream TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                priority INTEGER NOT NULL,
                envelope BLOB NOT NULL,
                digest TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                received_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                UNIQUE(source_id, session_id, stream, sequence)
            );
            INSERT INTO inbound_messages
            (inbox_order, message_id, source_id, session_id, stream, sequence,
             priority, envelope, digest, status, error, received_at_ms, updated_at_ms)
            SELECT inbox_order, message_id, source_id, session_id, 'operational',
                   sequence, 0, envelope, digest, status, error, received_at_ms,
                   updated_at_ms
            FROM inbound_messages_legacy;
            DROP TABLE inbound_messages_legacy;
            COMMIT;
            """
        )

    def enqueue(self, envelope: Envelope, now_ms: int, *, ready: bool = True) -> bool:
        body = encode_envelope(envelope)
        digest = hashlib.sha256(body).hexdigest()
        with self._lock:
            row = self._connection.execute(
                "SELECT digest FROM inbound_messages WHERE message_id = ?",
                (envelope.message_id,),
            ).fetchone()
            if row is not None:
                if row[0] != digest:
                    raise InboxConflictError(
                        "inbox message ID is bound to different content"
                    )
                return False
            status = "received" if ready else "staged"
            self._connection.execute(
                """
                INSERT INTO inbound_messages
                (message_id, source_id, session_id, stream, sequence, priority,
                 envelope, digest, status, error, received_at_ms, updated_at_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    envelope.message_id,
                    envelope.source_id,
                    envelope.session_id,
                    envelope.stream,
                    envelope.sequence,
                    int(envelope.stream == SAFETY_STREAM),
                    body,
                    digest,
                    status,
                    now_ms,
                    now_ms,
                ),
            )
        if ready:
            self._wake.set()
        return True

    def activate(self, message_id: str) -> None:
        with self._lock:
            self._connection.execute(
                """
                UPDATE inbound_messages SET status = 'received', updated_at_ms = ?
                WHERE message_id = ? AND status = 'staged'
                """,
                (int(time.time() * 1_000), message_id),
            )
        self._wake.set()

    def discard_staged(self, message_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "DELETE FROM inbound_messages WHERE message_id = ? AND status = 'staged'",
                (message_id,),
            )

    def contains_exact(self, envelope: Envelope) -> bool:
        digest = hashlib.sha256(encode_envelope(envelope)).hexdigest()
        with self._lock:
            row = self._connection.execute(
                "SELECT digest FROM inbound_messages WHERE message_id = ?",
                (envelope.message_id,),
            ).fetchone()
        return bool(row and row[0] == digest)

    def _claim(self) -> Envelope | None:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    """
                    SELECT message_id, envelope FROM inbound_messages AS candidate
                    WHERE status = 'received'
                      AND NOT EXISTS (
                        SELECT 1 FROM inbound_messages AS earlier
                        WHERE earlier.source_id = candidate.source_id
                          AND earlier.session_id = candidate.session_id
                          AND earlier.stream = candidate.stream
                          AND earlier.inbox_order < candidate.inbox_order
                          AND earlier.status IN ('received', 'processing')
                      )
                    ORDER BY priority DESC, inbox_order LIMIT 1
                    """
                ).fetchone()
                if row is None:
                    self._connection.execute("COMMIT")
                    return None
                self._connection.execute(
                    """
                    UPDATE inbound_messages SET status = 'processing', updated_at_ms = ?
                    WHERE message_id = ?
                    """,
                    (int(time.time() * 1_000), row[0]),
                )
                self._connection.execute("COMMIT")
                return decode_envelope(bytes(row[1]))
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def _worker(self) -> None:
        while not self._stop.is_set():
            envelope = self._claim()
            if envelope is None:
                self._wake.wait(0.2)
                self._wake.clear()
                continue
            try:
                self._handler(envelope)
            except Exception as error:
                status = "failed"
                description = f"{type(error).__name__}: {error}"[:1_024]
            else:
                status = "processed"
                description = None
            with self._lock:
                self._connection.execute(
                    """
                    UPDATE inbound_messages SET status = ?, error = ?, updated_at_ms = ?
                    WHERE message_id = ?
                    """,
                    (
                        status,
                        description,
                        int(time.time() * 1_000),
                        envelope.message_id,
                    ),
                )

    def counts(self) -> dict[str, int]:
        with self._lock:
            return dict(
                self._connection.execute(
                    "SELECT status, COUNT(*) FROM inbound_messages GROUP BY status"
                ).fetchall()
            )

    def failures(self, limit: int = 100) -> tuple[InboxFailure, ...]:
        if limit < 1 or limit > 10_000:
            raise ValueError("inbox failure limit must be between 1 and 10000")
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT message_id, source_id, session_id, sequence, error, updated_at_ms
                FROM inbound_messages WHERE status = 'failed'
                ORDER BY updated_at_ms DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return tuple(InboxFailure(*row) for row in rows)

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        for worker in self._workers:
            worker.join()
        with self._lock:
            self._connection.close()
