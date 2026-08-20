import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.authority import AuthorizationError, SqliteAuthorityStore
from ump.models import Assignment, AuthorityLease, PlanStep, RobotManifest, payload
from ump.runtime import Participant
from ump.simulation import SimulatedRobot, capability
from ump.transport import InMemoryBus, make_envelope


ROBOT_ID = "robot-1"
ISSUER_ID = "coordinator-1"
CAPABILITY = "ump.material.carry/v1"


def lease(**changes) -> AuthorityLease:
    values = {
        "lease_id": "lease-1",
        "grantor_robot_id": ROBOT_ID,
        "issuer_id": ISSUER_ID,
        "capabilities": (CAPABILITY,),
        "issued_at_ms": 1_000,
        "expires_at_ms": 10_000,
        "maximum_clock_uncertainty_ms": 100,
        "revision": 1,
    }
    values.update(changes)
    return AuthorityLease(**values)


def assignment(**changes) -> Assignment:
    values = {
        "assignment_id": "assignment-1",
        "goal_id": "goal-1",
        "plan_id": "plan-1",
        "step": PlanStep(
            "carry",
            "Carry package one",
            ROBOT_ID,
            CAPABILITY,
            {"object": "package-1"},
            "Package reaches the handoff point",
        ),
        "authority_lease_id": "lease-1",
    }
    values.update(changes)
    return Assignment(**values)


class CountingRobot(SimulatedRobot):
    def __init__(self):
        super().__init__(
            RobotManifest(
                ROBOT_ID,
                "Example Robotics",
                "R1",
                "mobile_base",
                (capability(CAPABILITY, "Carry a bounded payload"),),
            )
        )
        self.execution_count = 0

    def accept(self, item):
        self.execution_count += 1
        return super().accept(item)


class AuthorityStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary_directory.name) / "authority.sqlite3"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_granted_scope_persists_and_authorizes_only_holder_capability_and_time(self):
        store = SqliteAuthorityStore(ROBOT_ID, self.path)
        store.grant(lease(), 900)
        store.close()

        reopened = SqliteAuthorityStore(ROBOT_ID, self.path)
        authorized = reopened.authorize(ISSUER_ID, ROBOT_ID, assignment(), 5_000)
        self.assertEqual(authorized.lease_id, "lease-1")
        with self.assertRaisesRegex(AuthorizationError, "does not hold"):
            reopened.authorize("different-coordinator", ROBOT_ID, assignment(), 5_000)
        different_capability = assignment(
            step=PlanStep(
                "inspect",
                "Inspect route",
                ROBOT_ID,
                "ump.navigation.inspect/v1",
                {},
                "Route is inspected",
            )
        )
        with self.assertRaisesRegex(AuthorizationError, "outside"):
            reopened.authorize(ISSUER_ID, ROBOT_ID, different_capability, 5_000)
        with self.assertRaisesRegex(AuthorizationError, "not yet"):
            reopened.authorize(ISSUER_ID, ROBOT_ID, assignment(), 1_050)
        with self.assertRaisesRegex(AuthorizationError, "expired"):
            reopened.authorize(ISSUER_ID, ROBOT_ID, assignment(), 9_900)
        reopened.close()

    def test_revocation_is_revision_checked_durable_and_audited(self):
        store = SqliteAuthorityStore(ROBOT_ID, self.path)
        store.grant(lease(), 900)
        with self.assertRaisesRegex(AuthorizationError, "revision"):
            store.revoke("lease-1", 2, 2_000, "operator request")
        store.revoke("lease-1", 1, 2_000, "operator request")
        events = store.events("lease-1")
        self.assertEqual([event[1] for event in events], ["grant", "revoke"])
        store.close()

        reopened = SqliteAuthorityStore(ROBOT_ID, self.path)
        with self.assertRaisesRegex(AuthorizationError, "revoked"):
            reopened.authorize(ISSUER_ID, ROBOT_ID, assignment(), 5_000)
        reopened.close()

    def test_lease_identity_and_revision_cannot_be_silently_replaced(self):
        store = SqliteAuthorityStore(ROBOT_ID, self.path)
        store.grant(lease(), 900)
        with self.assertRaisesRegex(AuthorizationError, "identity"):
            store.grant(lease(issuer_id="attacker", revision=2), 1_000)
        with self.assertRaisesRegex(AuthorizationError, "increase by one"):
            store.grant(lease(revision=3), 1_000)
        store.close()


class ParticipantAuthorizationTests(unittest.TestCase):
    def publish_assignment(self, bus, item, issuer=ISSUER_ID, sequence=1, now_ms=5_000):
        bus.publish(
            make_envelope(
                "assignment",
                issuer,
                "issuer-session",
                sequence,
                now_ms,
                payload(item),
                item.goal_id,
            )
        )

    def test_participant_denies_assignments_when_no_policy_is_configured(self):
        bus = InMemoryBus()
        robot = CountingRobot()
        participant = Participant(robot, bus)
        self.publish_assignment(bus, assignment())
        outcome = next(item for item in bus.trace if item.message_type == "outcome")
        participant.close()
        self.assertEqual(robot.execution_count, 0)
        self.assertEqual(outcome.payload["status"], "rejected")
        self.assertIn("no assignment authority policy", outcome.payload["description"])

    def test_valid_local_lease_allows_exactly_scoped_adapter_execution(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = SqliteAuthorityStore(
                ROBOT_ID, Path(temporary_directory) / "authority.sqlite3"
            )
            store.grant(lease(), 900)
            bus = InMemoryBus()
            robot = CountingRobot()
            participant = Participant(
                robot, bus, authorizer=store, clock_ms=lambda: 5_000
            )
            self.publish_assignment(bus, assignment())
            participant.close()
            outcome = next(item for item in bus.trace if item.message_type == "outcome")
            self.assertEqual(robot.execution_count, 1)
            self.assertEqual(outcome.payload["status"], "succeeded")

    def test_backdated_envelope_cannot_extend_expired_lease(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = SqliteAuthorityStore(
                ROBOT_ID, Path(temporary_directory) / "authority.sqlite3"
            )
            store.grant(lease(), 900)
            bus = InMemoryBus()
            robot = CountingRobot()
            participant = Participant(
                robot, bus, authorizer=store, clock_ms=lambda: 11_000
            )
            self.publish_assignment(bus, assignment(), now_ms=5_000)
            participant.close()
            outcome = next(item for item in bus.trace if item.message_type == "outcome")
            self.assertEqual(robot.execution_count, 0)
            self.assertEqual(outcome.payload["status"], "rejected")
            self.assertIn("expired", outcome.payload["description"])


if __name__ == "__main__":
    unittest.main()
