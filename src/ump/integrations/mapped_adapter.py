from __future__ import annotations

from collections.abc import Callable, Mapping
from threading import RLock
from typing import Any

from ..models import Assignment, Outcome, PlanStep, RobotManifest, RobotState
from .base import (
    ExternalTaskMapping,
    MappingReport,
    TaskAuthorizer,
    require_task_authorization,
)


class MappedStandardsAdapter:
    """Stateful base for explicit, loss-aware standards mappings."""

    standard = ""
    standard_version = ""

    def __init__(
        self,
        manifest: RobotManifest,
        state: RobotState,
        *,
        external_id: str,
        read_only: bool = True,
        task_mappings: tuple[ExternalTaskMapping, ...] = (),
        authorizer: TaskAuthorizer | None = None,
        native_executor: Callable[[Assignment], Outcome] | None = None,
        native_canceller: Callable[[str, str], tuple[bool, str]] | None = None,
    ) -> None:
        if manifest.robot_id != state.robot_id:
            raise ValueError("integration manifest and state identity differ")
        self._manifest = manifest
        self._state = state
        self.external_id = external_id
        self.read_only = read_only
        self._task_mappings = {item.external_type: item for item in task_mappings}
        if len(self._task_mappings) != len(task_mappings):
            raise ValueError("external task mapping types must be unique")
        self._authorizer = authorizer
        self._native_executor = native_executor
        self._native_canceller = native_canceller
        self._lock = RLock()
        self.last_report: MappingReport | None = None

    def manifest(self) -> RobotManifest:
        with self._lock:
            return self._manifest

    def state(self) -> RobotState:
        with self._lock:
            return self._state

    def _store_manifest(self, value: RobotManifest, report: MappingReport) -> None:
        if value.robot_id != self._manifest.robot_id:
            raise ValueError("external identity cannot replace configured UMP identity")
        with self._lock:
            self._manifest = value
            self.last_report = report

    def _store_state(self, value: RobotState, report: MappingReport) -> None:
        if value.robot_id != self._manifest.robot_id:
            raise ValueError("external state identity does not match configured robot")
        with self._lock:
            self._state = value
            self.last_report = report

    def accept(self, assignment: Assignment) -> Outcome:
        if self._native_executor is None:
            return Outcome(
                assignment.assignment_id,
                self._manifest.robot_id,
                False,
                "Integration has no configured native assignment executor",
            )
        return self._native_executor(assignment)

    def cancel(self, assignment_id: str, reason: str) -> tuple[bool, str]:
        if self._native_canceller is None:
            return False, "Integration has no configured native cancellation endpoint"
        return self._native_canceller(assignment_id, reason)

    def _assignment_from_external(
        self,
        document: Mapping[str, Any],
        *,
        external_type: str,
        issuer_id: str,
        lease_id: str,
        assignment_id: str,
        issued_at_ms: int,
    ) -> Assignment:
        mapping = self._task_mappings.get(external_type)
        if mapping is None:
            raise ValueError(f"external task type is not mapped: {external_type}")
        require_task_authorization(
            read_only=self.read_only,
            authorizer=self._authorizer,
            robot_id=self._manifest.robot_id,
            issuer_id=issuer_id,
            lease_id=lease_id,
            capability=mapping.capability,
        )
        if self._manifest.capability(mapping.capability) is None:
            raise ValueError("mapped capability is not advertised by the robot")
        step = PlanStep(
            step_id=f"external-{assignment_id}",
            description=f"Authorized {self.standard} task {external_type}",
            assigned_robot_id=self._manifest.robot_id,
            capability=mapping.capability,
            inputs=mapping.map_input(document),
            completion_criteria="Native endpoint reports a terminal result",
            not_before_ms=issued_at_ms,
        )
        return Assignment(
            assignment_id=assignment_id,
            goal_id=f"external-goal-{assignment_id}",
            plan_id=f"external-plan-{assignment_id}",
            step=step,
            authority_lease_id=lease_id,
        )
