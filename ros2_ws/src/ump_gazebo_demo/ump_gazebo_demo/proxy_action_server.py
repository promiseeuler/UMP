import json
import time

import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from ump_interfaces.action import ExecuteCapability


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
        if request.capability != self._capability:
            return GoalResponse.REJECT
        try:
            inputs = json.loads(request.inputs_json)
        except json.JSONDecodeError:
            return GoalResponse.REJECT
        if not request.assignment_id or not isinstance(inputs, dict):
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
                return self._result(
                    ExecuteCapability.Result.SUCCEEDED,
                    "Proxy action reached its deterministic completion condition",
                )
            time.sleep(0.05)

    @staticmethod
    def _result(status, description):
        result = ExecuteCapability.Result()
        result.status = status
        result.description = description
        result.outputs_json = "{}"
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
