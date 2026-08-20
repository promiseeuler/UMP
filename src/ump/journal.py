from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Protocol

from .models import Assignment, AssignmentStatus, Outcome, PlanStep, payload


class ClaimKind(str, Enum):
    NEW = "new"
    REPLAY = "replay"
    CONFLICT = "conflict"
    RESOURCE_CONFLICT = "resource_conflict"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class ClaimResult:
    kind: ClaimKind
    outcome: Outcome | None = None


@dataclass(frozen=True)
class JournalRecord:
    assignment: Assignment
    issuer_id: str
    status: AssignmentStatus
    outcome: Outcome | None
    fingerprint: str


class AssignmentJournal(Protocol):
    def claim(
        self, assignment: Assignment, issuer_id: str, now_ms: int
    ) -> ClaimResult: ...
    def complete(self, assignment: Assignment, outcome: Outcome, now_ms: int) -> None: ...
    def lookup(self, assignment_id: str) -> JournalRecord | None: ...
    def resolve_unknown(
        self,
        assignment_id: str,
        status: AssignmentStatus,
        description: str,
        resolver_id: str,
        evidence: str,
        now_ms: int,
    ) -> Outcome: ...
    def close(self) -> None: ...


def assignment_fingerprint(assignment: Assignment) -> str:
    encoded = json.dumps(
        payload(assignment),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _outcome_json(outcome: Outcome) -> str:
    return json.dumps(
        payload(outcome),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _decode_outcome(encoded: str) -> Outcome:
    value = json.loads(encoded)
    return Outcome(
        assignment_id=value["assignment_id"],
        robot_id=value["robot_id"],
        succeeded=value["succeeded"],
        description=value["description"],
        status=AssignmentStatus(value["status"]),
    )


def _assignment_json(assignment: Assignment) -> str:
    return json.dumps(
        payload(assignment),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _decode_assignment(encoded: str) -> Assignment:
    value = json.loads(encoded)
    step = value["step"]
    return Assignment(
        assignment_id=value["assignment_id"],
        goal_id=value["goal_id"],
        plan_id=value["plan_id"],
        step=PlanStep(
            step_id=step["step_id"],
            description=step["description"],
            assigned_robot_id=step["assigned_robot_id"],
            capability=step["capability"],
            inputs=step["inputs"],
            completion_criteria=step["completion_criteria"],
            depends_on=tuple(step.get("depends_on", ())),
            resources=tuple(step.get("resources", ())),
            not_before_ms=step.get("not_before_ms"),
            deadline_ms=step.get("deadline_ms"),
        ),
        authority_lease_id=value.get("authority_lease_id"),
    )


class MemoryAssignmentJournal:
    def __init__(self) -> None:
        self._records: dict[str, JournalRecord] = {}
        self._resources: dict[str, str] = {}

    def claim(
        self, assignment: Assignment, issuer_id: str, now_ms: int
    ) -> ClaimResult:
        del now_ms
        fingerprint = assignment_fingerprint(assignment)
        record = self._records.get(assignment.assignment_id)
        if record is None:
            if any(
                owner != assignment.assignment_id
                for resource in assignment.step.resources
                if (owner := self._resources.get(resource)) is not None
            ):
                return ClaimResult(ClaimKind.RESOURCE_CONFLICT)
            self._records[assignment.assignment_id] = JournalRecord(
                assignment,
                issuer_id,
                AssignmentStatus.ACCEPTED,
                None,
                fingerprint,
            )
            for resource in assignment.step.resources:
                self._resources[resource] = assignment.assignment_id
            return ClaimResult(ClaimKind.NEW)
        if record.fingerprint != fingerprint or record.issuer_id != issuer_id:
            return ClaimResult(ClaimKind.CONFLICT)
        if record.outcome is not None:
            return ClaimResult(ClaimKind.REPLAY, record.outcome)
        if record.status is AssignmentStatus.UNKNOWN:
            return ClaimResult(ClaimKind.UNCERTAIN)
        return ClaimResult(ClaimKind.UNCERTAIN)

    def complete(self, assignment: Assignment, outcome: Outcome, now_ms: int) -> None:
        del now_ms
        fingerprint = assignment_fingerprint(assignment)
        record = self._records.get(assignment.assignment_id)
        if record is None or record.fingerprint != fingerprint:
            raise ValueError("cannot complete an unclaimed or conflicting assignment")
        if record.outcome is not None and record.outcome != outcome:
            raise ValueError("terminal assignment outcome is immutable")
        self._records[assignment.assignment_id] = JournalRecord(
            assignment,
            record.issuer_id,
            outcome.status,
            outcome,
            fingerprint,
        )
        if outcome.status is not AssignmentStatus.UNKNOWN:
            for resource in assignment.step.resources:
                if self._resources.get(resource) == assignment.assignment_id:
                    del self._resources[resource]

    def lookup(self, assignment_id: str) -> JournalRecord | None:
        return self._records.get(assignment_id)

    def resolve_unknown(
        self,
        assignment_id: str,
        status: AssignmentStatus,
        description: str,
        resolver_id: str,
        evidence: str,
        now_ms: int,
    ) -> Outcome:
        del now_ms
        _validate_resolution(status, resolver_id, evidence)
        record = self._records.get(assignment_id)
        if record is None:
            raise ValueError("assignment does not exist")
        if record.status is not AssignmentStatus.UNKNOWN or record.outcome is not None:
            raise ValueError("only an unknown assignment can be resolved")
        outcome = Outcome(
            assignment_id,
            record.assignment.step.assigned_robot_id,
            status is AssignmentStatus.SUCCEEDED,
            description,
            status,
        )
        self.complete(record.assignment, outcome, 0)
        return outcome

    def close(self) -> None:
        pass


class SqliteAssignmentJournal:
    """Durable assignment idempotency and outcome journal.

    Accepted records from a previous process are marked unknown at startup. This
    prevents an uncertain physical action from being retried after a crash.
    """

    def __init__(self, path: str | Path) -> None:
        journal_path = Path(path)
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(
            journal_path, isolation_level=None, check_same_thread=False
        )
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS assignments (
                assignment_id TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL,
                assignment_json TEXT NOT NULL,
                issuer_id TEXT NOT NULL,
                status TEXT NOT NULL,
                outcome_json TEXT,
                accepted_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS assignment_resolutions (
                assignment_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                resolver_id TEXT NOT NULL,
                evidence TEXT NOT NULL,
                occurred_at_ms INTEGER NOT NULL,
                outcome_json TEXT NOT NULL,
                FOREIGN KEY(assignment_id) REFERENCES assignments(assignment_id)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS resource_reservations (
                resource_id TEXT PRIMARY KEY,
                assignment_id TEXT NOT NULL,
                reserved_at_ms INTEGER NOT NULL,
                FOREIGN KEY(assignment_id) REFERENCES assignments(assignment_id)
            )
            """
        )
        columns = {
            row[1] for row in self._connection.execute("PRAGMA table_info(assignments)")
        }
        if "assignment_json" not in columns:
            self._connection.execute("ALTER TABLE assignments ADD COLUMN assignment_json TEXT")
        if "issuer_id" not in columns:
            self._connection.execute("ALTER TABLE assignments ADD COLUMN issuer_id TEXT")
        self._connection.execute(
            "UPDATE assignments SET status = ? WHERE status = ? AND outcome_json IS NULL",
            (AssignmentStatus.UNKNOWN.value, AssignmentStatus.ACCEPTED.value),
        )

    def claim(
        self, assignment: Assignment, issuer_id: str, now_ms: int
    ) -> ClaimResult:
        fingerprint = assignment_fingerprint(assignment)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    """
                    SELECT fingerprint, issuer_id, status, outcome_json
                    FROM assignments WHERE assignment_id = ?
                    """,
                    (assignment.assignment_id,),
                ).fetchone()
                if row is None:
                    conflicting_resource = self._connection.execute(
                        """
                        SELECT resource_id FROM resource_reservations
                        WHERE resource_id IN (
                            SELECT value FROM json_each(?)
                        ) AND assignment_id != ? LIMIT 1
                        """,
                        (json.dumps(assignment.step.resources), assignment.assignment_id),
                    ).fetchone()
                    if conflicting_resource is not None:
                        self._connection.execute("COMMIT")
                        return ClaimResult(ClaimKind.RESOURCE_CONFLICT)
                    self._connection.execute(
                        """
                        INSERT INTO assignments
                        (assignment_id, fingerprint, assignment_json, issuer_id,
                         status, outcome_json, accepted_at_ms, updated_at_ms)
                        VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
                        """,
                        (
                            assignment.assignment_id,
                            fingerprint,
                            _assignment_json(assignment),
                            issuer_id,
                            AssignmentStatus.ACCEPTED.value,
                            now_ms,
                            now_ms,
                        ),
                    )
                    self._connection.executemany(
                        """
                        INSERT INTO resource_reservations
                        (resource_id, assignment_id, reserved_at_ms)
                        VALUES (?, ?, ?)
                        """,
                        (
                            (resource, assignment.assignment_id, now_ms)
                            for resource in assignment.step.resources
                        ),
                    )
                    result = ClaimResult(ClaimKind.NEW)
                elif row[0] != fingerprint or row[1] != issuer_id:
                    result = ClaimResult(ClaimKind.CONFLICT)
                elif row[3] is not None:
                    result = ClaimResult(ClaimKind.REPLAY, _decode_outcome(row[3]))
                else:
                    result = ClaimResult(ClaimKind.UNCERTAIN)
                self._connection.execute("COMMIT")
                return result
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def complete(self, assignment: Assignment, outcome: Outcome, now_ms: int) -> None:
        fingerprint = assignment_fingerprint(assignment)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT fingerprint, outcome_json FROM assignments WHERE assignment_id = ?",
                    (assignment.assignment_id,),
                ).fetchone()
                if row is None or row[0] != fingerprint:
                    raise ValueError("cannot complete an unclaimed or conflicting assignment")
                encoded = _outcome_json(outcome)
                if row[1] is not None and row[1] != encoded:
                    raise ValueError("terminal assignment outcome is immutable")
                self._connection.execute(
                    """
                    UPDATE assignments
                    SET status = ?, outcome_json = ?, updated_at_ms = ?
                    WHERE assignment_id = ?
                    """,
                    (outcome.status.value, encoded, now_ms, assignment.assignment_id),
                )
                if outcome.status is not AssignmentStatus.UNKNOWN:
                    self._connection.execute(
                        "DELETE FROM resource_reservations WHERE assignment_id = ?",
                        (assignment.assignment_id,),
                    )
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def lookup(self, assignment_id: str) -> JournalRecord | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT fingerprint, assignment_json, issuer_id, status, outcome_json
                FROM assignments WHERE assignment_id = ?
                """,
                (assignment_id,),
            ).fetchone()
        if row is None or row[1] is None or row[2] is None:
            return None
        return JournalRecord(
            assignment=_decode_assignment(row[1]),
            issuer_id=row[2],
            status=AssignmentStatus(row[3]),
            outcome=_decode_outcome(row[4]) if row[4] is not None else None,
            fingerprint=row[0],
        )

    def resolve_unknown(
        self,
        assignment_id: str,
        status: AssignmentStatus,
        description: str,
        resolver_id: str,
        evidence: str,
        now_ms: int,
    ) -> Outcome:
        _validate_resolution(status, resolver_id, evidence)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    """
                    SELECT assignment_json, status, outcome_json
                    FROM assignments WHERE assignment_id = ?
                    """,
                    (assignment_id,),
                ).fetchone()
                if row is None:
                    raise ValueError("assignment does not exist")
                if AssignmentStatus(row[1]) is not AssignmentStatus.UNKNOWN or row[2] is not None:
                    raise ValueError("only an unknown assignment can be resolved")
                assignment = _decode_assignment(row[0])
                outcome = Outcome(
                    assignment_id,
                    assignment.step.assigned_robot_id,
                    status is AssignmentStatus.SUCCEEDED,
                    description,
                    status,
                )
                encoded = _outcome_json(outcome)
                self._connection.execute(
                    """
                    INSERT INTO assignment_resolutions
                    (assignment_id, status, resolver_id, evidence, occurred_at_ms, outcome_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (assignment_id, status.value, resolver_id, evidence, now_ms, encoded),
                )
                self._connection.execute(
                    """
                    UPDATE assignments SET status = ?, outcome_json = ?, updated_at_ms = ?
                    WHERE assignment_id = ?
                    """,
                    (status.value, encoded, now_ms, assignment_id),
                )
                self._connection.execute(
                    "DELETE FROM resource_reservations WHERE assignment_id = ?",
                    (assignment_id,),
                )
                self._connection.execute("COMMIT")
                return outcome
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def resolution(self, assignment_id: str) -> dict[str, object] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT status, resolver_id, evidence, occurred_at_ms
                FROM assignment_resolutions WHERE assignment_id = ?
                """,
                (assignment_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "assignment_id": assignment_id,
            "status": row[0],
            "resolver_id": row[1],
            "evidence": row[2],
            "occurred_at_ms": row[3],
        }

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def _validate_resolution(
    status: AssignmentStatus, resolver_id: str, evidence: str
) -> None:
    if status not in {
        AssignmentStatus.SUCCEEDED,
        AssignmentStatus.FAILED,
        AssignmentStatus.REJECTED,
        AssignmentStatus.CANCELLED,
    }:
        raise ValueError("resolution requires a known terminal status")
    if not resolver_id.strip() or len(resolver_id.encode("utf-8")) > 128:
        raise ValueError("resolver_id must contain 1 to 128 UTF-8 bytes")
    if not evidence.strip() or len(evidence.encode("utf-8")) > 4_096:
        raise ValueError("resolution evidence must contain 1 to 4096 UTF-8 bytes")
