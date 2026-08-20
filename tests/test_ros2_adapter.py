import sys
import threading
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.models import (
    Assignment,
    AssignmentStatus,
    Capability,
    Mode,
    PlanStep,
    RobotManifest,
    RobotState,
    Safety,
)
from ump.ros2 import (
    NativeActionResult,
    NativeActionStatus,
    Ros2RobotAdapter,
    RosActionBinding,
    SemanticStateStore,
    json_capability_binding,
)


class FakeActionBackend:
    def __init__(self, result: NativeActionResult) -> None:
        self.result = result
        self.executions = []
        self.cancellations = []

    def execute(self, binding, assignment):
        self.executions.append((binding, assignment))
        return self.result

    def cancel(self, assignment_id, reason):
        self.cancellations.append((assignment_id, reason))
        return True, "Fake ROS action cancellation accepted"


def fixture(result_status=NativeActionStatus.SUCCEEDED):
    capability = Capability(
        "ump.navigation.inspect/v1",
        "Inspect a bounded route",
        input_schema={"type": "object"},
    )
    manifest = RobotManifest(
        "robot-ros-1",
        "Example Robotics",
        "R2",
        "quadruped",
        (capability,),
    )
    state = RobotState(
        manifest.robot_id,
        Mode.IDLE,
        Safety.NORMAL,
        "Waiting for work",
        "Inspect assigned routes",
        0.0,
        "ROS robot is idle.",
    )
    store = SemanticStateStore(state)
    binding = RosActionBinding(
        capability.name,
        "/robot/inspect_route",
        object,
        lambda assignment, action_type: (assignment.step.inputs, action_type),
        lambda result, status: NativeActionResult(
            NativeActionStatus.SUCCEEDED, f"ROS result {result} status {status}"
        ),
    )
    backend = FakeActionBackend(
        NativeActionResult(result_status, f"Native action {result_status.value}")
    )
    adapter = Ros2RobotAdapter(
        manifest, store, {capability.name: binding}, backend
    )
    assignment = Assignment(
        "assignment-ros-1",
        "goal-1",
        "plan-1",
        PlanStep(
            "inspect",
            "Inspect route",
            manifest.robot_id,
            capability.name,
            {"route": "aisle-4"},
            "Route report is available",
        ),
    )
    return adapter, store, backend, assignment


class Ros2AdapterTests(unittest.TestCase):
    def test_action_result_maps_to_ump_outcome_without_control_translation(self):
        adapter, _, backend, assignment = fixture()
        outcome = adapter.accept(assignment)
        self.assertTrue(outcome.succeeded)
        self.assertEqual(outcome.status, AssignmentStatus.SUCCEEDED)
        self.assertEqual(backend.executions[0][1].step.inputs, {"route": "aisle-4"})

    def test_unknown_native_result_remains_unknown(self):
        adapter, _, _, assignment = fixture(NativeActionStatus.UNKNOWN)
        outcome = adapter.accept(assignment)
        self.assertFalse(outcome.succeeded)
        self.assertEqual(outcome.status, AssignmentStatus.UNKNOWN)

    def test_cancellation_is_delegated_to_ros_action_backend(self):
        adapter, _, backend, assignment = fixture()
        accepted, description = adapter.cancel(
            assignment.assignment_id, "Operator stopped the task"
        )
        self.assertTrue(accepted)
        self.assertIn("accepted", description)
        self.assertEqual(
            backend.cancellations,
            [(assignment.assignment_id, "Operator stopped the task")],
        )

    def test_unbound_capability_is_rejected_before_backend_execution(self):
        adapter, _, backend, assignment = fixture()
        unbound = replace(
            assignment,
            step=replace(assignment.step, capability="ump.private.unbound/v1"),
        )
        outcome = adapter.accept(unbound)
        self.assertEqual(outcome.status, AssignmentStatus.REJECTED)
        self.assertEqual(backend.executions, [])

    def test_semantic_state_store_is_thread_safe_and_identity_stable(self):
        adapter, store, _, _ = fixture()
        updated = replace(
            adapter.state(),
            mode=Mode.WORKING,
            progress=0.5,
            summary="ROS robot is halfway through route inspection.",
        )
        thread = threading.Thread(target=store.update, args=(updated,))
        thread.start()
        thread.join(timeout=2)
        self.assertEqual(adapter.state(), updated)
        with self.assertRaisesRegex(ValueError, "identity"):
            store.update(replace(updated, robot_id="different-robot"))

    def test_binding_keys_must_match_advertised_capabilities(self):
        adapter, store, backend, _ = fixture()
        binding = next(iter(adapter._bindings.values()))
        with self.assertRaisesRegex(ValueError, "advertised"):
            Ros2RobotAdapter(
                adapter.manifest(), store, {"different/v1": binding}, backend
            )

    def test_generic_json_action_codec_is_canonical_and_maps_status(self):
        class Action:
            class Goal:
                pass

        class Result:
            SUCCEEDED = 0
            FAILED = 1
            REJECTED = 2
            CANCELLED = 3
            UNKNOWN = 4

            def __init__(self):
                self.status = self.CANCELLED
                self.description = "Native action was cancelled"

        _, _, _, assignment = fixture()
        binding = json_capability_binding(
            assignment.step.capability, "/ump/execute", Action
        )
        goal = binding.goal_builder(assignment, Action)
        self.assertEqual(goal.assignment_id, assignment.assignment_id)
        self.assertEqual(goal.capability, assignment.step.capability)
        self.assertEqual(goal.inputs_json, '{"route":"aisle-4"}')
        result = binding.result_reader(Result(), 0)
        self.assertEqual(result.status, NativeActionStatus.CANCELLED)


if __name__ == "__main__":
    unittest.main()
