"""Stable manufacturer-facing adapter contract."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import Assignment, Outcome, RobotManifest, RobotState


@runtime_checkable
class RobotAdapter(Protocol):
    """Boundary between UMP coordination and manufacturer-native software."""

    def manifest(self) -> RobotManifest:
        """Return the robot identity and currently advertised capabilities."""
        ...

    def state(self) -> RobotState:
        """Return the latest bounded semantic state snapshot."""
        ...

    def accept(self, assignment: Assignment) -> Outcome:
        """Accept or reject one authorized high-level assignment."""
        ...

    def cancel(self, assignment_id: str, reason: str) -> tuple[bool, str]:
        """Ask native software to cancel; return its authoritative decision."""
        ...
