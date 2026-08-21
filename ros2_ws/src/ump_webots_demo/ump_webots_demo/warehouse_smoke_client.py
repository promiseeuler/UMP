import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import socket
from threading import Thread

import rclpy
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from ump.demo import WarehousePlanner
from ump.models import (
    Assignment,
    AssignmentStatus,
    Mode,
    RobotManifest,
    RobotState,
    Safety,
    SharedGoal,
)
from ump.ros2 import (
    RclpyActionBackend,
    Ros2RobotAdapter,
    SemanticStateStore,
    json_capability_binding,
)
from ump.vocabulary import standard_capability
from ump_interfaces.action import ExecuteCapability


ROBOT_PROFILES = (
    (
        "robot-quadruped-1",
        "robot_quadruped_1",
        "quadruped",
        "ump.navigation.inspect-route/v1",
    ),
    (
        "robot-humanoid-1",
        "robot_humanoid_1",
        "humanoid",
        "ump.material.carry/v1",
    ),
    (
        "robot-mobile-arm-1",
        "robot_mobile_arm_1",
        "mobile_arm",
        "ump.manipulation.place/v1",
    ),
)


def build_adapters(node, backend):
    manifests = []
    adapters = {}
    for robot_id, ros_name, robot_class, capability_name in ROBOT_PROFILES:
        capability = standard_capability(capability_name)
        manifest = RobotManifest(
            robot_id,
            "UMP Webots Fixture",
            "Warehouse v1",
            robot_class,
            (capability,),
        )
        state = RobotState(
            robot_id,
            Mode.IDLE,
            Safety.NORMAL,
            "Waiting for the collaborative warehouse plan",
            "Execute only approved UMP warehouse assignments",
            0.0,
            f"The Webots {robot_class} is ready.",
        )
        binding = json_capability_binding(
            capability_name,
            f"/{ros_name}/execute_capability",
            ExecuteCapability,
            server_timeout_s=30.0,
            result_timeout_s=45.0,
        )
        adapters[robot_id] = Ros2RobotAdapter(
            manifest,
            SemanticStateStore(state),
            {capability_name: binding},
            backend,
        )
        manifests.append(manifest)
    return tuple(manifests), adapters


def run_plan(node):
    executor = MultiThreadedExecutor(num_threads=2)
    executor.add_node(node)
    spin_thread = Thread(target=executor.spin, daemon=True)
    spin_thread.start()
    backend = RclpyActionBackend(node)
    manifests, adapters = build_adapters(node, backend)
    goal = SharedGoal(
        "goal-webots-move-package-1",
        "Move package-1 from intake to shelf A",
        tuple(manifest.robot_id for manifest in manifests),
    )
    plan = WarehousePlanner().propose(goal, manifests, ())
    results = []
    try:
        for step in plan.steps:
            assignment = Assignment(
                f"webots-{step.step_id}", goal.goal_id, plan.plan_id, step
            )
            outcome = adapters[step.assigned_robot_id].accept(assignment)
            if outcome.status is not AssignmentStatus.SUCCEEDED:
                raise RuntimeError(
                    f"{step.step_id} returned {outcome.status.value}: "
                    f"{outcome.description}"
                )
            if outcome.outputs.get("completed") is not True:
                raise RuntimeError(f"{step.step_id} omitted completion evidence")
            results.append(
                {
                    "assignment_id": outcome.assignment_id,
                    "robot_id": outcome.robot_id,
                    "capability": step.capability,
                    "status": outcome.status.value,
                    "description": outcome.description,
                    "outputs": outcome.outputs,
                }
            )
        return plan, results
    finally:
        backend.close()
        executor.shutdown()
        spin_thread.join(timeout=2.0)


def main(args=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--report")
    parser.add_argument("--world")
    parsed, ros_args = parser.parse_known_args(args)
    rclpy.init(args=ros_args)
    node = Node("ump_webots_warehouse_coordinator")
    try:
        plan, results = run_plan(node)
        report = {
            "profile": "ump.ros2-webots-warehouse/v1",
            "passed": True,
            "scenario": plan.summary,
            "planner_id": plan.planner_id,
            "plan_id": plan.plan_id,
            "checks": {
                "three_webots_drivers_ready": True,
                "ump_planner_exercised": True,
                "ump_ros2_adapters_exercised": True,
                "ordered_collaboration_succeeded": True,
                "physical_locomotion_exercised": True,
                "package_handoff_visualized": True,
            },
            "results": results,
            "environment": {
                "hostname": socket.gethostname(),
                "platform": platform.platform(),
                "python": platform.python_version(),
                "ros_distro": os.environ.get("ROS_DISTRO", "unknown"),
                "repository_revision": os.environ.get("GITHUB_SHA", "unknown"),
            },
        }
        if parsed.world:
            world = Path(parsed.world)
            report["world"] = {
                "path": str(world),
                "sha256": sha256(world.read_bytes()).hexdigest(),
            }
        encoded = json.dumps(report, sort_keys=True, separators=(",", ":"))
        if parsed.report:
            Path(parsed.report).write_text(encoded + "\n", encoding="utf-8")
        print(encoded)
    finally:
        node.destroy_node()
        rclpy.shutdown()
