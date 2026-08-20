"""Minimal manufacturer adapter that publishes awareness without accepting work."""

from __future__ import annotations

import json
from pathlib import Path

from ump import (
    AdapterConformanceHarness,
    Assignment,
    AssignmentStatus,
    Mode,
    Outcome,
    RobotManifest,
    RobotState,
    Safety,
)


class ReadOnlyAdapter:
    def __init__(self) -> None:
        self._stale_required_peers: tuple[str, ...] = ()
        self._manifest = RobotManifest(
            robot_id="manufacturer-robot-1",
            manufacturer="Example Manufacturer",
            model="Awareness Bridge",
            robot_class="mobile_robot",
            capabilities=(),
            adapter_version="1.0.0",
        )

    def manifest(self) -> RobotManifest:
        return self._manifest

    def state(self) -> RobotState:
        # Replace these values with bounded semantic data from the native API.
        blocked = bool(self._stale_required_peers)
        return RobotState(
            robot_id=self._manifest.robot_id,
            mode=Mode.IDLE,
            safety=Safety.NORMAL,
            activity=(
                "Waiting for required peer communication"
                if blocked
                else "Waiting for native work"
            ),
            intent=(
                "Remain read-only until required peers recover"
                if blocked
                else "Publish semantic state only"
            ),
            progress=0.0,
            summary=(
                "The robot is read-only and required peer communication is stale."
                if blocked
                else "The robot is idle and is not accepting UMP assignments."
            ),
            blockers=tuple(
                f"required_peer_stale:{peer_id}"
                for peer_id in self._stale_required_peers
            ),
        )

    def accept(self, assignment: Assignment) -> Outcome:
        return Outcome(
            assignment.assignment_id,
            self._manifest.robot_id,
            False,
            "Read-only adapter rejects all UMP assignments",
            status=AssignmentStatus.REJECTED,
        )

    def cancel(self, assignment_id: str, reason: str) -> tuple[bool, str]:
        del assignment_id, reason
        return False, "Read-only adapter has no native UMP work to cancel"

    def communication_lost(
        self, stale_peer_ids: tuple[str, ...], observed_at_ms: int
    ) -> None:
        del observed_at_ms
        self._stale_required_peers = stale_peer_ids

    def communication_restored(
        self, restored_peer_ids: tuple[str, ...], observed_at_ms: int
    ) -> None:
        del restored_peer_ids, observed_at_ms
        self._stale_required_peers = ()


def create_adapter(config_path: Path | None = None) -> ReadOnlyAdapter:
    """Factory used by ump-node; this example has no native configuration."""
    del config_path
    return ReadOnlyAdapter()


def main() -> None:
    report = AdapterConformanceHarness().inspect(ReadOnlyAdapter())
    print(
        json.dumps(
            {
                "subject": report.subject,
                "passed": report.passed,
                "checks": [
                    {
                        "id": check.check_id,
                        "passed": check.passed,
                        "description": check.description,
                    }
                    for check in report.checks
                ],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
