import json
import time

import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from ump_interfaces.action import ExecuteCapability


def valid_goal_request(
    expected_capability, assignment_id, capability, inputs_json
):
    if capability != expected_capability or not assignment_id:
        return False
    try:
        inputs = json.loads(inputs_json)
    except (TypeError, json.JSONDecodeError):
        return False
    return isinstance(inputs, dict)


def successful_outputs(capability, inputs):
    """Return deterministic coordination-level outputs for the demo capabilities."""
    if capability == "ump.navigation.inspect-route/v1":
        return {
            "completed": True,
            "traversable": True,
            "summary": "The requested route is traversable",
        }
    if capability == "ump.material.carry/v1":
        return {
            "completed": True,
            "delivered": True,
            "final_location": inputs.get("destination", "unspecified"),
        }
    if capability == "ump.manipulation.place/v1":
        return {
            "completed": True,
            "placed": True,
            "target": inputs.get("target", "unspecified"),
        }
    return {"completed": True}


class ProxyCapabilityServer(Node):
    """Cancellable lifecycle fixture; it does not model physical capability."""

    def __init__(self) -> None:
        super().__init__("ump_proxy_capability_server")
        self.declare_parameter("action_name", "execute_capability")
        self.declare_parameter("capability", "")
        self.declare_parameter("duration_s", 2.0)
        self._capability = self.get_parameter("capability").value
        self._duration_s = float(self.get_parameter("duration_s").value)
        if not self._capability or self._duration_s <= 0:
            raise ValueError("proxy server requires capability and positive duration")
        self._server = ActionServer(
            self,
            ExecuteCapability,
            self.get_parameter("action_name").value,
            execute_callback=self.execute,
            goal_callback=self.goal,
            cancel_callback=self.cancel,
        )

    def goal(self, request):
        if not valid_goal_request(
            self._capability,
            request.assignment_id,
            request.capability,
            request.inputs_json,
        ):
            return GoalResponse.REJECT
        return GoalResponse.ACCEPT

    def cancel(self, goal_handle):
        del goal_handle
        return CancelResponse.ACCEPT

    def execute(self, goal_handle):
        started = time.monotonic()
        feedback = ExecuteCapability.Feedback()
        while True:
            if goal_handle.is_cancel_requested:
                goal_handle.canceled()
                return self._result(
                    ExecuteCapability.Result.CANCELLED,
                    "Proxy action cancelled by its native ROS server",
                )
            elapsed = time.monotonic() - started
            progress = min(elapsed / self._duration_s, 1.0)
            feedback.progress = progress
            feedback.summary = f"Executing {self._capability} at {progress:.0%}"
            goal_handle.publish_feedback(feedback)
            if progress >= 1.0:
                goal_handle.succeed()
                inputs = json.loads(goal_handle.request.inputs_json)
                return self._result(
                    ExecuteCapability.Result.SUCCEEDED,
                    "Proxy action reached its deterministic completion condition",
                    successful_outputs(self._capability, inputs),
                )
            time.sleep(0.05)

    @staticmethod
    def _result(status, description, outputs=None):
        result = ExecuteCapability.Result()
        result.status = status
        result.description = description
        result.outputs_json = json.dumps(
            outputs or {}, sort_keys=True, separators=(",", ":")
        )
        return result

    def destroy_node(self):
        self._server.destroy()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = ProxyCapabilityServer()
    executor = MultiThreadedExecutor(num_threads=2)
    executor.add_node(node)
    try:
        executor.spin()
    finally:
        executor.shutdown()
        node.destroy_node()
        rclpy.shutdown()
