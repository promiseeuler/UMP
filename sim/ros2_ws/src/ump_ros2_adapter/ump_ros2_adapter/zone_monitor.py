from __future__ import annotations

import asyncio

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from .local_api import LocalApiClient


class ZoneMonitorAdapter(Node):
    """Report physical zone intrusion to the local UMP authority."""

    def __init__(self) -> None:
        super().__init__("ump_zone_monitor")
        self.declare_parameter("local_socket", "/run/ump/zone/adapter.sock")
        self.declare_parameter("resource_id", "ump:resource:s4-transfer-zone")
        self._intrusion_active = False
        self.create_subscription(String, "/ump/zone/state", self._on_state, 10)

    def _on_state(self, message: String) -> None:
        if message.data == "clear":
            self._intrusion_active = False
            return
        if not message.data.startswith("intrusion:") or self._intrusion_active:
            return
        self._intrusion_active = True
        try:
            result = asyncio.run(
                LocalApiClient(str(self.get_parameter("local_socket").value)).call(
                    "resource_intrusion",
                    {
                        "resource_id": str(self.get_parameter("resource_id").value),
                        "reason": message.data,
                    },
                )
            )
            self.get_logger().warning(
                f"zone intrusion revoked {len(result.get('revoked_reservation_ids', []))} reservations"
            )
        except (ConnectionError, OSError, RuntimeError, asyncio.TimeoutError) as error:
            self._intrusion_active = False
            self.get_logger().error(f"zone intrusion report failed: {error}")


def main(args=None) -> None:
    rclpy.init(args=args)
    node = ZoneMonitorAdapter()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
