import sys
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.collaboration import Coordinator, PlanValidationError
from ump.coordinator_store import (
    CoordinatorStore,
    RunStatus,
    StepStatus,
    read_run_snapshot,
    read_run_summaries,
)
from ump.demo import WarehousePlanner, build_demo
from ump.models import (
    AssignmentAcknowledgement,
    AssignmentStatus,
    Outcome,
    SharedGoal,
    payload,
)
from ump.runtime import Registry
from ump.transport import InMemoryBus, make_envelope


def awareness_only_network():
    source_bus, _, participants = build_demo()
    bus = InMemoryBus()
    registry = Registry(bus)
    for envelope in source_bus.trace:
        if envelope.message_type in {"manifest", "state"}:
            bus.publish(envelope)
    goal = SharedGoal(
        goal_id="goal-async-1",
        description="Move the package",
        participant_ids=tuple(participant.robot_id for participant in participants),
        deadline_ms=60_000,
    )
    return bus, registry, goal


def robot_reply(bus, assignment, status, sequence):
    robot_id = assignment.payload["step"]["assigned_robot_id"]
    assignment_id = assignment.payload["assignment_id"]
    bus.publish(
        make_envelope(
            "assignment_ack",
            robot_id,
            f"{robot_id}-session",
            sequence,
            2_000 + sequence,
            payload(
                AssignmentAcknowledgement(
                    assignment_id,
                    robot_id,
                    AssignmentStatus.ACCEPTED,
                    "Accepted by test adapter",
                )
            ),
            assignment.correlation_id,
        )
    )
    succeeded = status is AssignmentStatus.SUCCEEDED
    bus.publish(
        make_envelope(
            "outcome",
            robot_id,
            f"{robot_id}-session",
            sequence + 1,
            2_001 + sequence,
            payload(
                Outcome(
                    assignment_id,
                    robot_id,
                    succeeded,
                    "Completed" if succeeded else "Execution failed",
                    status,
                )
            ),
            assignment.correlation_id,
        )
    )


class CoordinatorLifecycleTests(unittest.TestCase):
    def test_read_only_run_history_is_bounded_filtered_and_newest_first(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "coordinator.sqlite3"
            bus, registry, goal = awareness_only_network()
            coordinator = Coordinator(
                "coordinator",
                bus,
                registry,
                store=CoordinatorStore(database),
                require_authority=False,
            )
            first = coordinator.submit(goal, WarehousePlanner(), 1_100)
            second_goal = replace(
                goal,
                goal_id="goal-async-2",
                description="Move another package",
            )
            second = coordinator.submit(second_goal, WarehousePlanner(), 1_200)

            summaries = read_run_summaries(
                database, status=RunStatus.ACTIVE, limit=1
            )

            self.assertEqual(len(summaries), 1)
            self.assertEqual(summaries[0].plan_id, second.plan_id)
            self.assertEqual(summaries[0].created_at_ms, 1_200)
            self.assertNotEqual(summaries[0].plan_id, first.plan_id)
            coordinator.close()

    def test_durable_journal_is_bound_to_one_coordinator_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "coordinator.sqlite3"
            first_bus = InMemoryBus()
            first = Coordinator(
                "coordinator-1",
                first_bus,
                Registry(first_bus),
                store=CoordinatorStore(database),
            )
            first.close()

            second_store = CoordinatorStore(database, recover_interrupted=False)
            second_bus = InMemoryBus()
            with self.assertRaisesRegex(ValueError, "another identity"):
                Coordinator(
                    "coordinator-2",
                    second_bus,
                    Registry(second_bus),
                    store=second_store,
                )
            second_store.close()

    def test_observer_open_does_not_apply_restart_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            bus, registry, goal = awareness_only_network()
            database = Path(directory) / "coordinator.sqlite3"
            coordinator = Coordinator(
                "coordinator",
                bus,
                registry,
                store=CoordinatorStore(database),
                require_authority=False,
            )
            plan = coordinator.submit(goal, WarehousePlanner(), 1_100)

            observer = CoordinatorStore(database, recover_interrupted=False)
            observed = observer.snapshot(plan.plan_id)
            observer.close()

            self.assertEqual(observed.status, RunStatus.ACTIVE)
            self.assertEqual(observed.steps["inspect-route"], StepStatus.DISPATCHED)
            self.assertEqual(coordinator.snapshot(plan.plan_id).status, RunStatus.ACTIVE)
            coordinator.close()

    def test_read_only_snapshot_does_not_initialize_an_absent_database(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "absent.sqlite3"
            with self.assertRaises(sqlite3.OperationalError):
                read_run_snapshot(database, "plan-1")
            self.assertFalse(database.exists())

    def test_coordinator_requires_authority_lease_mapping_by_default(self):
        bus, registry, goal = awareness_only_network()
        coordinator = Coordinator("coordinator", bus, registry)
        with self.assertRaisesRegex(PlanValidationError, "missing authority leases"):
            coordinator.submit(goal, WarehousePlanner(), 1_100)
        coordinator.close()

    def test_delayed_outcomes_advance_dependencies_one_step_at_a_time(self):
        bus, registry, goal = awareness_only_network()
        coordinator = Coordinator("coordinator", bus, registry, require_authority=False)
        plan = coordinator.submit(goal, WarehousePlanner(), 1_100)

        assignments = [item for item in bus.trace if item.message_type == "assignment"]
        self.assertEqual(len(assignments), 1)
        self.assertEqual(
            coordinator.snapshot(plan.plan_id).steps["inspect-route"],
            StepStatus.DISPATCHED,
        )

        robot_reply(bus, assignments[0], AssignmentStatus.SUCCEEDED, 10)
        assignments = [item for item in bus.trace if item.message_type == "assignment"]
        self.assertEqual(len(assignments), 2)

        robot_reply(bus, assignments[1], AssignmentStatus.SUCCEEDED, 20)
        assignments = [item for item in bus.trace if item.message_type == "assignment"]
        self.assertEqual(len(assignments), 3)

        robot_reply(bus, assignments[2], AssignmentStatus.SUCCEEDED, 30)
        snapshot = coordinator.snapshot(plan.plan_id)
        coordinator.close()
        self.assertEqual(snapshot.status, RunStatus.SUCCEEDED)
        self.assertTrue(all(status is StepStatus.SUCCEEDED for status in snapshot.steps.values()))

    def test_failed_step_blocks_dependents_and_fails_run(self):
        bus, registry, goal = awareness_only_network()
        coordinator = Coordinator("coordinator", bus, registry, require_authority=False)
        plan = coordinator.submit(goal, WarehousePlanner(), 1_100)
        assignment = next(item for item in bus.trace if item.message_type == "assignment")
        robot_reply(bus, assignment, AssignmentStatus.FAILED, 10)

        assignments = [item for item in bus.trace if item.message_type == "assignment"]
        snapshot = coordinator.snapshot(plan.plan_id)
        coordinator.close()
        self.assertEqual(len(assignments), 1)
        self.assertEqual(snapshot.status, RunStatus.FAILED)
        self.assertEqual(snapshot.steps["carry-package"], StepStatus.PENDING)

    def test_spoofed_outcome_from_unassigned_robot_is_rejected(self):
        bus, registry, goal = awareness_only_network()
        coordinator = Coordinator("coordinator", bus, registry, require_authority=False)
        plan = coordinator.submit(goal, WarehousePlanner(), 1_100)
        assignment = next(item for item in bus.trace if item.message_type == "assignment")
        assignment_id = assignment.payload["assignment_id"]
        with self.assertRaisesRegex(ValueError, "not the assigned robot"):
            bus.publish(
                make_envelope(
                    "outcome",
                    "attacker-robot",
                    "attacker-session",
                    1,
                    2_000,
                    payload(
                        Outcome(
                            assignment_id,
                            "attacker-robot",
                            True,
                            "Forged completion",
                        )
                    ),
                    goal.goal_id,
                )
            )
        self.assertEqual(coordinator.snapshot(plan.plan_id).status, RunStatus.ACTIVE)
        coordinator.close()

    def test_late_accepted_acknowledgement_does_not_replace_terminal_outcome(self):
        bus, registry, goal = awareness_only_network()
        coordinator = Coordinator("coordinator", bus, registry, require_authority=False)
        plan = coordinator.submit(goal, WarehousePlanner(), 1_100)
        assignment = next(item for item in bus.trace if item.message_type == "assignment")
        robot_id = assignment.payload["step"]["assigned_robot_id"]
        assignment_id = assignment.payload["assignment_id"]
        bus.publish(
            make_envelope(
                "outcome",
                robot_id,
                "robot-session",
                2,
                2_000,
                payload(Outcome(assignment_id, robot_id, True, "Completed")),
                goal.goal_id,
            )
        )
        bus.publish(
            make_envelope(
                "assignment_ack",
                robot_id,
                "robot-session",
                1,
                1_999,
                payload(
                    AssignmentAcknowledgement(
                        assignment_id,
                        robot_id,
                        AssignmentStatus.ACCEPTED,
                        "Delayed acknowledgement",
                    )
                ),
                goal.goal_id,
            )
        )
        self.assertEqual(
            coordinator.snapshot(plan.plan_id).steps["inspect-route"],
            StepStatus.SUCCEEDED,
        )
        coordinator.close()

    def test_restart_marks_in_flight_step_unknown_without_redispatch(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "coordinator.sqlite3"
            bus, registry, goal = awareness_only_network()
            first = Coordinator(
                "coordinator",
                bus,
                registry,
                CoordinatorStore(path),
                require_authority=False,
            )
            plan = first.submit(goal, WarehousePlanner(), 1_100)
            self.assertEqual(
                first.snapshot(plan.plan_id).steps["inspect-route"],
                StepStatus.DISPATCHED,
            )
            first.close()

            restarted_bus = InMemoryBus()
            restarted_registry = Registry(restarted_bus)
            restarted = Coordinator(
                "coordinator",
                restarted_bus,
                restarted_registry,
                CoordinatorStore(path),
                require_authority=False,
            )
            snapshot = restarted.snapshot(plan.plan_id)
            restarted.close()

            self.assertEqual(snapshot.status, RunStatus.UNKNOWN)
            self.assertEqual(snapshot.steps["inspect-route"], StepStatus.UNKNOWN)
            self.assertFalse(
                any(item.message_type == "assignment" for item in restarted_bus.trace)
            )

    def test_unknown_run_never_dispatches_remaining_pending_work(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "coordinator.sqlite3"
            bus, registry, goal = awareness_only_network()
            first = Coordinator(
                "coordinator",
                bus,
                registry,
                CoordinatorStore(path),
                require_authority=False,
            )
            plan = first.submit(goal, WarehousePlanner(), 1_100)
            first.close()

            restarted_bus, restarted_registry, _ = awareness_only_network()
            restarted = Coordinator(
                "coordinator",
                restarted_bus,
                restarted_registry,
                CoordinatorStore(path),
                require_authority=False,
            )
            self.assertEqual(restarted.resume_active(2_000), ())
            self.assertEqual(restarted.snapshot(plan.plan_id).status, RunStatus.UNKNOWN)
            self.assertFalse(
                any(item.message_type == "assignment" for item in restarted_bus.trace)
            )
            restarted.close()

    def test_replanning_creates_immutable_incremented_lineage(self):
        bus, registry, goal = awareness_only_network()
        coordinator = Coordinator(
            "coordinator", bus, registry, require_authority=False
        )
        original = coordinator.submit(goal, WarehousePlanner(), 1_100)
        assignment = next(item for item in bus.trace if item.message_type == "assignment")
        robot_reply(bus, assignment, AssignmentStatus.FAILED, 10)
        original_snapshot = coordinator.snapshot(original.plan_id)

        class RevisionPlanner(WarehousePlanner):
            def propose(self, goal, manifests, states):
                proposal = super().propose(goal, manifests, states)
                return replace(
                    proposal,
                    plan_id="replacement-plan",
                    revision=original.revision + 1,
                    supersedes_plan_id=original.plan_id,
                )

        replacement = coordinator.replan(original.plan_id, RevisionPlanner(), 3_000)
        self.assertEqual(replacement.revision, 2)
        self.assertEqual(replacement.supersedes_plan_id, original.plan_id)
        self.assertEqual(original_snapshot, coordinator.snapshot(original.plan_id))
        self.assertEqual(coordinator.store.plan(original.plan_id), original)
        coordinator.close()

    def test_replanning_rejects_wrong_predecessor_before_publication(self):
        bus, registry, goal = awareness_only_network()
        coordinator = Coordinator(
            "coordinator", bus, registry, require_authority=False
        )
        original = coordinator.submit(goal, WarehousePlanner(), 1_100)
        assignment = next(item for item in bus.trace if item.message_type == "assignment")
        robot_reply(bus, assignment, AssignmentStatus.FAILED, 10)

        class WrongLineagePlanner(WarehousePlanner):
            def propose(self, goal, manifests, states):
                proposal = super().propose(goal, manifests, states)
                return replace(
                    proposal,
                    plan_id="wrong-lineage-plan",
                    revision=2,
                    supersedes_plan_id="different-plan",
                )

        before = len(bus.trace)
        with self.assertRaisesRegex(PlanValidationError, "exact predecessor"):
            coordinator.replan(original.plan_id, WrongLineagePlanner(), 3_000)
        self.assertEqual(len(bus.trace), before)
        with self.assertRaises(KeyError):
            coordinator.snapshot("wrong-lineage-plan")
        coordinator.close()


if __name__ == "__main__":
    unittest.main()
