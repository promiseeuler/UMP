from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Protocol

from .models import Assignment, AuthorityLease, LeaseStatus


class AuthorizationError(PermissionError):
    pass


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
                    SELECT grantor_robot_id, issuer_id, revision
                    FROM authority_leases WHERE lease_id = ?
                    """,
                    (lease.lease_id,),
                ).fetchone()
                if existing is not None:
                    if existing[0] != lease.grantor_robot_id or existing[1] != lease.issuer_id:
                        raise AuthorizationError("lease identity fields are immutable")
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
