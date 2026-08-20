import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.authority import AllowAllAuthorizer
from ump.collaboration import Coordinator
from ump.coordinator_store import CoordinatorStore, RunStatus, StepStatus
from ump.demo import WarehousePlanner, build_demo
from ump.journal import SqliteAssignmentJournal, assignment_fingerprint
from ump.models import (
    AssignmentQuery,
    AssignmentSnapshot,
    AssignmentStatus,
    Outcome,
    SharedGoal,
    payload,
)
from ump.runtime import Participant, Registry
from ump.simulation import SimulatedRobot
from ump.transport import InMemoryBus, make_envelope


def coordinator_fixture(store_path: Path):
    source_bus, _, participants = build_demo()
    bus = InMemoryBus()
    registry = Registry(bus)
    for envelope in source_bus.trace:
        if envelope.message_type in {"manifest", "state"}:
            bus.publish(envelope)
    goal = SharedGoal(
        "goal-reconcile-1",
        "Move the package",
        tuple(participant.robot_id for participant in participants),
        deadline_ms=60_000,
    )
    coordinator = Coordinator(
        "coordinator", bus, registry, CoordinatorStore(store_path), require_authority=False
    )
    plan = coordinator.submit(goal, WarehousePlanner(), 1_100)
    assignment_envelope = next(
        item for item in bus.trace if item.message_type == "assignment"
    )
    assignment = coordinator.store.assignment(assignment_envelope.payload["assignment_id"])
    assert assignment is not None
    manifest = registry.peers[assignment.step.assigned_robot_id].manifest
    assert manifest is not None
    return coordinator, plan, assignment_envelope, assignment, manifest


class ReconciliationTests(unittest.TestCase):
    def test_operator_resolution_becomes_durable_participant_evidence(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            coordinator_path = root / "coordinator.sqlite3"
            participant_path = root / "participant.sqlite3"
            first, plan, envelope, assignment, manifest = coordinator_fixture(
                coordinator_path
            )
            first.close()
            journal = SqliteAssignmentJournal(participant_path)
            journal.claim(assignment, "coordinator", 1_200)
            journal.close()
            journal = SqliteAssignmentJournal(participant_path)
            journal.resolve_unknown(
                assignment.assignment_id,
                AssignmentStatus.SUCCEEDED,
                "Inspection route completion confirmed from native mission log",
                "manufacturer/service-console",
                "mission-log:inspection-2026-08-20-001",
                1_900,
            )
            journal.close()

            recovery_bus = InMemoryBus()
            worker = Participant(
                SimulatedRobot(manifest),
                recovery_bus,
                SqliteAssignmentJournal(participant_path),
                AllowAllAuthorizer(),
                clock_ms=lambda: 2_000,
            )
            recovered = Coordinator(
                "coordinator",
                recovery_bus,
                Registry(recovery_bus),
                CoordinatorStore(coordinator_path),
                require_authority=False,
            )
            recovered.reconcile(plan.plan_id, 2_000)

            snapshot = recovered.snapshot(plan.plan_id)
            self.assertEqual(snapshot.status, RunStatus.ACTIVE)
            self.assertEqual(snapshot.steps["inspect-route"], StepStatus.SUCCEEDED)
            dispatched = [
                item.payload["assignment_id"]
                for item in recovery_bus.trace
                if item.message_type == "assignment"
            ]
            self.assertNotIn(assignment.assignment_id, dispatched)
            self.assertTrue(dispatched)
            recovered.close()
            worker.close()

    def test_terminal_participant_evidence_resolves_crash_gap_and_resumes_dependencies(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            coordinator_path = root / "coordinator.sqlite3"
            participant_path = root / "participant.sqlite3"
            first, plan, envelope, _, manifest = coordinator_fixture(coordinator_path)
            first.close()

            execution_bus = InMemoryBus()
            worker = Participant(
                SimulatedRobot(manifest),
                execution_bus,
                SqliteAssignmentJournal(participant_path),
                AllowAllAuthorizer(),
                clock_ms=lambda: 1_200,
            )
            execution_bus.publish(envelope)
            worker.close()

            recovery_bus = InMemoryBus()
            recovered_worker = Participant(
                SimulatedRobot(manifest),
                recovery_bus,
                SqliteAssignmentJournal(participant_path),
                AllowAllAuthorizer(),
                clock_ms=lambda: 2_000,
            )
            recovered = Coordinator(
                "coordinator",
                recovery_bus,
                Registry(recovery_bus),
                CoordinatorStore(coordinator_path),
                require_authority=False,
            )
            queried = recovered.reconcile(plan.plan_id, 2_000)
            snapshot = recovered.snapshot(plan.plan_id)

            self.assertEqual(queried, (envelope.payload["assignment_id"],))
            self.assertEqual(snapshot.status, RunStatus.ACTIVE)
            self.assertEqual(snapshot.steps["inspect-route"], StepStatus.SUCCEEDED)
            self.assertTrue(
                any(
                    item.message_type == "assignment"
                    and item.payload["step"]["step_id"] == "carry-package"
                    for item in recovery_bus.trace
                )
            )
            recovered.close()
            recovered_worker.close()

    def test_participant_unknown_evidence_keeps_coordinator_unknown(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            coordinator_path = root / "coordinator.sqlite3"
            participant_path = root / "participant.sqlite3"
            first, plan, envelope, assignment, manifest = coordinator_fixture(coordinator_path)
            first.close()
            journal = SqliteAssignmentJournal(participant_path)
            journal.claim(assignment, "coordinator", 1_200)
            journal.close()

            recovery_bus = InMemoryBus()
            worker = Participant(
                SimulatedRobot(manifest),
                recovery_bus,
                SqliteAssignmentJournal(participant_path),
                AllowAllAuthorizer(),
                clock_ms=lambda: 2_000,
            )
            recovered = Coordinator(
                "coordinator",
                recovery_bus,
                Registry(recovery_bus),
                CoordinatorStore(coordinator_path),
                require_authority=False,
            )
            recovered.reconcile(plan.plan_id, 2_000)

            self.assertEqual(recovered.snapshot(plan.plan_id).status, RunStatus.UNKNOWN)
            self.assertFalse(
                any(item.message_type == "assignment" for item in recovery_bus.trace)
            )
            recovered.close()
            worker.close()

    def test_participant_ignores_query_from_originally_unauthorized_issuer(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            first, _, envelope, _, manifest = coordinator_fixture(root / "coordinator.sqlite3")
            first.close()
            bus = InMemoryBus()
            worker = Participant(
                SimulatedRobot(manifest),
                bus,
                SqliteAssignmentJournal(root / "participant.sqlite3"),
                AllowAllAuthorizer(),
                clock_ms=lambda: 1_200,
            )
            bus.publish(envelope)
            bus.publish(
                make_envelope(
                    "assignment_query",
                    "different-coordinator",
                    "attacker-session",
                    1,
                    2_000,
                    payload(
                        AssignmentQuery(
                            envelope.payload["assignment_id"], manifest.robot_id
                        )
                    ),
                )
            )
            snapshots = [item for item in bus.trace if item.message_type == "assignment_snapshot"]
            self.assertEqual(snapshots, [])
            worker.close()

    def test_coordinator_rejects_snapshot_with_wrong_assignment_fingerprint(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            coordinator_path = root / "coordinator.sqlite3"
            first, plan, envelope, assignment, manifest = coordinator_fixture(coordinator_path)
            first.close()
            bus = InMemoryBus()
            recovered = Coordinator(
                "coordinator",
                bus,
                Registry(bus),
                CoordinatorStore(coordinator_path),
                require_authority=False,
            )
            outcome = Outcome(
                assignment.assignment_id, manifest.robot_id, True, "Forged completion"
            )
            forged = AssignmentSnapshot(
                assignment.assignment_id,
                manifest.robot_id,
                AssignmentStatus.SUCCEEDED,
                outcome.description,
                "0" * 64,
                outcome,
            )
            self.assertNotEqual(forged.fingerprint, assignment_fingerprint(assignment))
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                bus.publish(
                    make_envelope(
                        "assignment_snapshot",
                        manifest.robot_id,
                        "robot-session",
                        1,
                        2_000,
                        payload(forged),
                        envelope.correlation_id,
                    )
                )
            self.assertEqual(recovered.snapshot(plan.plan_id).status, RunStatus.UNKNOWN)
            recovered.close()


if __name__ == "__main__":
    unittest.main()
