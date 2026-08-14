from __future__ import annotations

import asyncio
import json
import math
import time

import rclpy
from geometry_msgs.msg import TransformStamped, Twist
from nav_msgs.msg import Odometry
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import Bool, Float64, String
from tf2_ros import TransformBroadcaster
from ump_interfaces.action import ExecuteCapability
from ump_interfaces.msg import LocalizationUncertainty

from .control import ProgressWatchdog, compute_drive, parse_arm, parse_navigation
from .mapping import deadline_expired


NAVIGATE = "org.ump.navigation.navigate"
ARM_POSE = "org.ump.manipulation.arm_pose"
PAYLOAD_ATTACH = "org.ump.payload.attach"
PAYLOAD_DETACH = "org.ump.payload.detach"


class S4Controller(Node):
    """Robot-specific controller fixture used by the S4 Gazebo profile."""

    def __init__(self) -> None:
        super().__init__("s4_controller")
        self.declare_parameter("role", "mobile")
        self.declare_parameter("action_name", "~/execute_capability")
        self.declare_parameter("navigation_stall_seconds", 4.0)
        self.declare_parameter("navigation_minimum_progress_m", 0.03)
        self._pose = None
        self._payload_state = ""
        self._tf_fault = "normal"
        self._tf_broadcaster = TransformBroadcaster(self)
        self._cmd_vel = self.create_publisher(Twist, "/model/mobile_base/cmd_vel", 10)
        self._shoulder = self.create_publisher(Float64, "/model/robot_arm/shoulder", 10)
        self._elbow = self.create_publisher(Float64, "/model/robot_arm/elbow", 10)
        self._attach_arm = self.create_publisher(Bool, "/ump/payload/attach_arm", 10)
        self._attach_mobile = self.create_publisher(Bool, "/ump/payload/attach_mobile", 10)
        self._detach_payload = self.create_publisher(Bool, "/ump/payload/detach", 10)
        self._localization_uncertainty = self.create_publisher(
            LocalizationUncertainty, "/ump/localization/mobile_uncertainty", 10
        )
        self.create_subscription(
            Odometry, "/model/mobile_base/odometry", self._on_odometry, 10
        )
        self.create_subscription(String, "/ump/payload/state", self._on_payload_state, 10)
        self.create_subscription(String, "/ump/fault/mobile_tf", self._on_tf_fault, 10)
        self._server = ActionServer(
            self,
            ExecuteCapability,
            str(self.get_parameter("action_name").value),
            execute_callback=self._execute,
            goal_callback=self._goal,
            cancel_callback=lambda _: CancelResponse.ACCEPT,
        )

    def _on_odometry(self, message: Odometry) -> None:
        position = message.pose.pose.position
        orientation = message.pose.pose.orientation
        yaw = math.atan2(
            2.0 * (orientation.w * orientation.z + orientation.x * orientation.y),
            1.0 - 2.0 * (orientation.y**2 + orientation.z**2),
        )
        self._pose = (position.x, position.y, yaw)
        uncertainty = LocalizationUncertainty()
        uncertainty.source_time = message.header.stamp
        uncertainty.sequence = message.header.stamp.sec * 1_000_000_000 + message.header.stamp.nanosec
        uncertainty.position_uncertainty_m = 0.02
        uncertainty.orientation_uncertainty_rad = 0.03
        if self._tf_fault == "uncertain":
            uncertainty.position_uncertainty_m = 2.0
            uncertainty.orientation_uncertainty_rad = 0.8
        self._localization_uncertainty.publish(uncertainty)
        transform = TransformStamped()
        transform.header.stamp = message.header.stamp
        transform.header.frame_id = "world"
        transform.child_frame_id = "mobile_base/base_link"
        transform.transform.translation.x = position.x
        transform.transform.translation.y = position.y
        transform.transform.translation.z = position.z
        transform.transform.rotation = orientation
        if self._tf_fault == "stale":
            stamp_ns = max(
                0,
                transform.header.stamp.sec * 1_000_000_000
                + transform.header.stamp.nanosec
                - 2_000_000_000,
            )
            transform.header.stamp.sec = stamp_ns // 1_000_000_000
            transform.header.stamp.nanosec = stamp_ns % 1_000_000_000
        elif self._tf_fault == "jump":
            transform.transform.translation.x += 5.0
        self._tf_broadcaster.sendTransform(transform)

    def _on_tf_fault(self, message: String) -> None:
        self._tf_fault = (
            message.data
            if message.data in {"normal", "stale", "jump", "uncertain"}
            else "normal"
        )

    def _goal(self, request: ExecuteCapability.Goal) -> GoalResponse:
        role = str(self.get_parameter("role").value)
        allowed = (
            {NAVIGATE, PAYLOAD_ATTACH, PAYLOAD_DETACH}
            if role == "mobile"
            else {ARM_POSE, PAYLOAD_ATTACH, PAYLOAD_DETACH}
        )
        if request.capability not in allowed or not request.task_id:
            return GoalResponse.REJECT
        deadline_ms = request.deadline.sec * 1000 + request.deadline.nanosec // 1_000_000
        if deadline_ms and time.time_ns() // 1_000_000 >= deadline_ms:
            return GoalResponse.REJECT
        return GoalResponse.ACCEPT

    async def _execute(self, goal_handle):
        if goal_handle.request.capability == NAVIGATE:
            return await self._navigate(goal_handle)
        if goal_handle.request.capability == ARM_POSE:
            return await self._arm_pose(goal_handle)
        return await self._payload(goal_handle)

    def _on_payload_state(self, message: String) -> None:
        self._payload_state = message.data

    async def _navigate(self, goal_handle):
        result = ExecuteCapability.Result()
        try:
            navigation = parse_navigation(bytes(goal_handle.request.input))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            return self._failure(goal_handle, "ros.invalid_navigation_input", str(error))

        started = time.monotonic()
        watchdog = ProgressWatchdog(
            float(self.get_parameter("navigation_stall_seconds").value),
            float(self.get_parameter("navigation_minimum_progress_m").value),
        )
        while rclpy.ok():
            if self._deadline_expired(goal_handle):
                return self._failure(
                    goal_handle, "ros.deadline_exceeded", "navigation deadline expired"
                )
            if goal_handle.is_cancel_requested:
                self._stop_base()
                goal_handle.canceled()
                result.code = "ros.cancelled"
                return result
            if self._pose is None:
                if time.monotonic() - started > 3.0:
                    return self._failure(goal_handle, "ros.odometry_unavailable", "no odometry")
                await asyncio.sleep(0.05)
                continue
            x, y, yaw = self._pose
            drive = compute_drive(navigation, x, y, yaw)
            if drive.reached:
                self._stop_base()
                result.succeeded = True
                result.code = "ok"
                result.output = list(f'{{"x":{x},"y":{y}}}'.encode())
                result.output_content_type = "application/json"
                goal_handle.succeed()
                return result
            if watchdog.observe(drive.distance_m, time.monotonic()):
                return self._failure(
                    goal_handle,
                    "ros.navigation_blocked",
                    f"no progress; remaining_m={drive.distance_m:.3f}",
                )
            command_message = Twist()
            command_message.angular.z = drive.angular_z
            command_message.linear.x = drive.linear_x
            self._cmd_vel.publish(command_message)
            feedback = ExecuteCapability.Feedback()
            feedback.stage = "navigate"
            feedback.detail = f"remaining_m={drive.distance_m:.3f}"
            feedback.progress_percent = min(99, int(100 / (1 + drive.distance_m)))
            goal_handle.publish_feedback(feedback)
            await asyncio.sleep(0.05)
        return self._failure(goal_handle, "ros.shutdown", "ROS context stopped")

    async def _arm_pose(self, goal_handle):
        try:
            command = parse_arm(bytes(goal_handle.request.input))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            return self._failure(goal_handle, "ros.invalid_arm_input", str(error))
        self._shoulder.publish(Float64(data=command.shoulder_rad))
        self._elbow.publish(Float64(data=command.elbow_rad))
        steps = max(1, int(command.settle_seconds / 0.05))
        for step in range(steps):
            if self._deadline_expired(goal_handle):
                return self._failure(
                    goal_handle, "ros.deadline_exceeded", "arm deadline expired"
                )
            if goal_handle.is_cancel_requested:
                goal_handle.canceled()
                result = ExecuteCapability.Result()
                result.code = "ros.cancelled"
                return result
            feedback = ExecuteCapability.Feedback()
            feedback.stage = "arm_pose"
            feedback.progress_percent = int(100 * step / steps)
            goal_handle.publish_feedback(feedback)
            await asyncio.sleep(0.05)
        result = ExecuteCapability.Result()
        result.succeeded = True
        result.code = "ok"
        goal_handle.succeed()
        return result

    async def _payload(self, goal_handle):
        role = str(self.get_parameter("role").value)
        expected = "unowned"
        publisher = self._detach_payload
        if goal_handle.request.capability == PAYLOAD_ATTACH:
            expected = role
            publisher = self._attach_mobile if role == "mobile" else self._attach_arm
        self._payload_state = ""
        publisher.publish(Bool(data=True))
        for _ in range(40):
            if self._deadline_expired(goal_handle):
                return self._failure(
                    goal_handle, "ros.deadline_exceeded", "payload deadline expired"
                )
            if goal_handle.is_cancel_requested:
                goal_handle.canceled()
                result = ExecuteCapability.Result()
                result.code = "ros.cancelled"
                return result
            if self._payload_state == expected:
                result = ExecuteCapability.Result()
                result.succeeded = True
                result.code = "ok"
                result.output = list(self._payload_state.encode())
                result.output_content_type = "text/plain"
                goal_handle.succeed()
                return result
            if self._payload_state.startswith("rejected:"):
                return self._failure(
                    goal_handle, "ros.payload_" + self._payload_state, self._payload_state
                )
            await asyncio.sleep(0.05)
        return self._failure(goal_handle, "ros.payload_state_timeout", "no attachment state")

    def _stop_base(self) -> None:
        self._cmd_vel.publish(Twist())

    @staticmethod
    def _deadline_expired(goal_handle) -> bool:
        deadline = goal_handle.request.deadline
        return deadline_expired(time.time_ns(), deadline.sec, deadline.nanosec)

    def _failure(self, goal_handle, code: str, detail: str):
        self._stop_base()
        result = ExecuteCapability.Result()
        result.code = code
        result.detail = detail
        result.retryable = code in {
            "ros.navigation_blocked",
            "ros.odometry_unavailable",
            "ros.shutdown",
        }
        if code == "ros.navigation_blocked":
            result.retry_after.sec = 1
        goal_handle.abort()
        return result


def main(args=None) -> None:
    rclpy.init(args=args)
    node = S4Controller()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    finally:
        executor.shutdown()
        node.destroy_node()
        rclpy.shutdown()
