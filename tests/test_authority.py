import sys
from hashlib import sha256
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.authority import (
    AuthorizationError,
    AuthorityReadError,
    SqliteAuthorityStore,
    read_authority_events,
    read_authority_lease,
    read_authority_leases,
)
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
        with self.assertRaisesRegex(AuthorizationError, "cannot be renewed"):
            store.grant(lease(revision=3, expires_at_ms=20_000), 2_100)
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

    def test_read_only_inventory_reports_effective_status_and_filters(self):
        store = SqliteAuthorityStore(ROBOT_ID, self.path)
        store.grant(lease(lease_id="active"), 900)
        store.grant(
            lease(lease_id="future", issued_at_ms=6_000, expires_at_ms=12_000),
            901,
        )
        store.grant(lease(lease_id="expired", expires_at_ms=4_000), 902)
        store.grant(
            lease(lease_id="other-issuer", issuer_id="coordinator-2"), 903
        )
        store.grant(lease(lease_id="revoked"), 904)
        store.revoke("revoked", 1, 2_000, "operator request")
        store.close()

        summaries = read_authority_leases(self.path, ROBOT_ID, 5_000)
        statuses = {
            item.lease.lease_id: item.effective_status for item in summaries
        }
        self.assertEqual(statuses["active"], "active")
        self.assertEqual(statuses["future"], "not_yet_valid")
        self.assertEqual(statuses["expired"], "expired")
        self.assertEqual(statuses["revoked"], "revoked")
        self.assertEqual(
            [item.lease.lease_id for item in read_authority_leases(
                self.path, ROBOT_ID, 5_000, effective_status="active"
            )],
            ["other-issuer", "active"],
        )
        self.assertEqual(
            [item.lease.lease_id for item in read_authority_leases(
                self.path, ROBOT_ID, 5_000, issuer_id="coordinator-2"
            )],
            ["other-issuer"],
        )
        self.assertEqual(
            [item.lease.lease_id for item in read_authority_leases(
                self.path,
                ROBOT_ID,
                5_000,
                effective_status="expired",
                limit=1,
            )],
            ["expired"],
        )
        self.assertEqual(
            read_authority_lease(self.path, ROBOT_ID, "future", 5_000).effective_status,
            "not_yet_valid",
        )
        self.assertEqual(
            read_authority_lease(self.path, ROBOT_ID, "active", 1_050).effective_status,
            "not_yet_valid",
        )
        self.assertEqual(
            read_authority_lease(self.path, ROBOT_ID, "active", 1_100).effective_status,
            "active",
        )
        self.assertEqual(
            read_authority_lease(self.path, ROBOT_ID, "active", 9_900).effective_status,
            "expired",
        )

    def test_read_inventory_is_non_mutating_and_fails_closed(self):
        store = SqliteAuthorityStore(ROBOT_ID, self.path)
        store.grant(lease(), 900)
        store.close()
        before = sha256(self.path.read_bytes()).hexdigest()
        files_before = sorted(item.name for item in self.path.parent.iterdir())

        read_authority_leases(self.path, ROBOT_ID, 5_000)
        read_authority_lease(self.path, ROBOT_ID, "lease-1", 5_000)
        read_authority_events(self.path, ROBOT_ID, "lease-1")

        self.assertEqual(before, sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(
            files_before, sorted(item.name for item in self.path.parent.iterdir())
        )
        missing = self.path.parent / "missing" / "authority.sqlite3"
        with self.assertRaisesRegex(AuthorityReadError, "does not exist"):
            read_authority_leases(missing, ROBOT_ID, 5_000)
        self.assertFalse(missing.parent.exists())
        self.assertEqual(read_authority_leases(self.path, "robot-2", 5_000), ())
        with self.assertRaises(KeyError):
            read_authority_lease(self.path, "robot-2", "lease-1", 5_000)

    def test_bounded_events_return_latest_transitions_in_chronological_order(self):
        store = SqliteAuthorityStore(ROBOT_ID, self.path)
        store.grant(lease(), 900)
        store.grant(lease(revision=2, expires_at_ms=12_000), 1_000)
        store.revoke("lease-1", 2, 2_000, "operator request")
        store.close()

        events = read_authority_events(
            self.path, ROBOT_ID, "lease-1", limit=2
        )
        self.assertEqual([item[1] for item in events], ["grant", "revoke"])
        self.assertEqual([item[2] for item in events], [2, 3])


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
