import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.authority import AllowAllAuthorizer
from ump.collaboration import Coordinator
from ump.coordinator_store import CoordinatorStore, RunStatus, StepStatus
from ump.models import (
    Assignment,
    AssignmentStatus,
    CancellationRequest,
    Capability,
    Outcome,
    Plan,
    PlanStep,
    RobotManifest,
    SharedGoal,
    payload,
)
from ump.runtime import Participant, Registry
from ump.simulation import SimulatedRobot
from ump.transport import InMemoryBus, make_envelope


class OneStepPlanner:
    planner_id = "ump.test.one-step/v1"

    def __init__(self, robot_id: str) -> None:
        self.robot_id = robot_id

    def propose(self, goal, manifests, states):
        del manifests, states
        return Plan(
            "plan-cancel-1",
            goal.goal_id,
            self.planner_id,
            "Run one cancellable task",
            (
                PlanStep(
                    "work",
                    "Perform bounded work",
                    self.robot_id,
                    "ump.test.work/v1",
                    {},
                    "Work reaches its native terminal state",
                ),
            ),
        )


class BlockingRobot(SimulatedRobot):
    def __init__(self, manifest: RobotManifest, cancellation_accepted: bool) -> None:
        super().__init__(manifest)
        self.started = threading.Event()
        self.release = threading.Event()
        self.cancellation_accepted = cancellation_accepted
        self.cancel_calls = 0

    def accept(self, assignment: Assignment) -> Outcome:
        self.started.set()
        if not self.release.wait(timeout=5):
            raise RuntimeError("test adapter timed out waiting for cancellation")
        return Outcome(
            assignment.assignment_id,
            self.manifest().robot_id,
            True,
            "Native execution returned after cancellation request",
        )

    def cancel(self, assignment_id: str, reason: str) -> tuple[bool, str]:
        del assignment_id
        self.cancel_calls += 1
        self.release.set()
        if self.cancellation_accepted:
            return True, f"Native controller cancelled work: {reason}"
        return False, f"Native controller could not cancel work: {reason}"


def manifest() -> RobotManifest:
    return RobotManifest(
        "robot-cancellable-1",
        "Example Robotics",
        "C1",
        "mobile_base",
        (Capability("ump.test.work/v1", "Perform bounded test work"),),
    )


def running_system(cancellation_accepted: bool):
    bus = InMemoryBus()
    registry = Registry(bus)
    robot = BlockingRobot(manifest(), cancellation_accepted)
    participant = Participant(
        robot, bus, authorizer=AllowAllAuthorizer(), clock_ms=lambda: 2_000
    )
    participant.announce(1_000)
    coordinator = Coordinator(
        "coordinator", bus, registry, require_authority=False
    )
    goal = SharedGoal("goal-cancel-1", "Perform cancellable work", (robot.manifest().robot_id,))
    errors = []

    def submit() -> None:
        try:
            coordinator.submit(goal, OneStepPlanner(robot.manifest().robot_id), 1_100)
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=submit)
    thread.start()
    if not robot.started.wait(timeout=5):
        raise RuntimeError("assignment did not reach the blocking adapter")
    return bus, robot, participant, coordinator, thread, errors


class CancellationTests(unittest.TestCase):
    def test_native_accepted_cancellation_is_durable_terminal_outcome(self):
        bus, robot, participant, coordinator, thread, errors = running_system(True)
        requested = coordinator.cancel("plan-cancel-1", "Owner stopped the plan", 2_000)
        thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(requested), 1)
        self.assertEqual(robot.cancel_calls, 1)
        self.assertEqual(coordinator.snapshot("plan-cancel-1").status, RunStatus.CANCELLED)
        outcomes = [item for item in bus.trace if item.message_type == "outcome"]
        self.assertTrue(outcomes)
        self.assertTrue(all(item.payload["status"] == "cancelled" for item in outcomes))
        coordinator.close()
        participant.close()

    def test_native_rejection_does_not_claim_cancellation(self):
        bus, robot, participant, coordinator, thread, errors = running_system(False)
        coordinator.cancel("plan-cancel-1", "Owner requested cancellation", 2_000)
        thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(robot.cancel_calls, 1)
        self.assertEqual(coordinator.snapshot("plan-cancel-1").status, RunStatus.SUCCEEDED)
        ack = next(item for item in bus.trace if item.message_type == "cancellation_ack")
        self.assertEqual(ack.payload["status"], "rejected")
        coordinator.close()
        participant.close()

    def test_participant_ignores_cancellation_from_non_issuer(self):
        bus, robot, participant, coordinator, thread, errors = running_system(True)
        assignment = coordinator.store.assignment(
            next(
                item.payload["assignment_id"]
                for item in bus.trace
                if item.message_type == "assignment"
            )
        )
        assert assignment is not None
        bus.publish(
            make_envelope(
                "cancellation_request",
                "different-coordinator",
                "attacker-session",
                1,
                2_000,
                payload(
                    CancellationRequest(
                        assignment.assignment_id,
                        robot.manifest().robot_id,
                        "Unauthorized stop",
                    )
                ),
            )
        )
        self.assertEqual(robot.cancel_calls, 0)
        coordinator.cancel("plan-cancel-1", "Authorized cleanup", 2_001)
        thread.join(timeout=5)
        self.assertEqual(errors, [])
        coordinator.close()
        participant.close()

    def test_restart_turns_interrupted_cancellation_into_unknown(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "coordinator.sqlite3"
            store = CoordinatorStore(path)
            goal = SharedGoal("goal-1", "Perform work", ("robot-1",))
            step = PlanStep(
                "work",
                "Perform work",
                "robot-1",
                "ump.test.work/v1",
                {},
                "Work completes",
            )
            plan = Plan("plan-1", goal.goal_id, "planner-1", "One step", (step,))
            assignment = Assignment("assignment-1", goal.goal_id, plan.plan_id, step)
            store.create_run(goal, plan, (assignment,), 1_000)
            store.mark_dispatched(assignment.assignment_id, 1_001)
            store.request_cancellation(plan.plan_id, 1_002)
            store.close()

            reopened = CoordinatorStore(path)
            snapshot = reopened.snapshot(plan.plan_id)
            reopened.close()
            self.assertEqual(snapshot.status, RunStatus.UNKNOWN)
            self.assertEqual(snapshot.steps["work"], StepStatus.UNKNOWN)

    def test_owner_can_request_cancellation_after_unknown_restart_state(self):
        store = CoordinatorStore()
        goal = SharedGoal("goal-1", "Perform work", ("robot-1",))
        step = PlanStep(
            "work",
            "Perform work",
            "robot-1",
            "ump.test.work/v1",
            {},
            "Work completes",
        )
        plan = Plan("plan-1", goal.goal_id, "planner-1", "One step", (step,))
        assignment = Assignment("assignment-1", goal.goal_id, plan.plan_id, step)
        store.create_run(goal, plan, (assignment,), 1_000)
        store.mark_dispatched(assignment.assignment_id, 1_001)
        store.record_outcome(assignment.assignment_id, AssignmentStatus.UNKNOWN, 1_002)

        requested = store.request_cancellation(plan.plan_id, 1_003)

        self.assertEqual(requested, (assignment,))
        self.assertEqual(
            store.snapshot(plan.plan_id).steps[step.step_id],
            StepStatus.CANCELLATION_REQUESTED,
        )
        store.close()


if __name__ == "__main__":
    unittest.main()
