import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.authority import AllowAllAuthorizer
from ump.journal import SqliteAssignmentJournal
from ump.models import Assignment, PlanStep, RobotManifest, payload
from ump.runtime import Participant
from ump.simulation import SimulatedRobot, capability
from ump.transport import InMemoryBus, make_envelope


class CountingRobot(SimulatedRobot):
    def __init__(self, manifest):
        super().__init__(manifest)
        self.execution_count = 0

    def accept(self, assignment):
        self.execution_count += 1
        return super().accept(assignment)


class ParticipantDurabilityTests(unittest.TestCase):
    def test_completed_assignment_is_not_reexecuted_after_participant_restart(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "assignments.sqlite3"
            manifest = RobotManifest(
                robot_id="robot-1",
                manufacturer="Example Robotics",
                model="R1",
                robot_class="mobile_base",
                capabilities=(
                    capability("ump.material.carry/v1", "Carry a bounded payload"),
                ),
            )
            robot = CountingRobot(manifest)
            assignment = Assignment(
                assignment_id="assignment-1",
                goal_id="goal-1",
                plan_id="plan-1",
                step=PlanStep(
                    step_id="carry",
                    description="Carry package one",
                    assigned_robot_id="robot-1",
                    capability="ump.material.carry/v1",
                    inputs={"object": "package-1"},
                    completion_criteria="Package reaches the handoff point",
                ),
            )

            first_bus = InMemoryBus()
            first = Participant(
                robot,
                first_bus,
                SqliteAssignmentJournal(path),
                AllowAllAuthorizer(),
            )
            first_bus.publish(
                make_envelope(
                    "assignment",
                    "coordinator",
                    "coordinator-session-1",
                    1,
                    1_000,
                    payload(assignment),
                    assignment.goal_id,
                )
            )
            first.close()
            self.assertEqual(robot.execution_count, 1)

            second_bus = InMemoryBus()
            second = Participant(
                robot,
                second_bus,
                SqliteAssignmentJournal(path),
                AllowAllAuthorizer(),
            )
            second_bus.publish(
                make_envelope(
                    "assignment",
                    "coordinator",
                    "coordinator-session-2",
                    1,
                    1_100,
                    payload(assignment),
                    assignment.goal_id,
                )
            )
            second.close()

            self.assertEqual(robot.execution_count, 1)
            acknowledgement = next(
                item for item in second_bus.trace if item.message_type == "assignment_ack"
            )
            outcome = next(item for item in second_bus.trace if item.message_type == "outcome")
            self.assertEqual(acknowledgement.payload["status"], "succeeded")
            self.assertTrue(outcome.payload["succeeded"])


if __name__ == "__main__":
    unittest.main()
