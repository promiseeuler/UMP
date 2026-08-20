import argparse
import json
import time

from action_msgs.msg import GoalStatus
import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node

from ump_interfaces.action import ExecuteCapability


def parser():
    value = argparse.ArgumentParser(description="Exercise a UMP ROS action fixture")
    value.add_argument("--action", required=True)
    value.add_argument("--assignment-id", required=True)
    value.add_argument("--capability", required=True)
    value.add_argument(
        "--expect", choices=("succeeded", "rejected", "cancelled"), required=True
    )
    value.add_argument("--timeout", type=float, default=10.0)
    return value


def _spin(node, future, deadline):
    while not future.done() and time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.05)
    if not future.done():
        raise TimeoutError("ROS action operation timed out")
    return future.result()


def exercise(arguments) -> dict[str, object]:
    node = Node("ump_action_smoke_client")
    client = ActionClient(node, ExecuteCapability, arguments.action)
    deadline = time.monotonic() + arguments.timeout
    try:
        if not client.wait_for_server(timeout_sec=arguments.timeout):
            raise TimeoutError("ROS action server was not available")
        goal = ExecuteCapability.Goal()
        goal.assignment_id = arguments.assignment_id
        goal.capability = arguments.capability
        goal.inputs_json = "{}"
        handle = _spin(node, client.send_goal_async(goal), deadline)
        if arguments.expect == "rejected":
            if handle.accepted:
                raise RuntimeError("goal was accepted but rejection was expected")
            return {"assignment_id": arguments.assignment_id, "status": "rejected"}
        if not handle.accepted:
            raise RuntimeError("goal was rejected unexpectedly")
        if arguments.expect == "cancelled":
            cancel = _spin(node, handle.cancel_goal_async(), deadline)
            if not cancel.goals_canceling:
                raise RuntimeError("action server did not accept cancellation")
        wrapped = _spin(node, handle.get_result_async(), deadline)
        expected_ros_status = (
            GoalStatus.STATUS_CANCELED
            if arguments.expect == "cancelled"
            else GoalStatus.STATUS_SUCCEEDED
        )
        expected_ump_status = (
            ExecuteCapability.Result.CANCELLED
            if arguments.expect == "cancelled"
            else ExecuteCapability.Result.SUCCEEDED
        )
        if wrapped.status != expected_ros_status or wrapped.result.status != expected_ump_status:
            raise RuntimeError(
                f"unexpected terminal status ROS={wrapped.status} UMP={wrapped.result.status}"
            )
        return {
            "assignment_id": arguments.assignment_id,
            "status": arguments.expect,
            "description": wrapped.result.description,
        }
    finally:
        client.destroy()
        node.destroy_node()


def main(args=None):
    arguments = parser().parse_args(args)
    rclpy.init()
    try:
        print(json.dumps(exercise(arguments), sort_keys=True))
    finally:
        rclpy.shutdown()
