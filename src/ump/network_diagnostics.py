"""Read-only operational diagnostics for durable UMP delivery stores."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
from typing import Any


class NetworkDiagnosticsError(ValueError):
    pass


OUTBOX_COLUMNS = frozenset(
    {
        "peer_id",
        "message_id",
        "stream",
        "status",
        "attempts",
        "next_attempt_ms",
        "last_error",
        "created_at_ms",
        "updated_at_ms",
    }
)
INBOX_COLUMNS = frozenset(
    {
        "message_id",
        "source_id",
        "session_id",
        "stream",
        "sequence",
        "status",
        "error",
        "received_at_ms",
        "updated_at_ms",
    }
)


def _open_read_only(path: str | Path, description: str) -> sqlite3.Connection:
    database = Path(path).resolve()
    if not database.is_file():
        raise NetworkDiagnosticsError(f"{description} does not exist: {database}")
    try:
        connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
        connection.execute("PRAGMA query_only = ON")
        return connection
    except sqlite3.Error as error:
        raise NetworkDiagnosticsError(f"{description} cannot be opened: {error}") from error


def _require_schema(
    connection: sqlite3.Connection,
    table: str,
    expected: frozenset[str],
    description: str,
) -> None:
    try:
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error as error:
        raise NetworkDiagnosticsError(f"{description} schema cannot be read: {error}") from error
    missing = sorted(expected - columns)
    if missing:
        raise NetworkDiagnosticsError(f"{description} schema is missing columns: {missing}")


def _group_counts(
    connection: sqlite3.Connection, query: str
) -> dict[str, int]:
    return {str(key): int(count) for key, count in _execute(connection, query)}


def _execute(
    connection: sqlite3.Connection,
    query: str,
    parameters: tuple[Any, ...] = (),
) -> sqlite3.Cursor:
    try:
        return connection.execute(query, parameters)
    except sqlite3.Error as error:
        raise NetworkDiagnosticsError(
            f"delivery database query failed: {error}"
        ) from error


def inspect_network_databases(
    inbox_path: str | Path,
    outbox_path: str | Path,
    *,
    maximum_pending: int,
    reserved_safety: int,
    observed_at_ms: int,
    maximum_pending_age_ms: int = 60_000,
    detail_limit: int = 20,
) -> dict[str, Any]:
    """Return a content-free health snapshot without mutating either database."""
    if type(maximum_pending) is not int or maximum_pending < 1:
        raise NetworkDiagnosticsError("maximum_pending must be a positive integer")
    if (
        type(reserved_safety) is not int
        or reserved_safety < 0
        or reserved_safety >= maximum_pending
    ):
        raise NetworkDiagnosticsError("reserved_safety must fit inside maximum_pending")
    if type(observed_at_ms) is not int or observed_at_ms < 0:
        raise NetworkDiagnosticsError("observed_at_ms must be a non-negative integer")
    if type(maximum_pending_age_ms) is not int or maximum_pending_age_ms < 0:
        raise NetworkDiagnosticsError(
            "maximum_pending_age_ms must be a non-negative integer"
        )
    if type(detail_limit) is not int or not 1 <= detail_limit <= 1_000:
        raise NetworkDiagnosticsError("detail_limit must be between 1 and 1000")

    with closing(_open_read_only(outbox_path, "outbox database")) as outbox, closing(
        _open_read_only(inbox_path, "inbox database")
    ) as inbox:
        _require_schema(outbox, "outbound_messages", OUTBOX_COLUMNS, "outbox")
        _require_schema(inbox, "inbound_messages", INBOX_COLUMNS, "inbox")
        _execute(outbox, "BEGIN")
        _execute(inbox, "BEGIN")

        outbox_counts = _group_counts(
            outbox,
            "SELECT status, COUNT(*) FROM outbound_messages GROUP BY status",
        )
        pending_by_peer = _group_counts(
            outbox,
            "SELECT peer_id, COUNT(*) FROM outbound_messages "
            "WHERE status = 'pending' GROUP BY peer_id ORDER BY peer_id",
        )
        pending_by_stream = _group_counts(
            outbox,
            "SELECT stream, COUNT(*) FROM outbound_messages "
            "WHERE status = 'pending' GROUP BY stream ORDER BY stream",
        )
        attempts, oldest_created, next_retry = _execute(
            outbox,
            "SELECT COALESCE(SUM(attempts), 0), MIN(created_at_ms), "
            "MIN(next_attempt_ms) FROM outbound_messages WHERE status = 'pending'",
        ).fetchone()
        recent_errors = [
            {
                "peer_id": row[0],
                "message_id": row[1],
                "stream": row[2],
                "attempts": row[3],
                "last_error": row[4],
                "updated_at_ms": row[5],
            }
            for row in _execute(
                outbox,
                "SELECT peer_id, message_id, stream, attempts, last_error, updated_at_ms "
                "FROM outbound_messages WHERE status = 'pending' AND last_error IS NOT NULL "
                "ORDER BY updated_at_ms DESC, queue_order DESC LIMIT ?",
                (detail_limit,),
            )
        ]

        inbox_counts = _group_counts(
            inbox,
            "SELECT status, COUNT(*) FROM inbound_messages GROUP BY status",
        )
        failures = [
            {
                "message_id": row[0],
                "source_id": row[1],
                "session_id": row[2],
                "stream": row[3],
                "sequence": row[4],
                "error": row[5],
                "updated_at_ms": row[6],
            }
            for row in _execute(
                inbox,
                "SELECT message_id, source_id, session_id, stream, sequence, error, "
                "updated_at_ms FROM inbound_messages WHERE status = 'failed' "
                "ORDER BY updated_at_ms DESC, inbox_order DESC LIMIT ?",
                (detail_limit,),
            )
        ]

    pending = outbox_counts.get("pending", 0)
    operational_pending = pending_by_stream.get("operational", 0)
    oldest_age = (
        None if oldest_created is None else observed_at_ms - int(oldest_created)
    )
    checks = {
        "outbox_has_capacity": pending < maximum_pending,
        "operational_reserve_preserved": operational_pending
        < maximum_pending - reserved_safety,
        "pending_age_within_limit": oldest_age is None
        or 0 <= oldest_age <= maximum_pending_age_ms,
        "inbox_has_no_dead_letters": inbox_counts.get("failed", 0) == 0,
        "outbox_statuses_known": set(outbox_counts) <= {"pending", "delivered"},
        "inbox_statuses_known": set(inbox_counts)
        <= {"staged", "received", "processing", "processed", "failed"},
    }
    return {
        "profile": "ump.network-diagnostics/v1",
        "observed_at_ms": observed_at_ms,
        "healthy": all(checks.values()),
        "checks": checks,
        "outbox": {
            "counts": outbox_counts,
            "capacity": maximum_pending,
            "reserved_safety": reserved_safety,
            "pending_by_peer": pending_by_peer,
            "pending_by_stream": pending_by_stream,
            "pending_attempts": int(attempts),
            "oldest_pending_age_ms": oldest_age,
            "next_retry_at_ms": next_retry,
            "recent_errors": recent_errors,
        },
        "inbox": {"counts": inbox_counts, "failures": failures},
    }
