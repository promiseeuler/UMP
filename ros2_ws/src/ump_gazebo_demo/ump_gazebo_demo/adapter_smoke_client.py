import argparse
import json
from threading import Thread

import rclpy
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from ump.models import (
    Assignment,
    AssignmentStatus,
    Mode,
    PlanStep,
    RobotManifest,
    RobotState,
    Safety,
)
from ump.ros2 import (
    RclpyActionBackend,
    Ros2RobotAdapter,
    SemanticStateStore,
    json_capability_binding,
)
from ump.vocabulary import standard_capability
from ump_interfaces.action import ExecuteCapability


def parser():
    value = argparse.ArgumentParser(
        description="Exercise a native ROS action through the UMP adapter boundary"
    )
    value.add_argument("--action", required=True)
    value.add_argument("--robot-id", required=True)
    value.add_argument("--assignment-id", required=True)
    value.add_argument("--capability", required=True)
    value.add_argument("--inputs-json", required=True)
    value.add_argument("--timeout", type=float, default=10.0)
    return value


def exercise(arguments) -> dict[str, object]:
    try:
        inputs = json.loads(arguments.inputs_json)
    except json.JSONDecodeError as error:
        raise ValueError("inputs-json must contain valid JSON") from error
    if not isinstance(inputs, dict):
        raise ValueError("inputs-json must contain an object")
    capability = standard_capability(arguments.capability)
    manifest = RobotManifest(
        arguments.robot_id,
        "UMP Gazebo Fixture",
        "Proxy v1",
        "simulation_proxy",
        (capability,),
    )
    state = RobotState(
        arguments.robot_id,
        Mode.IDLE,
        Safety.NORMAL,
        "Waiting for adapter smoke assignment",
        "Exercise the manufacturer ROS action boundary",
        0.0,
        "The Gazebo proxy is ready for a high-level UMP assignment.",
    )
    node = Node("ump_adapter_smoke_client")
    executor = MultiThreadedExecutor(num_threads=2)
    executor.add_node(node)
    spin_thread = Thread(target=executor.spin, daemon=True)
    spin_thread.start()
    backend = RclpyActionBackend(node)
    binding = json_capability_binding(
        capability.name,
        arguments.action,
        ExecuteCapability,
        server_timeout_s=arguments.timeout,
        result_timeout_s=arguments.timeout,
    )
    adapter = Ros2RobotAdapter(
        manifest,
        SemanticStateStore(state),
        {capability.name: binding},
        backend,
    )
    assignment = Assignment(
        arguments.assignment_id,
        "goal-adapter-smoke",
        "plan-adapter-smoke",
        PlanStep(
            "adapter-smoke",
            "Exercise the native ROS action through the UMP adapter",
            arguments.robot_id,
            capability.name,
            inputs,
            "The native action returns a structured successful outcome",
        ),
    )
    try:
        outcome = adapter.accept(assignment)
        if outcome.status is not AssignmentStatus.SUCCEEDED:
            raise RuntimeError(
                f"UMP adapter returned {outcome.status.value}: {outcome.description}"
            )
        if outcome.outputs.get("completed") is not True:
            raise RuntimeError("UMP adapter outcome did not report completion")
        return {
            "assignment_id": outcome.assignment_id,
            "robot_id": outcome.robot_id,
            "status": outcome.status.value,
            "description": outcome.description,
            "outputs": outcome.outputs,
        }
    finally:
        backend.close()
        executor.shutdown()
        spin_thread.join(timeout=2.0)
        node.destroy_node()


def main(args=None):
    arguments = parser().parse_args(args)
    rclpy.init()
    try:
        print(json.dumps(exercise(arguments), sort_keys=True))
    finally:
        rclpy.shutdown()
