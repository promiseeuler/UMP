import json
import threading

import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.executors import MultiThreadedExecutor

from ump_interfaces.action import ExecuteCapability


class WarehouseRobotDriver:
    """Webots robot plugin exposing one high-level UMP capability over ROS 2."""

    def init(self, webots_node, properties):
        self._robot = webots_node.robot
        self._capability = properties["capability"]
        self._role = properties.get("role", "warehouse robot")
        self._package_def = properties.get("packageDef")
        self._left = self._robot.getDevice("left wheel motor")
        self._right = self._robot.getDevice("right wheel motor")
        for motor in (self._left, self._right):
            motor.setPosition(float("inf"))
            motor.setVelocity(0.0)
        self._arm = self._robot.getDevice("arm joint motor")
        self._gps = self._robot.getDevice("gps")
        self._gps.enable(int(self._robot.getBasicTimeStep()))
        self._active = None
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)

        if not rclpy.ok():
            rclpy.init(args=None)
        node_name = self._robot.getName().lower().replace("-", "_") + "_ump"
        self._node = rclpy.create_node(
            node_name, namespace=f"/{self._robot.getName()}"
        )
        self._server = ActionServer(
            self._node,
            ExecuteCapability,
            "execute_capability",
            execute_callback=self._execute,
            goal_callback=self._goal,
            cancel_callback=lambda _: CancelResponse.ACCEPT,
        )
        self._executor = MultiThreadedExecutor(num_threads=2)
        self._executor.add_node(self._node)
        threading.Thread(target=self._executor.spin, daemon=True).start()
        self._node.get_logger().info(
            f"UMP {self._role} ready: {self._capability}"
        )

    def _goal(self, request):
        if request.capability != self._capability or not request.assignment_id:
            return GoalResponse.REJECT
        try:
            inputs = json.loads(request.inputs_json)
        except (TypeError, json.JSONDecodeError):
            return GoalResponse.REJECT
        if not isinstance(inputs, dict):
            return GoalResponse.REJECT
        with self._lock:
            return GoalResponse.REJECT if self._active else GoalResponse.ACCEPT

    def _execute(self, goal_handle):
        duration = {
            "ump.navigation.inspect-route/v1": 6.0,
            "ump.material.carry/v1": 5.2,
            "ump.manipulation.place/v1": 6.0,
        }[self._capability]
        with self._condition:
            self._active = {
                "goal": goal_handle,
                "started": self._robot.getTime(),
                "duration": duration,
                "progress": 0.0,
                "cancelled": False,
                "done": False,
            }
            while not self._active["done"]:
                self._condition.wait(timeout=0.1)
                feedback = ExecuteCapability.Feedback()
                feedback.progress = self._active["progress"]
                feedback.summary = (
                    f"{self._role} executing {self._capability} "
                    f"at {feedback.progress:.0%}"
                )
                goal_handle.publish_feedback(feedback)
            cancelled = self._active["cancelled"]
            self._active = None

        if cancelled:
            goal_handle.canceled()
            return self._result(
                ExecuteCapability.Result.CANCELLED,
                "Webots robot stopped after ROS 2 action cancellation",
                {"completed": False},
            )
        goal_handle.succeed()
        return self._result(
            ExecuteCapability.Result.SUCCEEDED,
            f"Webots {self._role} reached its physical simulation endpoint",
            self._outputs(),
        )

    def step(self):
        with self._condition:
            active = self._active
            if active is None:
                return
            if active["goal"].is_cancel_requested:
                active["cancelled"] = True
                active["done"] = True
            else:
                elapsed = self._robot.getTime() - active["started"]
                active["progress"] = min(elapsed / active["duration"], 1.0)
                self._drive(active["progress"])
                self._move_package(active["progress"])
                if active["progress"] >= 1.0:
                    active["done"] = True
            if active["done"]:
                self._left.setVelocity(0.0)
                self._right.setVelocity(0.0)
            self._condition.notify_all()

    def _drive(self, progress):
        speed = 6.0 if progress < 0.9 else 2.0
        self._left.setVelocity(speed)
        self._right.setVelocity(speed)
        if self._arm is not None and self._capability == "ump.manipulation.place/v1":
            self._arm.setPosition(-0.65 if progress > 0.65 else 0.35)

    def _move_package(self, progress):
        if not self._package_def:
            return
        package = self._robot.getFromDef(self._package_def)
        if package is None:
            return
        field = package.getField("translation")
        if self._capability == "ump.material.carry/v1":
            x, _, z = self._gps.getValues()
            field.setSFVec3f([x + 0.55, 0.42, z])
        elif progress >= 1.0:
            field.setSFVec3f([3.6, 1.34, -1.8])

    def _outputs(self):
        if self._capability == "ump.navigation.inspect-route/v1":
            return {"completed": True, "traversable": True}
        if self._capability == "ump.material.carry/v1":
            return {"completed": True, "delivered": True, "final_location": "handoff"}
        return {"completed": True, "placed": True, "target": "shelf-a"}

    @staticmethod
    def _result(status, description, outputs):
        result = ExecuteCapability.Result()
        result.status = status
        result.description = description
        result.outputs_json = json.dumps(outputs, sort_keys=True, separators=(",", ":"))
        return result
