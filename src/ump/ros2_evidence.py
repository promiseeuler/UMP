"""Validation for native ROS 2 simulator smoke reports."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any


class Ros2EvidenceValidationError(ValueError):
    pass


EXPECTED_RESULTS = {
    "smoke-inspect": ("succeeded", None),
    "smoke-adapter-carry": ("succeeded", "robot-humanoid-1"),
    "smoke-carry": ("succeeded", None),
    "smoke-place": ("cancelled", None),
    "smoke-reject": ("rejected", None),
}
EXPECTED_CHECKS = frozenset(
    {
        "gazebo_world_ready",
        "three_action_servers_ready",
        "native_action_success",
        "ump_adapter_success",
        "native_cancellation",
        "capability_rejection",
    }
)
WEBOTS_RESULTS = {
    "webots-inspect-route": ("succeeded", "robot-quadruped-1"),
    "webots-carry-package": ("succeeded", "robot-humanoid-1"),
    "webots-place-package": ("succeeded", "robot-mobile-arm-1"),
}
WEBOTS_CHECKS = frozenset(
    {
        "three_webots_drivers_ready",
        "ump_planner_exercised",
        "ump_ros2_adapters_exercised",
        "ordered_collaboration_succeeded",
        "physical_locomotion_exercised",
        "package_handoff_visualized",
    }
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise Ros2EvidenceValidationError(message)


def _load(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise Ros2EvidenceValidationError(
            f"ROS 2 smoke report cannot be read: {error}"
        ) from error
    if not isinstance(document, dict):
        raise Ros2EvidenceValidationError("ROS 2 smoke report must be a JSON object")
    return document


def validate_ros2_smoke_report(
    report_path: str | Path,
    *,
    world_path: str | Path | None = None,
    expected_revision: str | None = None,
) -> dict[str, Any]:
    """Validate native smoke outcomes and bind them to optional source evidence."""
    document = _load(Path(report_path))
    profile = document.get("profile")
    _require(
        profile in {"ump.ros2-gazebo-smoke/v1", "ump.ros2-webots-warehouse/v1"},
        "unsupported ROS 2 smoke profile",
    )
    is_webots = profile == "ump.ros2-webots-warehouse/v1"
    expected_checks = WEBOTS_CHECKS if is_webots else EXPECTED_CHECKS
    expected_results = WEBOTS_RESULTS if is_webots else EXPECTED_RESULTS
    _require(document.get("passed") is True, "ROS 2 smoke report did not pass")
    checks = document.get("checks")
    _require(isinstance(checks, dict), "ROS 2 smoke checks must be an object")
    _require(set(checks) == expected_checks, "ROS 2 smoke check set is incomplete")
    _require(
        all(value is True for value in checks.values()),
        "ROS 2 smoke checks did not all pass",
    )

    results = document.get("results")
    _require(isinstance(results, list), "ROS 2 smoke results must be an array")
    indexed: dict[str, dict[str, Any]] = {}
    for result in results:
        _require(isinstance(result, dict), "ROS 2 smoke result must be an object")
        assignment_id = result.get("assignment_id")
        _require(
            isinstance(assignment_id, str) and assignment_id not in indexed,
            "ROS 2 smoke assignment IDs must be unique strings",
        )
        indexed[assignment_id] = result
    _require(set(indexed) == set(expected_results), "ROS 2 smoke result set is invalid")
    for assignment_id, (status, robot_id) in expected_results.items():
        result = indexed[assignment_id]
        _require(
            result.get("status") == status,
            f"{assignment_id} has an unexpected status",
        )
        if robot_id is not None:
            _require(
                result.get("robot_id") == robot_id,
                f"{assignment_id} has an unexpected robot identity",
            )
        if status == "succeeded":
            outputs = result.get("outputs")
            _require(
                isinstance(outputs, dict) and outputs.get("completed") is True,
                f"{assignment_id} lacks structured completion output",
            )

    environment = document.get("environment")
    _require(isinstance(environment, dict), "ROS 2 smoke environment is missing")
    repository_revision = environment.get("repository_revision")
    _require(
        isinstance(repository_revision, str)
        and re.fullmatch(r"[0-9a-f]{40}", repository_revision) is not None,
        "ROS 2 smoke report requires a full lowercase repository revision",
    )
    _require(
        environment.get("ros_distro") == "jazzy",
        "ROS 2 smoke evidence must use the supported Jazzy distribution",
    )
    if expected_revision is not None:
        _require(
            repository_revision == expected_revision,
            "ROS 2 smoke report revision does not match",
        )

    world = document.get("world")
    _require(isinstance(world, dict), "ROS 2 smoke world evidence is missing")
    if is_webots:
        _require(
            document.get("planner_id") == "ump.reference.warehouse-planner/v1",
            "Webots smoke report planner identity is invalid",
        )
    else:
        _require(
            world.get("service") == "/world/ump_conformance/control",
            "ROS 2 smoke world service is invalid",
        )
    if world_path is not None:
        try:
            actual_digest = sha256(Path(world_path).read_bytes()).hexdigest()
        except OSError as error:
            raise Ros2EvidenceValidationError(
                f"Simulator world cannot be read: {error}"
            ) from error
        _require(world.get("sha256") == actual_digest, "Simulator world digest differs")

    return {
        "valid": True,
        "validation_scope": "native_smoke_outcomes_and_source_binding",
        "profile": document["profile"],
        "repository_revision": repository_revision,
        "ros_distro": environment["ros_distro"],
        "checks_verified": len(checks),
        "results_verified": len(results),
    }
