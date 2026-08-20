from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
import sqlite3
from threading import RLock

from .models import (
    Assignment,
    AssignmentStatus,
    Plan,
    PlanStep,
    SharedGoal,
    payload,
)


class RunStatus(str, Enum):
    ACTIVE = "active"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class StepStatus(str, Enum):
    PENDING = "pending"
    DISPATCHED = "dispatched"
    ACCEPTED = "accepted"
    CANCELLATION_REQUESTED = "cancellation_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


TERMINAL_STEP_STATUSES = frozenset(
    {
        StepStatus.SUCCEEDED,
        StepStatus.FAILED,
        StepStatus.REJECTED,
        StepStatus.CANCELLED,
        StepStatus.UNKNOWN,
    }
)


@dataclass(frozen=True)
class RunSnapshot:
    plan_id: str
    goal_id: str
    status: RunStatus
    steps: dict[str, StepStatus]


def _canonical(value: object) -> str:
    return json.dumps(
        payload(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def _decode_step(value: dict) -> PlanStep:
    return PlanStep(
        step_id=value["step_id"],
        description=value["description"],
        assigned_robot_id=value["assigned_robot_id"],
        capability=value["capability"],
        inputs=value["inputs"],
        completion_criteria=value["completion_criteria"],
        depends_on=tuple(value.get("depends_on", ())),
        resources=tuple(value.get("resources", ())),
        not_before_ms=value.get("not_before_ms"),
        deadline_ms=value.get("deadline_ms"),
    )


def _decode_assignment(encoded: str) -> Assignment:
    value = json.loads(encoded)
    return Assignment(
        assignment_id=value["assignment_id"],
        goal_id=value["goal_id"],
        plan_id=value["plan_id"],
        step=_decode_step(value["step"]),
        authority_lease_id=value.get("authority_lease_id"),
    )


def _decode_goal(encoded: str) -> SharedGoal:
    value = json.loads(encoded)
    return SharedGoal(
        goal_id=value["goal_id"],
        description=value["description"],
        participant_ids=tuple(value["participant_ids"]),
        constraints=value.get("constraints", {}),
        deadline_ms=value.get("deadline_ms"),
    )


def _decode_plan(encoded: str) -> Plan:
    value = json.loads(encoded)
    return Plan(
        plan_id=value["plan_id"],
        goal_id=value["goal_id"],
        planner_id=value["planner_id"],
        summary=value["summary"],
        steps=tuple(_decode_step(step) for step in value["steps"]),
        revision=value.get("revision", 1),
        supersedes_plan_id=value.get("supersedes_plan_id"),
    )


class CoordinatorStore:
    """SQLite journal for plans and coordinator-side assignment lifecycle."""

    def __init__(
        self, path: str | Path = ":memory:", *, recover_interrupted: bool = True
    ) -> None:
        if str(path) != ":memory:":
            store_path = Path(path)
            store_path.parent.mkdir(parents=True, exist_ok=True)
            database = str(store_path)
        else:
            database = ":memory:"
        self._lock = RLock()
        self._connection = sqlite3.connect(
            database, isolation_level=None, check_same_thread=False
        )
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                plan_id TEXT PRIMARY KEY,
                goal_id TEXT NOT NULL,
                goal_json TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS run_steps (
                assignment_id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                assignment_json TEXT NOT NULL,
                dependencies_json TEXT NOT NULL,
                status TEXT NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                UNIQUE(plan_id, step_id),
                FOREIGN KEY(plan_id) REFERENCES runs(plan_id)
            );
            CREATE INDEX IF NOT EXISTS run_steps_plan ON run_steps(plan_id);
            """
        )
        if recover_interrupted:
            self._recover_interrupted_runs()

    def _recover_interrupted_runs(self) -> None:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                affected = self._connection.execute(
                    """
                    SELECT DISTINCT plan_id FROM run_steps
                    WHERE status IN (?, ?, ?)
                    """,
                    (
                        StepStatus.DISPATCHED.value,
                        StepStatus.ACCEPTED.value,
                        StepStatus.CANCELLATION_REQUESTED.value,
                    ),
                ).fetchall()
                self._connection.execute(
                    """
                    UPDATE run_steps SET status = ?
                    WHERE status IN (?, ?, ?)
                    """,
                    (
                        StepStatus.UNKNOWN.value,
                        StepStatus.DISPATCHED.value,
                        StepStatus.ACCEPTED.value,
                        StepStatus.CANCELLATION_REQUESTED.value,
                    ),
                )
                for (plan_id,) in affected:
                    self._connection.execute(
                        "UPDATE runs SET status = ? WHERE plan_id = ?",
                        (RunStatus.UNKNOWN.value, plan_id),
                    )
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def create_run(
        self,
        goal: SharedGoal,
        plan: Plan,
        assignments: tuple[Assignment, ...],
        now_ms: int,
    ) -> None:
        goal_json = _canonical(goal)
        plan_json = _canonical(plan)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self._connection.execute(
                    "SELECT goal_json, plan_json FROM runs WHERE plan_id = ?",
                    (plan.plan_id,),
                ).fetchone()
                if existing is not None:
                    if existing != (goal_json, plan_json):
                        raise ValueError("plan_id is already bound to different content")
                    self._connection.execute("COMMIT")
                    return
                self._connection.execute(
                    """
                    INSERT INTO runs
                    (plan_id, goal_id, goal_json, plan_json, status, created_at_ms, updated_at_ms)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        plan.plan_id,
                        goal.goal_id,
                        goal_json,
                        plan_json,
                        RunStatus.ACTIVE.value,
                        now_ms,
                        now_ms,
                    ),
                )
                for assignment in assignments:
                    self._connection.execute(
                        """
                        INSERT INTO run_steps
                        (assignment_id, plan_id, step_id, assignment_json,
                         dependencies_json, status, updated_at_ms)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            assignment.assignment_id,
                            plan.plan_id,
                            assignment.step.step_id,
                            _canonical(assignment),
                            json.dumps(assignment.step.depends_on),
                            StepStatus.PENDING.value,
                            now_ms,
                        ),
                    )
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def ready_assignments(self, plan_id: str) -> tuple[Assignment, ...]:
        with self._lock:
            run = self._connection.execute(
                "SELECT status FROM runs WHERE plan_id = ?", (plan_id,)
            ).fetchone()
            if run is None:
                raise KeyError(plan_id)
            if RunStatus(run[0]) is not RunStatus.ACTIVE:
                return ()
            rows = self._connection.execute(
                """
                SELECT assignment_json, dependencies_json FROM run_steps
                WHERE plan_id = ? AND status = ? ORDER BY rowid
                """,
                (plan_id, StepStatus.PENDING.value),
            ).fetchall()
            statuses = dict(
                self._connection.execute(
                    "SELECT step_id, status FROM run_steps WHERE plan_id = ?",
                    (plan_id,),
                ).fetchall()
            )
        ready = []
        for assignment_json, dependencies_json in rows:
            dependencies = json.loads(dependencies_json)
            if all(statuses.get(item) == StepStatus.SUCCEEDED.value for item in dependencies):
                ready.append(_decode_assignment(assignment_json))
        return tuple(ready)

    def active_plan_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(
                row[0]
                for row in self._connection.execute(
                    "SELECT plan_id FROM runs WHERE status = ? ORDER BY created_at_ms",
                    (RunStatus.ACTIVE.value,),
                )
            )

    def unknown_assignments(self, plan_id: str) -> tuple[Assignment, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT assignment_json FROM run_steps
                WHERE plan_id = ? AND status = ? ORDER BY rowid
                """,
                (plan_id, StepStatus.UNKNOWN.value),
            ).fetchall()
        return tuple(_decode_assignment(row[0]) for row in rows)

    def mark_dispatched(self, assignment_id: str, now_ms: int) -> bool:
        with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE run_steps SET status = ?, updated_at_ms = ?
                WHERE assignment_id = ? AND status = ?
                """,
                (
                    StepStatus.DISPATCHED.value,
                    now_ms,
                    assignment_id,
                    StepStatus.PENDING.value,
                ),
            )
            return cursor.rowcount == 1

    def request_cancellation(
        self, plan_id: str, now_ms: int
    ) -> tuple[Assignment, ...]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                run = self._connection.execute(
                    "SELECT 1 FROM runs WHERE plan_id = ?", (plan_id,)
                ).fetchone()
                if run is None:
                    raise KeyError(plan_id)
                rows = self._connection.execute(
                    """
                    SELECT assignment_json FROM run_steps
                    WHERE plan_id = ? AND status IN (?, ?)
                    ORDER BY rowid
                    """,
                    (
                        plan_id,
                        StepStatus.DISPATCHED.value,
                        StepStatus.ACCEPTED.value,
                    ),
                ).fetchall()
                self._connection.execute(
                    """
                    UPDATE run_steps SET status = ?, updated_at_ms = ?
                    WHERE plan_id = ? AND status = ?
                    """,
                    (
                        StepStatus.CANCELLED.value,
                        now_ms,
                        plan_id,
                        StepStatus.PENDING.value,
                    ),
                )
                self._connection.execute(
                    """
                    UPDATE run_steps SET status = ?, updated_at_ms = ?
                    WHERE plan_id = ? AND status IN (?, ?)
                    """,
                    (
                        StepStatus.CANCELLATION_REQUESTED.value,
                        now_ms,
                        plan_id,
                        StepStatus.DISPATCHED.value,
                        StepStatus.ACCEPTED.value,
                    ),
                )
                self._refresh_run_locked(plan_id, now_ms)
                self._connection.execute("COMMIT")
                return tuple(_decode_assignment(row[0]) for row in rows)
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def record_cancellation_acknowledgement(
        self, assignment_id: str, accepted: bool, now_ms: int
    ) -> str | None:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT plan_id, status FROM run_steps WHERE assignment_id = ?",
                    (assignment_id,),
                ).fetchone()
                if row is None:
                    self._connection.execute("COMMIT")
                    return None
                plan_id, current = row
                if StepStatus(current) is StepStatus.CANCELLATION_REQUESTED and not accepted:
                    self._connection.execute(
                        """
                        UPDATE run_steps SET status = ?, updated_at_ms = ?
                        WHERE assignment_id = ?
                        """,
                        (StepStatus.UNKNOWN.value, now_ms, assignment_id),
                    )
                self._refresh_run_locked(plan_id, now_ms)
                self._connection.execute("COMMIT")
                return plan_id
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def record_acknowledgement(
        self, assignment_id: str, status: AssignmentStatus, now_ms: int
    ) -> str | None:
        target = None
        if status is AssignmentStatus.ACCEPTED:
            target = StepStatus.ACCEPTED
        elif status is AssignmentStatus.REJECTED:
            target = StepStatus.REJECTED
        elif status is AssignmentStatus.UNKNOWN:
            target = StepStatus.UNKNOWN
        if target is None:
            return self.plan_for_assignment(assignment_id)
        return self._transition(assignment_id, target, now_ms, from_ack=True)

    def record_outcome(
        self, assignment_id: str, status: AssignmentStatus, now_ms: int
    ) -> str | None:
        mapping = {
            AssignmentStatus.SUCCEEDED: StepStatus.SUCCEEDED,
            AssignmentStatus.FAILED: StepStatus.FAILED,
            AssignmentStatus.REJECTED: StepStatus.REJECTED,
            AssignmentStatus.CANCELLED: StepStatus.CANCELLED,
            AssignmentStatus.UNKNOWN: StepStatus.UNKNOWN,
        }
        if status not in mapping:
            raise ValueError("outcome status is not terminal")
        return self._transition(assignment_id, mapping[status], now_ms, from_ack=False)

    def reconcile_outcome(
        self, assignment_id: str, status: AssignmentStatus, now_ms: int
    ) -> str | None:
        mapping = {
            AssignmentStatus.SUCCEEDED: StepStatus.SUCCEEDED,
            AssignmentStatus.FAILED: StepStatus.FAILED,
            AssignmentStatus.REJECTED: StepStatus.REJECTED,
            AssignmentStatus.CANCELLED: StepStatus.CANCELLED,
        }
        if status not in mapping:
            raise ValueError("reconciliation requires a known terminal outcome")
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT plan_id, status FROM run_steps WHERE assignment_id = ?",
                    (assignment_id,),
                ).fetchone()
                if row is None:
                    self._connection.execute("COMMIT")
                    return None
                plan_id, current = row
                if StepStatus(current) is not StepStatus.UNKNOWN:
                    raise ValueError("only unknown assignments can be reconciled")
                self._connection.execute(
                    """
                    UPDATE run_steps SET status = ?, updated_at_ms = ?
                    WHERE assignment_id = ?
                    """,
                    (mapping[status].value, now_ms, assignment_id),
                )
                self._refresh_run_locked(plan_id, now_ms)
                self._connection.execute("COMMIT")
                return plan_id
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def _transition(
        self,
        assignment_id: str,
        target: StepStatus,
        now_ms: int,
        *,
        from_ack: bool,
    ) -> str | None:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT plan_id, status FROM run_steps WHERE assignment_id = ?",
                    (assignment_id,),
                ).fetchone()
                if row is None:
                    self._connection.execute("COMMIT")
                    return None
                plan_id, current_value = row
                current = StepStatus(current_value)
                if current in TERMINAL_STEP_STATUSES:
                    if from_ack:
                        self._connection.execute("COMMIT")
                        return plan_id
                    if current is not target:
                        raise ValueError("terminal coordinator step status is immutable")
                else:
                    allowed = (
                        current in {StepStatus.DISPATCHED, StepStatus.ACCEPTED}
                        if from_ack
                        else current
                        in {
                            StepStatus.DISPATCHED,
                            StepStatus.ACCEPTED,
                            StepStatus.CANCELLATION_REQUESTED,
                        }
                    )
                    if not allowed:
                        raise ValueError(f"invalid step transition {current.value} -> {target.value}")
                    self._connection.execute(
                        """
                        UPDATE run_steps SET status = ?, updated_at_ms = ?
                        WHERE assignment_id = ?
                        """,
                        (target.value, now_ms, assignment_id),
                    )
                self._refresh_run_locked(plan_id, now_ms)
                self._connection.execute("COMMIT")
                return plan_id
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def _refresh_run_locked(self, plan_id: str, now_ms: int) -> None:
        statuses = {
            StepStatus(row[0])
            for row in self._connection.execute(
                "SELECT status FROM run_steps WHERE plan_id = ?", (plan_id,)
            )
        }
        if StepStatus.UNKNOWN in statuses:
            run_status = RunStatus.UNKNOWN
        elif statuses & {StepStatus.FAILED, StepStatus.REJECTED}:
            run_status = RunStatus.FAILED
        elif statuses == {StepStatus.SUCCEEDED}:
            run_status = RunStatus.SUCCEEDED
        elif statuses <= {StepStatus.SUCCEEDED, StepStatus.CANCELLED}:
            run_status = RunStatus.CANCELLED
        else:
            run_status = RunStatus.ACTIVE
        self._connection.execute(
            "UPDATE runs SET status = ?, updated_at_ms = ? WHERE plan_id = ?",
            (run_status.value, now_ms, plan_id),
        )

    def plan_for_assignment(self, assignment_id: str) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT plan_id FROM run_steps WHERE assignment_id = ?", (assignment_id,)
            ).fetchone()
            return row[0] if row else None

    def status_for_assignment(self, assignment_id: str) -> StepStatus | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT status FROM run_steps WHERE assignment_id = ?", (assignment_id,)
            ).fetchone()
            return StepStatus(row[0]) if row else None

    def robot_for_assignment(self, assignment_id: str) -> str | None:
        assignment = self.assignment(assignment_id)
        return assignment.step.assigned_robot_id if assignment is not None else None

    def assignment(self, assignment_id: str) -> Assignment | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT assignment_json FROM run_steps WHERE assignment_id = ?",
                (assignment_id,),
            ).fetchone()
            if row is None:
                return None
            return _decode_assignment(row[0])

    def snapshot(self, plan_id: str) -> RunSnapshot:
        with self._lock:
            run = self._connection.execute(
                "SELECT goal_id, status FROM runs WHERE plan_id = ?", (plan_id,)
            ).fetchone()
            if run is None:
                raise KeyError(plan_id)
            steps = {
                step_id: StepStatus(status)
                for step_id, status in self._connection.execute(
                    "SELECT step_id, status FROM run_steps WHERE plan_id = ? ORDER BY rowid",
                    (plan_id,),
                )
            }
        return RunSnapshot(plan_id, run[0], RunStatus(run[1]), steps)

    def goal(self, plan_id: str) -> SharedGoal:
        with self._lock:
            row = self._connection.execute(
                "SELECT goal_json FROM runs WHERE plan_id = ?", (plan_id,)
            ).fetchone()
        if row is None:
            raise KeyError(plan_id)
        return _decode_goal(row[0])

    def plan(self, plan_id: str) -> Plan:
        with self._lock:
            row = self._connection.execute(
                "SELECT plan_json FROM runs WHERE plan_id = ?", (plan_id,)
            ).fetchone()
        if row is None:
            raise KeyError(plan_id)
        return _decode_plan(row[0])

    def close(self) -> None:
        with self._lock:
            self._connection.close()
