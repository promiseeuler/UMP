from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.integrations import validate_mapping_report
from ump.integrations.massrobotics import MassRoboticsAdapter
from ump.integrations.open_rmf import OpenRmfAdapter
from ump.integrations.opc_ua import OpcUaRoboticsAdapter
from ump.integrations.vda5050 import Vda5050Adapter
from ump.authority import AllowAllAuthorizer
from ump.collaboration import Coordinator
from ump.models import Capability, Mode, Outcome, Plan, PlanStep, RobotManifest, RobotState, Safety, SharedGoal
from ump.ros2 import NativeActionResult, NativeActionStatus, Ros2RobotAdapter, RosActionBinding, SemanticStateStore
from ump.runtime import Participant, Registry
from ump.transport import InMemoryBus
from examples.standards_mapping_demo import run


ROOT = Path(__file__).parents[1]


class CrossStandardConformanceTests(unittest.TestCase):
    def test_all_five_mapping_fixtures_are_loss_aware_and_valid(self):
        for standard in ("massrobotics", "vda5050", "open_rmf", "ros2", "opc_ua"):
            with self.subTest(standard=standard):
                value, report = run(standard)
                validate_mapping_report(report)
                self.assertTrue(report.passed)
                self.assertTrue(value)

    def test_all_examples_are_runnable_from_source_checkout(self):
        for standard in ("massrobotics", "vda5050", "open_rmf", "ros2", "opc_ua"):
            completed = subprocess.run(
                [sys.executable, "examples/standards_mapping_demo.py", standard],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn('"passed": true', completed.stdout)

    def test_five_standards_facing_participants_complete_one_plan(self):
        bus = InMemoryBus()
        registry = Registry(bus)
        names = ("massrobotics", "vda5050", "open-rmf", "ros2", "opc-ua")
        adapters = []

        def manifest_and_state(index):
            robot_id = f"robot-{index}"
            capability = Capability(f"ump.integration.step-{index}/v1", f"Step {index}")
            return (
                RobotManifest(robot_id, "Example", f"M{index}", "robot", (capability,)),
                RobotState(robot_id, Mode.IDLE, Safety.NORMAL, "Idle", "Await", 0, "Idle"),
                capability,
            )

        def executor(assignment):
            return Outcome(assignment.assignment_id, assignment.step.assigned_robot_id, True, "Completed", outputs={})

        for index, adapter_type in enumerate((MassRoboticsAdapter, Vda5050Adapter, OpenRmfAdapter, OpcUaRoboticsAdapter), 1):
            manifest, state, _ = manifest_and_state(index)
            adapters.append(adapter_type(manifest, state, external_id=f"external-{index}", native_executor=executor))

        manifest, state, capability = manifest_and_state(5)

        class Backend:
            def execute(self, binding, assignment):
                return NativeActionResult(NativeActionStatus.SUCCEEDED, "Completed", {})

            def cancel(self, assignment_id, reason):
                return True, "Cancelled"

        binding = RosActionBinding(capability.name, "/ump/execute", object, lambda assignment, goal: goal, lambda result, status: result)
        adapters.insert(3, Ros2RobotAdapter(manifest, SemanticStateStore(state), {capability.name: binding}, Backend()))
        participants = [Participant(adapter, bus, authorizer=AllowAllAuthorizer(), clock_ms=lambda: 100) for adapter in adapters]
        for participant in participants:
            participant.announce(100)

        class Planner:
            planner_id = "ump.test.cross-standard/v1"

            def propose(self, goal, manifests, states):
                return Plan(
                    "cross-standard-plan", goal.goal_id, self.planner_id, "Five mapped steps",
                    tuple(PlanStep(f"step-{index}", f"Run {name}", f"robot-{index}", f"ump.integration.step-{index}/v1", {}, "Terminal success") for index, name in enumerate(names, 1)),
                )

        goal = SharedGoal("cross-standard-goal", "Complete one step through each standards-facing participant", tuple(f"robot-{index}" for index in range(1, 6)))
        plan = Coordinator("coordinator", bus, registry, require_authority=False).execute(goal, Planner(), 100)
        self.assertEqual(plan.plan_id, "cross-standard-plan")


if __name__ == "__main__":
    unittest.main()
