from __future__ import annotations

from dataclasses import replace

from .models import (
    Assignment,
    Capability,
    Mode,
    Outcome,
    RobotManifest,
    RobotState,
    Safety,
)


class SimulatedRobot:
    """A native-controller stand-in used only by the deterministic demo."""

    def __init__(self, manifest: RobotManifest) -> None:
        self._manifest = manifest
        self._state = RobotState(
            robot_id=manifest.robot_id,
            mode=Mode.IDLE,
            safety=Safety.NORMAL,
            activity="Waiting for work",
            intent="Observe peer state and await an assignment",
            progress=0.0,
            summary=f"{manifest.robot_class} {manifest.robot_id} is idle and available.",
        )

    def manifest(self) -> RobotManifest:
        return self._manifest

    def state(self) -> RobotState:
        return self._state

    def accept(self, assignment: Assignment) -> Outcome:
        capability = self._manifest.capability(assignment.step.capability)
        if not capability:
            return Outcome(
                assignment.assignment_id,
                self._manifest.robot_id,
                False,
                "Native controller rejected an unsupported capability",
            )
        self._state = replace(
            self._state,
            mode=Mode.IDLE,
            activity=f"Completed: {assignment.step.description}",
            intent="Publish completion and await dependent work",
            progress=1.0,
            assignment_id=assignment.assignment_id,
            summary=(
                f"{self._manifest.robot_class} {self._manifest.robot_id} completed "
                f"{assignment.step.description.lower()} and is available."
            ),
        )
        return Outcome(
            assignment.assignment_id,
            self._manifest.robot_id,
            True,
            assignment.step.completion_criteria,
        )

    def cancel(self, assignment_id: str, reason: str) -> tuple[bool, str]:
        del assignment_id
        return False, f"Simulation completed synchronously before cancellation: {reason}"


def capability(name: str, description: str) -> Capability:
    return Capability(
        name=name,
        description=description,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
