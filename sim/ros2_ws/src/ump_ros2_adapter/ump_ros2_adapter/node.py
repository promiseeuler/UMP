from __future__ import annotations

import asyncio
import time

import rclpy
from diagnostic_msgs.msg import DiagnosticArray
from rclpy.action import ActionClient
from rclpy.executors import MultiThreadedExecutor
from rclpy.lifecycle import LifecycleNode, State, TransitionCallbackReturn
from tf2_ros import Buffer, TransformException, TransformListener
from ump_interfaces.action import ExecuteCapability
from ump_interfaces.msg import (
    AdapterState,
    LocalizationUncertainty,
    SafetyEvent,
    SpatialSample,
)

from .local_api import LocalApiClient
from .mapping import (
    apply_safety_override,
    latch_safety_override,
    map_diagnostics,
    requires_safety_stop,
    validate_spatial_transform,
)


class UmpRos2Adapter(LifecycleNode):
    """Maps ROS observations and actions to the local UMP runtime boundary."""

    def __init__(self) -> None:
        super().__init__("ump_ros2_adapter")
        self.declare_parameter("machine_id", "")
        self.declare_parameter("local_socket", "/run/ump/adapter.sock")
        self.declare_parameter("parent_frame", "map")
        self.declare_parameter("child_frame", "base_link")
        self.declare_parameter("tf_period_seconds", 0.1)
        self.declare_parameter("tf_max_age_seconds", 0.5)
        self.declare_parameter("tf_max_translation_jump_m", 0.75)
        self.declare_parameter("tf_max_rotation_jump_rad", 1.2)
        self.declare_parameter("position_uncertainty_m", 0.02)
        self.declare_parameter("orientation_uncertainty_rad", 0.03)
        self.declare_parameter("maximum_position_uncertainty_m", 0.25)
        self.declare_parameter("maximum_orientation_uncertainty_rad", 0.35)
        self.declare_parameter("localization_uncertainty_max_age_seconds", 0.5)
        self.declare_parameter("localization_uncertainty_topic", "")
        self.declare_parameter("poll_period_seconds", 0.1)
        self.declare_parameter("controller_action", "~/controller/execute_capability")
        self._revision = 1
        self._sequence = 0
        self._diagnostics = []
        self._state_pub = None
        self._spatial_pub = None
        self._diag_sub = None
        self._safety_sub = None
        self._localization_sub = None
        self._timer = None
        self._action_client = None
        self._busy = False
        self._last_reported_state = None
        self._pending_feedback = None
        self._safety_override = None
        self._active_goal_handle = None
        self._last_transform = None
        self._position_uncertainty_m = float(
            self.get_parameter("position_uncertainty_m").value
        )
        self._orientation_uncertainty_rad = float(
            self.get_parameter("orientation_uncertainty_rad").value
        )
        self._uncertainty_stamp_ns = 0
        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, self)

    def on_configure(self, state: State) -> TransitionCallbackReturn:
        del state
        if not self.get_parameter("machine_id").value:
            self.get_logger().error("machine_id must be configured")
            return TransitionCallbackReturn.FAILURE
        self._state_pub = self.create_lifecycle_publisher(AdapterState, "~/state", 10)
        self._spatial_pub = self.create_lifecycle_publisher(SpatialSample, "~/spatial", 10)
        self._diag_sub = self.create_subscription(
            DiagnosticArray, "/diagnostics", self._on_diagnostics, 10
        )
        self._safety_sub = self.create_subscription(
            SafetyEvent, "~/safety", self._on_safety, 10
        )
        localization_topic = str(
            self.get_parameter("localization_uncertainty_topic").value
        )
        if localization_topic:
            self._localization_sub = self.create_subscription(
                LocalizationUncertainty,
                localization_topic,
                self._on_localization_uncertainty,
                10,
            )
        self._action_client = ActionClient(
            self,
            ExecuteCapability,
            str(self.get_parameter("controller_action").value),
        )
        return TransitionCallbackReturn.SUCCESS

    def on_activate(self, state: State) -> TransitionCallbackReturn:
        result = super().on_activate(state)
        self._timer = self.create_timer(
            float(self.get_parameter("poll_period_seconds").value), self._poll
        )
        return result

    def on_deactivate(self, state: State) -> TransitionCallbackReturn:
        if self._timer is not None:
            self.destroy_timer(self._timer)
            self._timer = None
        return super().on_deactivate(state)

    def _on_diagnostics(self, message: DiagnosticArray) -> None:
        self._diagnostics = [
            (status.level, status.hardware_id, status.message) for status in message.status
        ]

    def _on_safety(self, message: SafetyEvent) -> None:
        self._safety_override = latch_safety_override(
            self._safety_override, message.state
        )
        if requires_safety_stop(message.state) and self._active_goal_handle is not None:
            self._active_goal_handle.cancel_goal_async()

    def _on_localization_uncertainty(self, message: LocalizationUncertainty) -> None:
        self._position_uncertainty_m = message.position_uncertainty_m
        self._orientation_uncertainty_rad = message.orientation_uncertainty_rad
        self._uncertainty_stamp_ns = (
            message.source_time.sec * 1_000_000_000 + message.source_time.nanosec
        )

    async def _poll(self) -> None:
        if self._busy:
            return
        self._busy = True
        client = LocalApiClient(str(self.get_parameter("local_socket").value))
        try:
            await self._publish_observations(client)
            response = await client.call("next_task", {})
            task = response.get("task")
            if task is not None:
                await self._execute_task(client, task)
        except (ConnectionError, OSError, RuntimeError, asyncio.TimeoutError) as error:
            self.get_logger().warning(f"UMP local API unavailable: {error}")
        finally:
            self._busy = False

    async def _execute_task(self, client: LocalApiClient, task: dict) -> None:
        if not self._action_client.wait_for_server(timeout_sec=1.0):
            await client.call(
                "complete",
                {
                    "task_id": task["task_id"],
                    "outcome": "failed",
                    "error_code": "ros.controller_unavailable",
                    "retryable": True,
                    "retry_after_ms": 500,
                },
            )
            return
        goal = ExecuteCapability.Goal()
        goal.task_id = task["task_id"]
        goal.issuer_machine_id = task["issuer_machine_id"]
        goal.capability = task["capability"]
        goal.input = task.get("input", [])
        goal.input_content_type = task.get("input_content_type", "")
        goal.attempt = task["attempt"]
        deadline_ms = task["deadline_ms"]
        goal.deadline.sec = deadline_ms // 1000
        goal.deadline.nanosec = (deadline_ms % 1000) * 1_000_000
        goal.correlation_id = task.get("correlation_id", "")
        goal_handle = await self._action_client.send_goal_async(
            goal, feedback_callback=self._on_action_feedback
        )
        if not goal_handle.accepted:
            await client.call(
                "complete",
                {
                    "task_id": task["task_id"],
                    "outcome": "failed",
                    "error_code": "ros.controller_rejected",
                },
            )
            return
        self._active_goal_handle = goal_handle
        result_future = goal_handle.get_result_async()
        while not result_future.done():
            feedback = self._pending_feedback
            self._pending_feedback = None
            if feedback is not None:
                await client.call(
                    "progress",
                    {
                        "task_id": task["task_id"],
                        "progress_per_mille": min(1000, feedback.progress_percent * 10),
                        "stage": feedback.stage or "executing",
                    },
                )
            status = await client.call("task_status", {"task_id": task["task_id"]})
            mapped = map_diagnostics(self._diagnostics)
            safety = self._safety_override or mapped.safety
            if status.get("state") in {
                "cancel_pending", "cancelled", "unknown", "failed", "rejected"
            } or requires_safety_stop(safety):
                await goal_handle.cancel_goal_async()
            await asyncio.sleep(0.1)
        wrapped = result_future.result()
        self._active_goal_handle = None
        result = wrapped.result
        outcome = "succeeded" if result.succeeded else "failed"
        if result.code == "ros.cancelled":
            outcome = "cancelled"
        await client.call(
            "complete",
            {
                "task_id": task["task_id"],
                "outcome": outcome,
                "output": list(result.output),
                "error_code": result.code,
                "retryable": result.retryable,
                "retry_after_ms": result.retry_after.sec * 1000
                + result.retry_after.nanosec // 1_000_000,
            },
        )

    def _on_action_feedback(self, message) -> None:
        self._pending_feedback = message.feedback

    async def _publish_observations(self, client: LocalApiClient) -> None:
        now = self.get_clock().now().to_msg()
        mapped = map_diagnostics(self._diagnostics)
        if self._safety_override is not None:
            mapped = apply_safety_override(mapped, self._safety_override)
        self._revision += 1
        state = AdapterState()
        state.source_time = now
        state.revision = self._revision
        state.operational = mapped.operational
        state.safety = mapped.safety
        state.health_codes = list(mapped.health_codes)
        self._state_pub.publish(state)
        state_key = (mapped.operational, mapped.safety, mapped.health_codes)
        if state_key != self._last_reported_state:
            operational_names = {
                1: "starting", 2: "idle", 3: "busy", 4: "paused",
                5: "degraded", 6: "stopping", 7: "faulted",
            }
            safety_names = {
                1: "normal", 2: "protective_stop", 3: "emergency_stop",
                4: "recovery_required", 5: "unknown",
            }
            await client.call(
                "state_update",
                {
                    "operational": operational_names[mapped.operational],
                    "safety": safety_names[mapped.safety],
                    "revision": self._revision,
                    "source_time_ms": time.time_ns() // 1_000_000,
                    "health_codes": list(mapped.health_codes),
                },
            )
            self._last_reported_state = state_key

        sample = SpatialSample()
        sample.source_time = now
        self._sequence += 1
        sample.sequence = self._sequence
        sample.parent_frame = str(self.get_parameter("parent_frame").value)
        sample.child_frame = str(self.get_parameter("child_frame").value)
        try:
            now_ns = self.get_clock().now().nanoseconds
            stamped = self._tf_buffer.lookup_transform(
                sample.parent_frame, sample.child_frame, rclpy.time.Time()
            )
            transform = stamped.transform
            translation = (
                transform.translation.x,
                transform.translation.y,
                transform.translation.z,
            )
            rotation = (
                transform.rotation.x,
                transform.rotation.y,
                transform.rotation.z,
                transform.rotation.w,
            )
            previous_translation = self._last_transform[0] if self._last_transform else None
            previous_rotation = self._last_transform[1] if self._last_transform else None
            stamp_ns = stamped.header.stamp.sec * 1_000_000_000 + stamped.header.stamp.nanosec
            invalid_reason = validate_spatial_transform(
                now_ns,
                stamp_ns,
                translation,
                rotation,
                previous_translation,
                previous_rotation,
                int(float(self.get_parameter("tf_max_age_seconds").value) * 1_000_000_000),
                float(self.get_parameter("tf_max_translation_jump_m").value),
                float(self.get_parameter("tf_max_rotation_jump_rad").value),
                self._position_uncertainty_m,
                self._orientation_uncertainty_rad,
                float(self.get_parameter("maximum_position_uncertainty_m").value),
                float(self.get_parameter("maximum_orientation_uncertainty_rad").value),
                (
                    now_ns
                    if self._localization_sub is None
                    else self._uncertainty_stamp_ns or None
                ),
                int(
                    float(
                        self.get_parameter(
                            "localization_uncertainty_max_age_seconds"
                        ).value
                    )
                    * 1_000_000_000
                ),
            )
            sample.translation_x = transform.translation.x
            sample.translation_y = transform.translation.y
            sample.translation_z = transform.translation.z
            sample.rotation_x = transform.rotation.x
            sample.rotation_y = transform.rotation.y
            sample.rotation_z = transform.rotation.z
            sample.rotation_w = transform.rotation.w
            sample.position_uncertainty_m = self._position_uncertainty_m
            sample.orientation_uncertainty_rad = self._orientation_uncertainty_rad
            sample.source_time = stamped.header.stamp
            sample.valid = not invalid_reason
            sample.invalid_reason = invalid_reason
            if invalid_reason not in {"tf.non_finite", "tf.invalid_quaternion"}:
                self._last_transform = (translation, rotation)
        except TransformException as error:
            sample.valid = False
            sample.invalid_reason = str(error)
        self._spatial_pub.publish(sample)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = UmpRos2Adapter()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    finally:
        executor.shutdown()
        node.destroy_node()
        rclpy.shutdown()
