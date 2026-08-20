import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.journal import ClaimKind, SqliteAssignmentJournal
from ump.models import Assignment, AssignmentStatus, Outcome, PlanStep


def assignment() -> Assignment:
    return Assignment(
        assignment_id="assignment-durable-1",
        goal_id="goal-1",
        plan_id="plan-1",
        step=PlanStep(
            step_id="carry",
            description="Carry a package",
            assigned_robot_id="robot-1",
            capability="ump.material.carry/v1",
            inputs={"object": "package-1"},
            completion_criteria="Package reaches the handoff point",
        ),
    )


class SqliteJournalTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary_directory.name) / "assignments.sqlite3"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_terminal_outcome_survives_restart_and_is_replayed(self):
        item = assignment()
        outcome = Outcome(
            item.assignment_id,
            item.step.assigned_robot_id,
            True,
            "Package reached the handoff point",
        )
        first = SqliteAssignmentJournal(self.path)
        self.assertEqual(first.claim(item, "coordinator-1", 1_000).kind, ClaimKind.NEW)
        first.complete(item, outcome, 1_100)
        first.close()

        reopened = SqliteAssignmentJournal(self.path)
        claim = reopened.claim(item, "coordinator-1", 1_200)
        reopened.close()
        self.assertEqual(claim.kind, ClaimKind.REPLAY)
        self.assertEqual(claim.outcome, outcome)

    def test_interrupted_accepted_assignment_becomes_unknown_not_new(self):
        item = assignment()
        first = SqliteAssignmentJournal(self.path)
        self.assertEqual(first.claim(item, "coordinator-1", 1_000).kind, ClaimKind.NEW)
        first.close()

        reopened = SqliteAssignmentJournal(self.path)
        claim = reopened.claim(item, "coordinator-1", 1_200)
        reopened.close()
        self.assertEqual(claim.kind, ClaimKind.UNCERTAIN)
        self.assertIsNone(claim.outcome)

    def test_same_id_with_different_payload_is_rejected(self):
        item = assignment()
        journal = SqliteAssignmentJournal(self.path)
        self.assertEqual(journal.claim(item, "coordinator-1", 1_000).kind, ClaimKind.NEW)
        changed = replace(item, step=replace(item.step, inputs={"object": "package-2"}))
        self.assertEqual(
            journal.claim(changed, "coordinator-1", 1_001).kind,
            ClaimKind.CONFLICT,
        )
        journal.close()

    def test_terminal_outcome_cannot_be_changed(self):
        item = assignment()
        journal = SqliteAssignmentJournal(self.path)
        journal.claim(item, "coordinator-1", 1_000)
        succeeded = Outcome(item.assignment_id, "robot-1", True, "Completed")
        journal.complete(item, succeeded, 1_100)
        changed = Outcome(
            item.assignment_id,
            "robot-1",
            False,
            "Changed result",
            AssignmentStatus.FAILED,
        )
        with self.assertRaisesRegex(ValueError, "immutable"):
            journal.complete(item, changed, 1_200)
        journal.close()

    def test_active_assignment_exclusively_reserves_robot_local_resource(self):
        first = replace(
            assignment(),
            step=replace(assignment().step, resources=("tool/gripper-a",)),
        )
        second = replace(
            first,
            assignment_id="assignment-durable-2",
            step=replace(first.step, step_id="carry-2"),
        )
        journal = SqliteAssignmentJournal(self.path)
        self.assertEqual(
            journal.claim(first, "coordinator-1", 1_000).kind, ClaimKind.NEW
        )
        self.assertEqual(
            journal.claim(second, "coordinator-1", 1_001).kind,
            ClaimKind.RESOURCE_CONFLICT,
        )
        journal.close()

    def test_terminal_completion_releases_resource_for_next_assignment(self):
        first = replace(
            assignment(),
            step=replace(assignment().step, resources=("zone/loading-bay",)),
        )
        second = replace(
            first,
            assignment_id="assignment-durable-2",
            step=replace(first.step, step_id="carry-2"),
        )
        journal = SqliteAssignmentJournal(self.path)
        journal.claim(first, "coordinator-1", 1_000)
        journal.complete(
            first,
            Outcome(first.assignment_id, "robot-1", True, "Resource work completed"),
            1_100,
        )
        self.assertEqual(
            journal.claim(second, "coordinator-1", 1_101).kind, ClaimKind.NEW
        )
        journal.close()

    def test_unknown_assignment_retains_resource_reservation_after_restart(self):
        first = replace(
            assignment(),
            step=replace(assignment().step, resources=("fixture/inspection-1",)),
        )
        second = replace(
            first,
            assignment_id="assignment-durable-2",
            step=replace(first.step, step_id="inspect-2"),
        )
        journal = SqliteAssignmentJournal(self.path)
        journal.claim(first, "coordinator-1", 1_000)
        journal.close()

        reopened = SqliteAssignmentJournal(self.path)
        self.assertEqual(
            reopened.claim(second, "coordinator-1", 1_100).kind,
            ClaimKind.RESOURCE_CONFLICT,
        )
        self.assertEqual(
            reopened.lookup(first.assignment_id).status,
            AssignmentStatus.UNKNOWN,
        )
        reopened.close()

    def test_operator_evidence_resolves_unknown_and_releases_resources(self):
        item = replace(
            assignment(),
            step=replace(assignment().step, resources=("fixture/inspection-1",)),
        )
        journal = SqliteAssignmentJournal(self.path)
        journal.claim(item, "coordinator-1", 1_000)
        journal.close()

        reopened = SqliteAssignmentJournal(self.path)
        outcome = reopened.resolve_unknown(
            item.assignment_id,
            AssignmentStatus.SUCCEEDED,
            "Barcode scan and destination sensor confirm delivery",
            "operator/alice",
            "inspection-record:site-a/2026-08-20/42",
            1_200,
        )
        self.assertTrue(outcome.succeeded)
        self.assertEqual(reopened.lookup(item.assignment_id).outcome, outcome)
        self.assertEqual(
            reopened.resolution(item.assignment_id),
            {
                "assignment_id": item.assignment_id,
                "status": "succeeded",
                "resolver_id": "operator/alice",
                "evidence": "inspection-record:site-a/2026-08-20/42",
                "occurred_at_ms": 1_200,
            },
        )
        second = replace(item, assignment_id="assignment-durable-2")
        self.assertEqual(
            reopened.claim(second, "coordinator-1", 1_201).kind,
            ClaimKind.NEW,
        )
        reopened.close()

    def test_resolution_rejects_unknown_status_and_terminal_rewrite(self):
        item = assignment()
        journal = SqliteAssignmentJournal(self.path)
        journal.claim(item, "coordinator-1", 1_000)
        journal.close()
        reopened = SqliteAssignmentJournal(self.path)
        with self.assertRaisesRegex(ValueError, "known terminal"):
            reopened.resolve_unknown(
                item.assignment_id, AssignmentStatus.UNKNOWN, "Unknown", "operator/a", "No evidence", 1_100
            )
        reopened.resolve_unknown(
            item.assignment_id, AssignmentStatus.FAILED, "Not completed", "operator/a", "Camera review", 1_101
        )
        with self.assertRaisesRegex(ValueError, "only an unknown"):
            reopened.resolve_unknown(
                item.assignment_id, AssignmentStatus.SUCCEEDED, "Changed", "operator/b", "Different review", 1_102
            )
        reopened.close()


if __name__ == "__main__":
    unittest.main()
