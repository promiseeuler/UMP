import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.cli import authority_main, reconcile_main
from ump.journal import SqliteAssignmentJournal
from ump.models import Assignment, PlanStep


class AuthorityCliTests(unittest.TestCase):
    def test_owner_can_grant_audit_and_revoke_local_lease(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = str(Path(temporary_directory) / "authority.sqlite3")
            common = ["--robot-id", "robot-1", "--database", database]
            output = io.StringIO()
            with redirect_stdout(output):
                result = authority_main(
                    common
                    + [
                        "grant",
                        "--lease-id",
                        "lease-1",
                        "--issuer-id",
                        "coordinator-1",
                        "--capability",
                        "ump.material.carry/v1",
                        "--issued-at-ms",
                        "1000",
                        "--expires-at-ms",
                        "10000",
                    ]
                )
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "active")

            output = io.StringIO()
            with redirect_stdout(output):
                result = authority_main(common + ["events", "--lease-id", "lease-1"])
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(output.getvalue())[0]["event_type"], "grant")

            output = io.StringIO()
            with redirect_stdout(output):
                result = authority_main(
                    common
                    + [
                        "revoke",
                        "--lease-id",
                        "lease-1",
                        "--expected-revision",
                        "1",
                        "--reason",
                        "operator request",
                        "--occurred-at-ms",
                        "2000",
                    ]
                )
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "revoked")


class ReconciliationCliTests(unittest.TestCase):
    def test_operator_can_resolve_unknown_assignment_with_evidence(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = str(Path(temporary_directory) / "assignments.sqlite3")
            assignment = Assignment(
                "assignment-1",
                "goal-1",
                "plan-1",
                PlanStep(
                    "inspect",
                    "Inspect an aisle",
                    "robot-1",
                    "ump.navigation.inspect/v1",
                    {},
                    "Inspection log is complete",
                ),
            )
            journal = SqliteAssignmentJournal(database)
            journal.claim(assignment, "coordinator-1", 1_000)
            journal.close()
            journal = SqliteAssignmentJournal(database)
            journal.close()

            output = io.StringIO()
            with redirect_stdout(output):
                result = reconcile_main(
                    [
                        "--database", database,
                        "--assignment-id", assignment.assignment_id,
                        "--status", "succeeded",
                        "--description", "Native mission log confirms completion",
                        "--resolver-id", "operator/alice",
                        "--evidence", "mission-log:42",
                        "--occurred-at-ms", "1200",
                    ]
                )
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "succeeded")
            verified = SqliteAssignmentJournal(database)
            self.assertEqual(
                verified.resolution(assignment.assignment_id)["resolver_id"],
                "operator/alice",
            )
            verified.close()


if __name__ == "__main__":
    unittest.main()
