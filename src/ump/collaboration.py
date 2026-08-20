from __future__ import annotations

from collections.abc import Mapping
from uuid import uuid4

from .coordinator_store import CoordinatorStore, RunSnapshot, RunStatus, StepStatus
from .journal import assignment_fingerprint
from .models import (
    Assignment,
    AssignmentQuery,
    AssignmentSnapshot,
    AssignmentStatus,
    CancellationRequest,
    CancellationStatus,
    Outcome,
    Plan,
    SharedGoal,
    payload,
)
from .planner import Planner
from .runtime import Registry
from .transport import MessageBus, ProtocolDecodeError, encode_envelope, make_envelope


class PlanValidationError(ValueError):
    pass


def _validate_core_message_size(message_type: str, body: object) -> None:
    try:
        envelope = make_envelope(
            message_type,
            "s" * 128,
            "s" * 128,
            1,
            0,
            payload(body),
            "c" * 128,
        )
        encode_envelope(envelope)
    except ProtocolDecodeError as error:
        raise PlanValidationError(
            f"{message_type} does not fit the 64 KiB core profile"
        ) from error


def validate_plan(goal: SharedGoal, plan: Plan, registry: Registry, now_ms: int) -> None:
    _validate_core_message_size("plan", plan)
    if plan.goal_id != goal.goal_id or plan.revision < 1 or not plan.steps:
        raise PlanValidationError("plan does not identify a valid goal and revision")
    if len(plan.steps) > 256:
        raise PlanValidationError("plan exceeds 256 steps")

    step_ids = [step.step_id for step in plan.steps]
    if any(not item for item in step_ids) or len(step_ids) != len(set(step_ids)):
        raise PlanValidationError("step identifiers must be non-empty and unique")
    known = set(step_ids)
    graph: dict[str, tuple[str, ...]] = {}
    participants = set(goal.participant_ids)

    for step in plan.steps:
        if step.assigned_robot_id not in participants:
            raise PlanValidationError(f"unknown participant: {step.assigned_robot_id}")
        peer = registry.peers.get(step.assigned_robot_id)
        if not peer or not peer.manifest or not registry.is_fresh(step.assigned_robot_id, now_ms):
            raise PlanValidationError(f"participant is absent or stale: {step.assigned_robot_id}")
        capability = peer.manifest.capability(step.capability)
        if not capability or capability.availability.value != "available":
            raise PlanValidationError(
                f"{step.assigned_robot_id} does not have available capability {step.capability}"
            )
        if step.deadline_ms is not None and goal.deadline_ms is not None:
            if step.deadline_ms > goal.deadline_ms:
                raise PlanValidationError(f"step exceeds goal deadline: {step.step_id}")
        if step.deadline_ms is not None and now_ms > step.deadline_ms:
            raise PlanValidationError(f"step deadline has passed: {step.step_id}")
        missing = set(step.depends_on) - known
        if missing:
            raise PlanValidationError(f"unknown dependencies: {sorted(missing)}")
        graph[step.step_id] = step.depends_on

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(step_id: str) -> None:
        if step_id in visiting:
            raise PlanValidationError("plan contains a dependency cycle")
        if step_id in visited:
            return
        visiting.add(step_id)
        for dependency in graph[step_id]:
            visit(dependency)
        visiting.remove(step_id)
        visited.add(step_id)

    for step_id in graph:
        visit(step_id)


class Coordinator:
    def __init__(
        self,
        coordinator_id: str,
        bus: MessageBus,
        registry: Registry,
        store: CoordinatorStore | None = None,
        authority_lease_ids: Mapping[str, str] | None = None,
        require_authority: bool = True,
    ) -> None:
        self.coordinator_id = coordinator_id
        self.bus = bus
        self.registry = registry
        self._sequence = 0
        self.session_id = str(uuid4())
        self.store = store or CoordinatorStore()
        self.authority_lease_ids = dict(authority_lease_ids or {})
        self.require_authority = require_authority
        self.outcomes: dict[str, bool] = {}
        self.acknowledgements: dict[str, str] = {}
        bus.subscribe("assignment_ack", self._acknowledgement)
        bus.subscribe("outcome", self._outcome)
        bus.subscribe("assignment_snapshot", self._assignment_snapshot)
        bus.subscribe("cancellation_ack", self._cancellation_acknowledgement)

    def _publish(self, message_type: str, body: object, now_ms: int, correlation_id: str) -> None:
        self._sequence += 1
        self.bus.publish(
            make_envelope(
                message_type,
                self.coordinator_id,
                self.session_id,
                self._sequence,
                now_ms,
                payload(body),
                correlation_id,
            )
        )

    def _outcome(self, envelope) -> None:
        assignment_id = envelope.payload["assignment_id"]
        expected_robot = self.store.robot_for_assignment(assignment_id)
        if expected_robot is None:
            return
        if envelope.payload.get("robot_id") != envelope.source_id:
            raise ValueError("outcome robot_id does not match message source")
        if envelope.source_id != expected_robot:
            raise ValueError("outcome source is not the assigned robot")
        self.outcomes[assignment_id] = envelope.payload["succeeded"]
        status = AssignmentStatus(envelope.payload["status"])
        if self.store.status_for_assignment(assignment_id) is StepStatus.UNKNOWN:
            plan_id = self.store.reconcile_outcome(
                assignment_id, status, envelope.timestamp_ms
            )
        else:
            plan_id = self.store.record_outcome(
                assignment_id, status, envelope.timestamp_ms
            )
        if plan_id is not None:
            self._dispatch_ready(plan_id, envelope.timestamp_ms)

    def _acknowledgement(self, envelope) -> None:
        assignment_id = envelope.payload["assignment_id"]
        expected_robot = self.store.robot_for_assignment(assignment_id)
        if expected_robot is None:
            return
        if envelope.payload.get("robot_id") != envelope.source_id:
            raise ValueError("acknowledgement robot_id does not match message source")
        if envelope.source_id != expected_robot:
            raise ValueError("acknowledgement source is not the assigned robot")
        status = AssignmentStatus(envelope.payload["status"])
        self.acknowledgements[assignment_id] = status.value
        self.store.record_acknowledgement(assignment_id, status, envelope.timestamp_ms)

    def _cancellation_acknowledgement(self, envelope) -> None:
        assignment_id = envelope.payload["assignment_id"]
        expected_robot = self.store.robot_for_assignment(assignment_id)
        if expected_robot is None:
            return
        if envelope.payload.get("robot_id") != envelope.source_id:
            raise ValueError("cancellation robot_id does not match message source")
        if envelope.source_id != expected_robot:
            raise ValueError("cancellation source is not the assigned robot")
        status = CancellationStatus(envelope.payload["status"])
        accepted = status in {
            CancellationStatus.ACCEPTED,
            CancellationStatus.ALREADY_TERMINAL,
        }
        self.store.record_cancellation_acknowledgement(
            assignment_id, accepted, envelope.timestamp_ms
        )

    def _assignment_snapshot(self, envelope) -> None:
        data = envelope.payload
        assignment_id = data["assignment_id"]
        expected_robot = self.store.robot_for_assignment(assignment_id)
        if expected_robot is None:
            return
        if data.get("robot_id") != envelope.source_id:
            raise ValueError("snapshot robot_id does not match message source")
        if envelope.source_id != expected_robot:
            raise ValueError("snapshot source is not the assigned robot")
        outcome_data = data.get("outcome")
        if outcome_data is None:
            return
        outcome = Outcome(
            assignment_id=outcome_data["assignment_id"],
            robot_id=outcome_data["robot_id"],
            succeeded=outcome_data["succeeded"],
            description=outcome_data["description"],
            status=AssignmentStatus(outcome_data["status"]),
            outputs=outcome_data.get("outputs", {}),
        )
        snapshot = AssignmentSnapshot(
            assignment_id=assignment_id,
            robot_id=data["robot_id"],
            status=AssignmentStatus(data["status"]),
            description=data["description"],
            fingerprint=data["fingerprint"],
            outcome=outcome,
        )
        assignment = self.store.assignment(assignment_id)
        if assignment is None or snapshot.fingerprint != assignment_fingerprint(assignment):
            raise ValueError("snapshot fingerprint does not match stored assignment")
        plan_id = self.store.reconcile_outcome(
            snapshot.assignment_id, snapshot.status, envelope.timestamp_ms
        )
        self.outcomes[snapshot.assignment_id] = outcome.succeeded
        if plan_id is not None:
            self._dispatch_ready(plan_id, envelope.timestamp_ms)

    def _prepare(
        self, goal: SharedGoal, planner: Planner, now_ms: int
    ) -> tuple[Plan, tuple[Assignment, ...]]:
        _validate_core_message_size("goal", goal)
        manifests = {
            robot_id: peer.manifest
            for robot_id, peer in self.registry.peers.items()
            if peer.manifest and robot_id in goal.participant_ids
        }
        states = {
            robot_id: peer.state
            for robot_id, peer in self.registry.peers.items()
            if peer.state and robot_id in goal.participant_ids
        }
        plan = planner.propose(goal, manifests, states)
        validate_plan(goal, plan, self.registry, now_ms)
        if self.require_authority:
            missing = sorted(
                {
                    step.assigned_robot_id
                    for step in plan.steps
                    if step.assigned_robot_id not in self.authority_lease_ids
                }
            )
            if missing:
                raise PlanValidationError(
                    f"missing authority leases for assigned robots: {missing}"
                )
        assignments = tuple(
            Assignment(
                assignment_id=str(uuid4()),
                goal_id=goal.goal_id,
                plan_id=plan.plan_id,
                step=step,
                authority_lease_id=self.authority_lease_ids.get(step.assigned_robot_id),
            )
            for step in plan.steps
        )
        return plan, assignments

    def submit(self, goal: SharedGoal, planner: Planner, now_ms: int) -> Plan:
        plan, assignments = self._prepare(goal, planner, now_ms)
        self.store.create_run(goal, plan, assignments, now_ms)
        self._publish("goal", goal, now_ms, goal.goal_id)
        self._publish("plan", plan, now_ms, goal.goal_id)
        self._dispatch_ready(plan.plan_id, now_ms)
        return plan

    def submit_many(
        self,
        goals: tuple[SharedGoal, ...],
        planner: Planner,
        now_ms: int,
    ) -> tuple[Plan, ...]:
        if not 1 <= len(goals) <= 256:
            raise PlanValidationError("goal batch requires 1 to 256 goals")
        goal_ids = [goal.goal_id for goal in goals]
        if len(goal_ids) != len(set(goal_ids)):
            raise PlanValidationError("goal batch identifiers must be unique")
        prepared = tuple(self._prepare(goal, planner, now_ms) for goal in goals)
        plan_ids = [plan.plan_id for plan, _ in prepared]
        if len(plan_ids) != len(set(plan_ids)):
            raise PlanValidationError("planner returned duplicate plan identifiers")
        for goal, (plan, assignments) in zip(goals, prepared, strict=True):
            self.store.create_run(goal, plan, assignments, now_ms)
        for goal, (plan, _) in zip(goals, prepared, strict=True):
            self._publish("goal", goal, now_ms, goal.goal_id)
            self._publish("plan", plan, now_ms, goal.goal_id)
            self._dispatch_ready(plan.plan_id, now_ms)
        return tuple(plan for plan, _ in prepared)

    def _dispatch_ready(self, plan_id: str, now_ms: int) -> None:
        for assignment in self.store.ready_assignments(plan_id):
            if self.store.mark_dispatched(assignment.assignment_id, now_ms):
                self._publish("assignment", assignment, now_ms, assignment.goal_id)

    def execute(self, goal: SharedGoal, planner: Planner, now_ms: int) -> Plan:
        plan = self.submit(goal, planner, now_ms)
        status = self.store.snapshot(plan.plan_id).status
        if status is not RunStatus.SUCCEEDED:
            raise RuntimeError(f"plan did not complete successfully: {status.value}")
        return plan

    def execute_many(
        self,
        goals: tuple[SharedGoal, ...],
        planner: Planner,
        now_ms: int,
    ) -> tuple[Plan, ...]:
        plans = self.submit_many(goals, planner, now_ms)
        incomplete = {
            plan.plan_id: self.store.snapshot(plan.plan_id).status
            for plan in plans
            if self.store.snapshot(plan.plan_id).status is not RunStatus.SUCCEEDED
        }
        if incomplete:
            statuses = {plan_id: status.value for plan_id, status in incomplete.items()}
            raise RuntimeError(f"goal batch did not complete successfully: {statuses}")
        return plans

    def replan(self, previous_plan_id: str, planner: Planner, now_ms: int) -> Plan:
        previous_snapshot = self.store.snapshot(previous_plan_id)
        if previous_snapshot.status is RunStatus.ACTIVE:
            raise PlanValidationError("an active plan must be cancelled before replanning")
        previous_plan = self.store.plan(previous_plan_id)
        goal = self.store.goal(previous_plan_id)
        plan, assignments = self._prepare(goal, planner, now_ms)
        if plan.plan_id == previous_plan.plan_id:
            raise PlanValidationError("replanning requires a new plan identifier")
        if plan.revision != previous_plan.revision + 1:
            raise PlanValidationError("replanning must increment the plan revision by one")
        if plan.supersedes_plan_id != previous_plan.plan_id:
            raise PlanValidationError("replanning must identify the exact predecessor plan")
        self.store.create_run(goal, plan, assignments, now_ms)
        self._publish("goal", goal, now_ms, goal.goal_id)
        self._publish("plan", plan, now_ms, goal.goal_id)
        self._dispatch_ready(plan.plan_id, now_ms)
        return plan

    def snapshot(self, plan_id: str) -> RunSnapshot:
        return self.store.snapshot(plan_id)

    def resume_active(self, now_ms: int) -> tuple[str, ...]:
        """Dispatch safe pending work after peers have re-announced following restart."""
        plan_ids = self.store.active_plan_ids()
        for plan_id in plan_ids:
            self._dispatch_ready(plan_id, now_ms)
        return plan_ids

    def reconcile(self, plan_id: str, now_ms: int) -> tuple[str, ...]:
        assignment_ids = []
        for assignment in self.store.unknown_assignments(plan_id):
            assignment_ids.append(assignment.assignment_id)
            self._publish(
                "assignment_query",
                AssignmentQuery(assignment.assignment_id, assignment.step.assigned_robot_id),
                now_ms,
                assignment.goal_id,
            )
        return tuple(assignment_ids)

    def cancel(self, plan_id: str, reason: str, now_ms: int) -> tuple[str, ...]:
        if not reason.strip() or len(reason.encode("utf-8")) > 4_096:
            raise ValueError("cancellation reason must contain 1 to 4096 UTF-8 bytes")
        assignments = self.store.request_cancellation(plan_id, now_ms)
        for assignment in assignments:
            self._publish(
                "cancellation_request",
                CancellationRequest(
                    assignment.assignment_id,
                    assignment.step.assigned_robot_id,
                    reason,
                ),
                now_ms,
                assignment.goal_id,
            )
        return tuple(assignment.assignment_id for assignment in assignments)

    def close(self) -> None:
        self.store.close()
