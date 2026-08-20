"""Validation for portable, integrity-bound UMP hardware pilot bundles."""

from __future__ import annotations

from hashlib import sha256
from importlib.resources import files
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


class PilotValidationError(ValueError):
    pass


def pilot_schema() -> dict[str, Any]:
    resource = files("ump").joinpath("pilot_data/v1/schema.json")
    return json.loads(resource.read_text(encoding="utf-8"))


def _digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _evidence_items(document: dict[str, Any]):
    for participant in document["participants"]:
        yield participant["conformance_evidence"]
        assignment = participant.get("assignment_evidence")
        if assignment is not None:
            yield assignment
    yield from document["safety_evidence"].values()


def validate_pilot_bundle(manifest_path: str | Path) -> dict[str, Any]:
    """Validate pilot structure and every referenced artifact digest."""
    path = Path(manifest_path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PilotValidationError(f"pilot manifest cannot be read: {error}") from error
    if not isinstance(document, dict):
        raise PilotValidationError("pilot manifest must contain a JSON object")
    validator = Draft202012Validator(pilot_schema())
    errors = sorted(validator.iter_errors(document), key=lambda item: list(item.path))
    if errors:
        error = errors[0]
        location = ".".join(str(item) for item in error.absolute_path) or "document"
        raise PilotValidationError(f"{location}: {error.message}")

    participants = document["participants"]
    robot_ids = [participant["robot_id"] for participant in participants]
    if len(robot_ids) != len(set(robot_ids)):
        raise PilotValidationError("participant robot IDs must be unique")
    physical = [item for item in participants if item["deployment"] == "physical"]
    simulated = [item for item in participants if item["deployment"] == "simulation"]
    if len(physical) < 2 or not simulated:
        raise PilotValidationError(
            "hardware pilot requires at least two physical and one simulated participant"
        )
    if document["phase"] == "supervised_assignment":
        missing = [
            item["robot_id"] for item in physical if "assignment_evidence" not in item
        ]
        if missing:
            raise PilotValidationError(
                f"physical participants lack supervised assignment evidence: {missing}"
            )
    elif any("assignment_evidence" in item for item in participants):
        raise PilotValidationError(
            "read-only pilot must not contain supervised assignment evidence"
        )

    base = path.resolve().parent
    verified = 0
    evidence_items = tuple(_evidence_items(document))
    artifact_names = [item["artifact"] for item in evidence_items]
    if len(artifact_names) != len(set(artifact_names)):
        raise PilotValidationError("each evidence gate requires a distinct artifact")
    for evidence in evidence_items:
        artifact = (base / evidence["artifact"]).resolve()
        if not artifact.is_relative_to(base):
            raise PilotValidationError("evidence artifact escapes the pilot bundle")
        if not artifact.is_file():
            raise PilotValidationError(
                f"evidence artifact does not exist: {evidence['artifact']}"
            )
        actual = _digest(artifact)
        if actual != evidence["sha256"]:
            raise PilotValidationError(
                f"evidence digest does not match: {evidence['artifact']}"
            )
        verified += 1

    return {
        "valid": True,
        "validation_scope": "schema_topology_and_evidence_integrity",
        "protocol": document["protocol"],
        "pilot_id": document["pilot_id"],
        "phase": document["phase"],
        "physical_participants": len(physical),
        "simulated_participants": len(simulated),
        "evidence_artifacts_verified": verified,
    }
