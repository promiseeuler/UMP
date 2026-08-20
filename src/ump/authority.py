from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Protocol

from .models import Assignment, AuthorityLease, LeaseStatus


class AuthorizationError(PermissionError):
    pass


class AuthorityReadError(ValueError):
    pass


@dataclass(frozen=True)
class AuthorityLeaseSummary:
    lease: AuthorityLease
    effective_status: str
    updated_at_ms: int


EFFECTIVE_LEASE_STATUSES = frozenset(
    {"active", "not_yet_valid", "expired", "revoked"}
)
AUTHORITY_LEASE_COLUMNS = frozenset(
    {
        "lease_id",
        "grantor_robot_id",
        "issuer_id",
        "capabilities_json",
        "issued_at_ms",
        "expires_at_ms",
        "maximum_clock_uncertainty_ms",
        "revision",
        "status",
        "updated_at_ms",
    }
)
AUTHORITY_EVENT_COLUMNS = frozenset(
    {"sequence", "lease_id", "event_type", "revision", "occurred_at_ms", "detail"}
)


def _open_authority_read_only(path: str | Path) -> sqlite3.Connection:
    database = Path(path).resolve()
    if not database.is_file():
        raise AuthorityReadError(f"authority database does not exist: {database}")
    try:
        connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
        connection.execute("PRAGMA query_only = ON")
        tables = {
            "authority_leases": AUTHORITY_LEASE_COLUMNS,
            "authority_events": AUTHORITY_EVENT_COLUMNS,
        }
        for table, expected in tables.items():
            columns = {
                row[1] for row in connection.execute(f"PRAGMA table_info({table})")
            }
            if missing := sorted(expected - columns):
                raise AuthorityReadError(
                    f"authority database {table} is missing columns: {missing}"
                )
        return connection
    except (sqlite3.Error, AuthorityReadError) as error:
        if "connection" in locals():
            connection.close()
        if isinstance(error, AuthorityReadError):
            raise
        raise AuthorityReadError(
            f"authority database cannot be opened read-only: {error}"
        ) from error


def _effective_status(lease: AuthorityLease, now_ms: int) -> str:
    if lease.status is LeaseStatus.REVOKED:
        return "revoked"
    if lease.status is LeaseStatus.EXPIRED:
        return "expired"
    uncertainty = lease.maximum_clock_uncertainty_ms
    if now_ms - uncertainty < lease.issued_at_ms:
        return "not_yet_valid"
    if now_ms + uncertainty >= lease.expires_at_ms:
        return "expired"
    return "active"


def _summary(row: tuple, now_ms: int) -> AuthorityLeaseSummary:
    try:
        capabilities = json.loads(row[3])
        if not isinstance(capabilities, list):
            raise ValueError("capabilities are not an array")
        lease = AuthorityLease(
            lease_id=row[0],
            grantor_robot_id=row[1],
            issuer_id=row[2],
            capabilities=tuple(capabilities),
            issued_at_ms=row[4],
            expires_at_ms=row[5],
            maximum_clock_uncertainty_ms=row[6],
            revision=row[7],
            status=LeaseStatus(row[8]),
        )
        return AuthorityLeaseSummary(lease, _effective_status(lease, now_ms), row[9])
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise AuthorityReadError(f"authority lease row is invalid: {error}") from error


def read_authority_leases(
    path: str | Path,
    robot_id: str,
    now_ms: int,
    *,
    effective_status: str | None = None,
    issuer_id: str | None = None,
    limit: int = 100,
) -> tuple[AuthorityLeaseSummary, ...]:
    """List bounded lease summaries from an existing database without mutation."""
    if not isinstance(robot_id, str) or not robot_id.strip():
        raise AuthorityReadError("robot_id is required")
    if type(now_ms) is not int or now_ms < 0:
        raise AuthorityReadError("now_ms must be a non-negative integer")
    if effective_status is not None and effective_status not in EFFECTIVE_LEASE_STATUSES:
        raise AuthorityReadError("effective lease status is invalid")
    if issuer_id is not None and (not isinstance(issuer_id, str) or not issuer_id.strip()):
        raise AuthorityReadError("issuer_id filter is invalid")
    if type(limit) is not int or not 1 <= limit <= 1_000:
        raise AuthorityReadError("lease limit must be between 1 and 1000")

    connection = _open_authority_read_only(path)
    try:
        clauses = ["grantor_robot_id = ?"]
        parameters: list[object] = [robot_id]
        if issuer_id is not None:
            clauses.append("issuer_id = ?")
            parameters.append(issuer_id)
        if effective_status == "active":
            clauses.append(
                "status = 'active' AND ? - maximum_clock_uncertainty_ms >= issued_at_ms "
                "AND ? + maximum_clock_uncertainty_ms < expires_at_ms"
            )
            parameters.extend((now_ms, now_ms))
        elif effective_status == "not_yet_valid":
            clauses.append(
                "status = 'active' AND ? - maximum_clock_uncertainty_ms < issued_at_ms"
            )
            parameters.append(now_ms)
        elif effective_status == "expired":
            clauses.append(
                "(status = 'expired' OR (status = 'active' "
                "AND ? + maximum_clock_uncertainty_ms >= expires_at_ms))"
            )
            parameters.append(now_ms)
        elif effective_status == "revoked":
            clauses.append("status = 'revoked'")
        rows = connection.execute(
            "SELECT lease_id, grantor_robot_id, issuer_id, capabilities_json, "
            "issued_at_ms, expires_at_ms, maximum_clock_uncertainty_ms, revision, "
            "status, updated_at_ms FROM authority_leases WHERE "
            + " AND ".join(clauses)
            + " ORDER BY updated_at_ms DESC, lease_id LIMIT ?",
            (*parameters, limit),
        ).fetchall()
        return tuple(_summary(row, now_ms) for row in rows)
    except sqlite3.Error as error:
        raise AuthorityReadError(f"authority leases cannot be read: {error}") from error
    finally:
        connection.close()


def read_authority_lease(
    path: str | Path, robot_id: str, lease_id: str, now_ms: int
) -> AuthorityLeaseSummary:
    if not isinstance(robot_id, str) or not robot_id.strip():
        raise AuthorityReadError("robot_id is required")
    if not isinstance(lease_id, str) or not lease_id.strip():
        raise AuthorityReadError("lease_id is required")
    if type(now_ms) is not int or now_ms < 0:
        raise AuthorityReadError("now_ms must be a non-negative integer")
    connection = _open_authority_read_only(path)
    try:
        row = connection.execute(
            "SELECT lease_id, grantor_robot_id, issuer_id, capabilities_json, "
            "issued_at_ms, expires_at_ms, maximum_clock_uncertainty_ms, revision, "
            "status, updated_at_ms FROM authority_leases "
            "WHERE grantor_robot_id = ? AND lease_id = ?",
            (robot_id, lease_id),
        ).fetchone()
        if row is None:
            raise KeyError(lease_id)
        return _summary(row, now_ms)
    except sqlite3.Error as error:
        raise AuthorityReadError(f"authority lease cannot be read: {error}") from error
    finally:
        connection.close()


def read_authority_events(
    path: str | Path,
    robot_id: str,
    lease_id: str,
    *,
    limit: int = 1_000,
) -> tuple[tuple[int, str, int, int, str], ...]:
    if not isinstance(robot_id, str) or not robot_id.strip():
        raise AuthorityReadError("robot_id is required")
    if not isinstance(lease_id, str) or not lease_id.strip():
        raise AuthorityReadError("lease_id is required")
    if type(limit) is not int or not 1 <= limit <= 1_000:
        raise AuthorityReadError("event limit must be between 1 and 1000")
    connection = _open_authority_read_only(path)
    try:
        owner = connection.execute(
            "SELECT grantor_robot_id FROM authority_leases WHERE lease_id = ?",
            (lease_id,),
        ).fetchone()
        if owner is None or owner[0] != robot_id:
            raise KeyError(lease_id)
        return tuple(
            connection.execute(
                "SELECT sequence, event_type, revision, occurred_at_ms, detail FROM ("
                "SELECT sequence, event_type, revision, occurred_at_ms, detail "
                "FROM authority_events WHERE lease_id = ? "
                "ORDER BY sequence DESC LIMIT ?) ORDER BY sequence",
                (lease_id, limit),
            )
        )
    except sqlite3.Error as error:
        raise AuthorityReadError(f"authority events cannot be read: {error}") from error
    finally:
        connection.close()


class AssignmentAuthorizer(Protocol):
    def authorize(
        self,
        authenticated_issuer_id: str,
        robot_id: str,
        assignment: Assignment,
        now_ms: int,
    ) -> AuthorityLease | None: ...

    def close(self) -> None: ...


class DenyAllAuthorizer:
    def authorize(
        self,
        authenticated_issuer_id: str,
        robot_id: str,
        assignment: Assignment,
        now_ms: int,
    ) -> None:
        del authenticated_issuer_id, robot_id, assignment, now_ms
        raise AuthorizationError("no assignment authority policy is configured")

    def close(self) -> None:
        pass


class AllowAllAuthorizer:
    """Explicitly permissive policy for deterministic simulation and tests only."""

    def authorize(
        self,
        authenticated_issuer_id: str,
        robot_id: str,
        assignment: Assignment,
        now_ms: int,
    ) -> None:
        del authenticated_issuer_id, robot_id, assignment, now_ms

    def close(self) -> None:
        pass


class SqliteAuthorityStore:
    """Robot-local durable authority leases and append-only audit events."""

    def __init__(self, robot_id: str, path: str | Path) -> None:
        if not robot_id:
            raise ValueError("authority store robot_id cannot be empty")
        store_path = Path(path)
        store_path.parent.mkdir(parents=True, exist_ok=True)
        self.robot_id = robot_id
        self._lock = RLock()
        self._connection = sqlite3.connect(
            store_path, isolation_level=None, check_same_thread=False
        )
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS authority_leases (
                lease_id TEXT PRIMARY KEY,
                grantor_robot_id TEXT NOT NULL,
                issuer_id TEXT NOT NULL,
                capabilities_json TEXT NOT NULL,
                issued_at_ms INTEGER NOT NULL,
                expires_at_ms INTEGER NOT NULL,
                maximum_clock_uncertainty_ms INTEGER NOT NULL,
                revision INTEGER NOT NULL,
                status TEXT NOT NULL,
                updated_at_ms INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS authority_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                lease_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                revision INTEGER NOT NULL,
                occurred_at_ms INTEGER NOT NULL,
                detail TEXT NOT NULL
            );
            """
        )

    def grant(self, lease: AuthorityLease, now_ms: int) -> None:
        if lease.grantor_robot_id != self.robot_id:
            raise AuthorizationError("lease grantor does not match local robot")
        if lease.status is not LeaseStatus.ACTIVE:
            raise ValueError("new or renewed lease must be active")
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self._connection.execute(
                    """
                    SELECT grantor_robot_id, issuer_id, revision, status
                    FROM authority_leases WHERE lease_id = ?
                    """,
                    (lease.lease_id,),
                ).fetchone()
                if existing is not None:
                    if existing[0] != lease.grantor_robot_id or existing[1] != lease.issuer_id:
                        raise AuthorizationError("lease identity fields are immutable")
                    if LeaseStatus(existing[3]) is LeaseStatus.REVOKED:
                        raise AuthorizationError(
                            "revoked lease cannot be renewed; use a new lease ID"
                        )
                    if lease.revision != existing[2] + 1:
                        raise AuthorizationError("lease renewal revision must increase by one")
                self._connection.execute(
                    """
                    INSERT INTO authority_leases
                    (lease_id, grantor_robot_id, issuer_id, capabilities_json,
                     issued_at_ms, expires_at_ms, maximum_clock_uncertainty_ms,
                     revision, status, updated_at_ms)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(lease_id) DO UPDATE SET
                      capabilities_json = excluded.capabilities_json,
                      issued_at_ms = excluded.issued_at_ms,
                      expires_at_ms = excluded.expires_at_ms,
                      maximum_clock_uncertainty_ms = excluded.maximum_clock_uncertainty_ms,
                      revision = excluded.revision,
                      status = excluded.status,
                      updated_at_ms = excluded.updated_at_ms
                    """,
                    (
                        lease.lease_id,
                        lease.grantor_robot_id,
                        lease.issuer_id,
                        json.dumps(lease.capabilities, separators=(",", ":")),
                        lease.issued_at_ms,
                        lease.expires_at_ms,
                        lease.maximum_clock_uncertainty_ms,
                        lease.revision,
                        lease.status.value,
                        now_ms,
                    ),
                )
                self._append_event_locked(
                    lease.lease_id, "grant", lease.revision, now_ms, "lease active"
                )
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def revoke(
        self, lease_id: str, expected_revision: int, now_ms: int, reason: str
    ) -> None:
        if not reason.strip() or len(reason.encode()) > 1_024:
            raise ValueError("revocation reason is required and bounded")
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT revision, status FROM authority_leases WHERE lease_id = ?",
                    (lease_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(lease_id)
                if row[0] != expected_revision:
                    raise AuthorizationError("lease revision changed before revocation")
                if LeaseStatus(row[1]) is not LeaseStatus.ACTIVE:
                    raise AuthorizationError("only an active lease can be revoked")
                revision = expected_revision + 1
                self._connection.execute(
                    """
                    UPDATE authority_leases
                    SET status = ?, revision = ?, updated_at_ms = ?
                    WHERE lease_id = ?
                    """,
                    (LeaseStatus.REVOKED.value, revision, now_ms, lease_id),
                )
                self._append_event_locked(
                    lease_id, "revoke", revision, now_ms, reason
                )
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def authorize(
        self,
        authenticated_issuer_id: str,
        robot_id: str,
        assignment: Assignment,
        now_ms: int,
    ) -> AuthorityLease:
        if robot_id != self.robot_id:
            raise AuthorizationError("authority store does not belong to target robot")
        if assignment.authority_lease_id is None:
            raise AuthorizationError("assignment does not present an authority lease")
        with self._lock:
            row = self._connection.execute(
                """
                SELECT grantor_robot_id, issuer_id, capabilities_json, issued_at_ms,
                       expires_at_ms, maximum_clock_uncertainty_ms, revision, status
                FROM authority_leases WHERE lease_id = ?
                """,
                (assignment.authority_lease_id,),
            ).fetchone()
        if row is None:
            raise AuthorizationError("authority lease is unknown")
        lease = AuthorityLease(
            lease_id=assignment.authority_lease_id,
            grantor_robot_id=row[0],
            issuer_id=row[1],
            capabilities=tuple(json.loads(row[2])),
            issued_at_ms=row[3],
            expires_at_ms=row[4],
            maximum_clock_uncertainty_ms=row[5],
            revision=row[6],
            status=LeaseStatus(row[7]),
        )
        if lease.status is not LeaseStatus.ACTIVE:
            raise AuthorizationError(f"authority lease is {lease.status.value}")
        if lease.issuer_id != authenticated_issuer_id:
            raise AuthorizationError("authenticated issuer does not hold the lease")
        if assignment.step.capability not in lease.capabilities:
            raise AuthorizationError("capability is outside the authority lease scope")
        uncertainty = lease.maximum_clock_uncertainty_ms
        if now_ms - uncertainty < lease.issued_at_ms:
            raise AuthorizationError("authority lease is not yet valid")
        if now_ms + uncertainty >= lease.expires_at_ms:
            raise AuthorizationError("authority lease is expired or inside uncertainty bound")
        return lease

    def events(self, lease_id: str) -> tuple[tuple[int, str, int, int, str], ...]:
        with self._lock:
            return tuple(
                self._connection.execute(
                    """
                    SELECT sequence, event_type, revision, occurred_at_ms, detail
                    FROM authority_events WHERE lease_id = ? ORDER BY sequence
                    """,
                    (lease_id,),
                )
            )

    def _append_event_locked(
        self,
        lease_id: str,
        event_type: str,
        revision: int,
        now_ms: int,
        detail: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO authority_events
            (lease_id, event_type, revision, occurred_at_ms, detail)
            VALUES (?, ?, ?, ?, ?)
            """,
            (lease_id, event_type, revision, now_ms, detail),
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()
