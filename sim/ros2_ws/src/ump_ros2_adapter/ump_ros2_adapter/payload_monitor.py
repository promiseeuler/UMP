from __future__ import annotations

import asyncio

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from .local_api import LocalApiClient


class PayloadMonitorAdapter(Node):
    """Report physical payload loss to the handoff authority."""

    def __init__(self) -> None:
        super().__init__("ump_payload_monitor")
        self.declare_parameter("local_socket", "/run/ump/zone/adapter.sock")
        self.declare_parameter("subject_id", "ump:subject:s4-package")
        self._drop_reported = False
        self.create_subscription(String, "/ump/payload/state", self._on_state, 10)

    def _on_state(self, message: String) -> None:
        if message.data != "dropped":
            self._drop_reported = False
            return
        if self._drop_reported:
            return
        self._drop_reported = True
        try:
            result = asyncio.run(
                LocalApiClient(str(self.get_parameter("local_socket").value)).call(
                    "handoff_subject_fault",
                    {
                        "subject_id": str(self.get_parameter("subject_id").value),
                        "reason": "payload_dropped",
                    },
                )
            )
            self.get_logger().error(
                "payload drop affected "
                f"{len(result.get('affected_handoffs', []))} handoffs"
            )
        except (ConnectionError, OSError, RuntimeError, asyncio.TimeoutError) as error:
            self._drop_reported = False
            self.get_logger().error(f"payload drop report failed: {error}")


def main(args=None) -> None:
    rclpy.init(args=args)
    node = PayloadMonitorAdapter()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
