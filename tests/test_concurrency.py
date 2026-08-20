from pathlib import Path
import sys
from threading import Barrier, Event, Lock
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.authority import AllowAllAuthorizer
from ump.models import (
    Assignment,
    AssignmentStatus,
    Mode,
    Outcome,
    PlanStep,
    RobotManifest,
    RobotState,
    Safety,
    payload,
)
from ump.runtime import Participant, Registry
from ump.simulation import capability
from ump.transport import InMemoryBus, make_envelope


class ConcurrentAdapter:
    def __init__(self) -> None:
        self._manifest = RobotManifest(
            "concurrent-robot",
            "UMP Test",
            "C1",
            "test",
            (capability("ump.test.concurrent/v1", "Execute independent test work"),),
        )
        self.barrier = Barrier(2, timeout=2.0)

    def manifest(self):
        return self._manifest

    def state(self):
        return RobotState(
            self._manifest.robot_id,
            Mode.IDLE,
            Safety.NORMAL,
            "Ready",
            "Await work",
            0.0,
            "Test robot is ready.",
        )

    def accept(self, assignment):
        self.barrier.wait()
        return Outcome(
            assignment.assignment_id,
            self._manifest.robot_id,
            True,
            "Independent work completed concurrently",
        )

    def cancel(self, assignment_id, reason):
        del assignment_id, reason
        return False, "Already complete"


class ConcurrentExecutionTests(unittest.TestCase):
    @staticmethod
    def assignment(sequence):
        return Assignment(
            f"assignment-{sequence}",
            "goal-concurrent",
            "plan-concurrent",
            PlanStep(
                f"step-{sequence}",
                "Execute independent work",
                "concurrent-robot",
                "ump.test.concurrent/v1",
                {},
                "Work completes",
            ),
        )

    def test_opted_in_independent_assignments_overlap_and_publish_in_order(self):
        bus = InMemoryBus()
        registry = Registry(bus)
        adapter = ConcurrentAdapter()
        participant = Participant(
            adapter,
            bus,
            authorizer=AllowAllAuthorizer(),
            execution_workers=2,
            clock_ms=lambda: 2_000,
        )
        completed = Event()
        completion_lock = Lock()
        outcomes = []

        def observe(envelope):
            with completion_lock:
                outcomes.append(envelope)
                if len(outcomes) == 2:
                    completed.set()

        bus.subscribe("outcome", observe)
        participant.announce(1_000)
        try:
            for sequence in (1, 2):
                assignment = self.assignment(sequence)
                bus.publish(
                    make_envelope(
                        "assignment",
                        "coordinator",
                        "coordinator-session",
                        sequence,
                        1_100 + sequence,
                        payload(assignment),
                        "goal-concurrent",
                    )
                )
            self.assertTrue(completed.wait(3.0), "independent work did not overlap")
            robot_events = [
                event for event in bus.trace if event.source_id == participant.robot_id
            ]
            self.assertEqual(
                [event.sequence for event in robot_events],
                list(range(1, len(robot_events) + 1)),
            )
            self.assertEqual(len(outcomes), 2)
            self.assertIsNotNone(registry.peers[participant.robot_id].state)
        finally:
            participant.close()

    def test_worker_count_is_bounded(self):
        with self.assertRaisesRegex(ValueError, "between 0 and 32"):
            Participant(ConcurrentAdapter(), InMemoryBus(), execution_workers=33)

    def test_adapter_exception_becomes_explicit_unknown_outcome(self):
        class FailingAdapter(ConcurrentAdapter):
            def accept(self, assignment):
                del assignment
                raise RuntimeError("native connection disappeared")

        bus = InMemoryBus()
        participant = Participant(
            FailingAdapter(),
            bus,
            authorizer=AllowAllAuthorizer(),
            execution_workers=1,
            clock_ms=lambda: 2_000,
        )
        completed = Event()
        outcomes = []
        bus.subscribe("outcome", lambda envelope: (outcomes.append(envelope), completed.set()))
        try:
            bus.publish(
                make_envelope(
                    "assignment",
                    "coordinator",
                    "coordinator-session",
                    1,
                    1_100,
                    payload(self.assignment(1)),
                    "goal-concurrent",
                )
            )
            self.assertTrue(completed.wait(2.0))
            self.assertEqual(outcomes[0].payload["status"], AssignmentStatus.UNKNOWN.value)
            self.assertFalse(outcomes[0].payload["succeeded"])
        finally:
            participant.close()


if __name__ == "__main__":
    unittest.main()
