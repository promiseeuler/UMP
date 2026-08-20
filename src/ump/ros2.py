from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from threading import Event, RLock
import json
from typing import Any, Protocol

from .models import (
    Assignment,
    AssignmentStatus,
    Outcome,
    RobotManifest,
    RobotState,
)


class Ros2UnavailableError(RuntimeError):
    pass


class NativeActionStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class NativeActionResult:
    status: NativeActionStatus
    description: str
    outputs: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValueError("native action result requires a description")
        if self.outputs is not None and not isinstance(self.outputs, dict):
            raise ValueError("native action result outputs must be an object")


GoalBuilder = Callable[[Assignment, Any], Any]
ResultReader = Callable[[Any, int], NativeActionResult]


@dataclass(frozen=True)
class RosActionBinding:
    """Maps one advertised UMP capability to one vendor-owned ROS action."""

    capability: str
    action_name: str
    action_type: Any
    goal_builder: GoalBuilder
    result_reader: ResultReader
    server_timeout_s: float = 5.0
    result_timeout_s: float | None = None

    def __post_init__(self) -> None:
        if not self.capability or not self.action_name:
            raise ValueError("ROS action binding requires capability and action name")
        if self.server_timeout_s <= 0:
            raise ValueError("ROS action server timeout must be positive")
        if self.result_timeout_s is not None and self.result_timeout_s <= 0:
            raise ValueError("ROS action result timeout must be positive")


class ActionBackend(Protocol):
    def execute(
        self, binding: RosActionBinding, assignment: Assignment
    ) -> NativeActionResult: ...

    def cancel(self, assignment_id: str, reason: str) -> tuple[bool, str]: ...


class SemanticStateStore:
    """Thread-safe semantic state written by manufacturer ROS callbacks."""

    def __init__(self, initial: RobotState) -> None:
        self._state = initial
        self._lock = RLock()

    def update(self, state: RobotState) -> None:
        with self._lock:
            if state.robot_id != self._state.robot_id:
                raise ValueError("semantic state robot identity cannot change")
            self._state = state

    def read(self) -> RobotState:
        with self._lock:
            return self._state


class Ros2RobotAdapter:
    """UMP adapter over capability-specific ROS 2 action bindings.

    The adapter never constructs trajectories. Manufacturer code supplies each
    goal/result codec and updates the semantic state store from trusted ROS data.
    """

    def __init__(
        self,
        manifest: RobotManifest,
        state_store: SemanticStateStore,
        bindings: Mapping[str, RosActionBinding],
        backend: ActionBackend,
    ) -> None:
        if state_store.read().robot_id != manifest.robot_id:
            raise ValueError("manifest and semantic state robot IDs must match")
        advertised = {capability.name for capability in manifest.capabilities}
        unknown = set(bindings) - advertised
        mismatched = {
            key for key, binding in bindings.items() if key != binding.capability
        }
        if unknown or mismatched:
            raise ValueError("ROS action bindings must match advertised capabilities")
        self._manifest = manifest
        self._state_store = state_store
        self._bindings = dict(bindings)
        self._backend = backend

    def manifest(self) -> RobotManifest:
        return self._manifest

    def state(self) -> RobotState:
        return self._state_store.read()

    def accept(self, assignment: Assignment) -> Outcome:
        binding = self._bindings.get(assignment.step.capability)
        if binding is None:
            return Outcome(
                assignment.assignment_id,
                self._manifest.robot_id,
                False,
                "Native ROS adapter has no action binding for this capability",
                AssignmentStatus.REJECTED,
            )
        result = self._backend.execute(binding, assignment)
        status = AssignmentStatus(result.status.value)
        return Outcome(
            assignment.assignment_id,
            self._manifest.robot_id,
            status is AssignmentStatus.SUCCEEDED,
            result.description,
            status,
            result.outputs or {},
        )

    def cancel(self, assignment_id: str, reason: str) -> tuple[bool, str]:
        return self._backend.cancel(assignment_id, reason)


class RclpyActionBackend:
    """Blocking UMP adapter facade over asynchronous rclpy action clients.

    The supplied ROS node must be spinning in another executor thread. This
    class deliberately imports rclpy lazily so the UMP core remains ROS-free.
    """

    def __init__(self, node: Any) -> None:
        try:
            from rclpy.action import ActionClient
        except ImportError as error:
            raise Ros2UnavailableError(
                "rclpy is required to instantiate RclpyActionBackend"
            ) from error
        self._node = node
        self._action_client_type = ActionClient
        self._clients: dict[tuple[Any, str], Any] = {}
        self._active_goals: dict[str, Any] = {}
        self._lock = RLock()

    @staticmethod
    def _wait(future: Any, timeout_s: float | None) -> Any:
        completed = Event()
        future.add_done_callback(lambda _: completed.set())
        if not completed.wait(timeout_s):
            raise TimeoutError("ROS action operation timed out")
        return future.result()

    def _client(self, binding: RosActionBinding) -> Any:
        key = (binding.action_type, binding.action_name)
        with self._lock:
            client = self._clients.get(key)
            if client is None:
                client = self._action_client_type(
                    self._node, binding.action_type, binding.action_name
                )
                self._clients[key] = client
            return client

    def execute(
        self, binding: RosActionBinding, assignment: Assignment
    ) -> NativeActionResult:
        client = self._client(binding)
        if not client.wait_for_server(timeout_sec=binding.server_timeout_s):
            return NativeActionResult(
                NativeActionStatus.REJECTED,
                f"ROS action server unavailable: {binding.action_name}",
            )
        goal = binding.goal_builder(assignment, binding.action_type)
        goal_handle = self._wait(
            client.send_goal_async(goal), binding.server_timeout_s
        )
        if goal_handle is None or not goal_handle.accepted:
            return NativeActionResult(
                NativeActionStatus.REJECTED,
                f"ROS action server rejected goal: {binding.action_name}",
            )
        with self._lock:
            self._active_goals[assignment.assignment_id] = goal_handle
        try:
            wrapped_result = self._wait(
                goal_handle.get_result_async(), binding.result_timeout_s
            )
            return binding.result_reader(wrapped_result.result, wrapped_result.status)
        except TimeoutError:
            try:
                self._wait(goal_handle.cancel_goal_async(), binding.server_timeout_s)
            except Exception:
                pass
            return NativeActionResult(
                NativeActionStatus.UNKNOWN,
                f"ROS action result timed out and execution state is unknown: {binding.action_name}",
            )
        except Exception as error:
            return NativeActionResult(
                NativeActionStatus.UNKNOWN,
                f"ROS action result could not be interpreted: {type(error).__name__}",
            )
        finally:
            with self._lock:
                self._active_goals.pop(assignment.assignment_id, None)

    def cancel(self, assignment_id: str, reason: str) -> tuple[bool, str]:
        with self._lock:
            goal_handle = self._active_goals.get(assignment_id)
        if goal_handle is None:
            return False, "ROS action goal is not active"
        try:
            response = self._wait(goal_handle.cancel_goal_async(), 5.0)
        except TimeoutError:
            return False, "ROS action cancellation timed out"
        accepted = bool(getattr(response, "goals_canceling", ()))
        if accepted:
            return True, f"ROS action server accepted cancellation: {reason}"
        return False, "ROS action server rejected cancellation"

    def close(self) -> None:
        with self._lock:
            clients = tuple(self._clients.values())
            self._clients.clear()
        for client in clients:
            client.destroy()


def json_capability_binding(
    capability: str,
    action_name: str,
    action_type: Any,
    *,
    server_timeout_s: float = 5.0,
    result_timeout_s: float | None = None,
) -> RosActionBinding:
    """Create a binding for `ump_interfaces/action/ExecuteCapability`."""

    def build_goal(assignment: Assignment, interface: Any) -> Any:
        goal = interface.Goal()
        goal.assignment_id = assignment.assignment_id
        goal.capability = assignment.step.capability
        goal.inputs_json = json.dumps(
            assignment.step.inputs,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return goal

    def read_result(result: Any, ros_status: int) -> NativeActionResult:
        del ros_status
        statuses = {
            result.SUCCEEDED: NativeActionStatus.SUCCEEDED,
            result.FAILED: NativeActionStatus.FAILED,
            result.REJECTED: NativeActionStatus.REJECTED,
            result.CANCELLED: NativeActionStatus.CANCELLED,
            result.UNKNOWN: NativeActionStatus.UNKNOWN,
        }
        try:
            status = statuses[result.status]
        except (AttributeError, KeyError) as error:
            raise ValueError("ExecuteCapability returned an invalid status") from error
        try:
            outputs = json.loads(getattr(result, "outputs_json", "{}"))
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("ExecuteCapability returned invalid outputs JSON") from error
        if not isinstance(outputs, dict):
            raise ValueError("ExecuteCapability outputs JSON must be an object")
        return NativeActionResult(status, result.description, outputs)

    return RosActionBinding(
        capability,
        action_name,
        action_type,
        build_goal,
        read_result,
        server_timeout_s,
        result_timeout_s,
    )
