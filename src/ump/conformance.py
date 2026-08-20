from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from .models import Assignment, AssignmentStatus, Outcome, RobotManifest, RobotState
from .runtime import RobotAdapter
from .transport import ProtocolDecodeError, decode_envelope, encode_envelope


@dataclass(frozen=True)
class ConformanceCheck:
    check_id: str
    passed: bool
    description: str


@dataclass(frozen=True)
class ConformanceReport:
    subject: str
    checks: tuple[ConformanceCheck, ...]

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(check.passed for check in self.checks)


def _check(check_id: str, operation) -> ConformanceCheck:
    try:
        operation()
    except Exception as error:
        return ConformanceCheck(
            check_id, False, f"{type(error).__name__}: {error}"
        )
    return ConformanceCheck(check_id, True, "passed")


class AdapterConformanceHarness:
    """Read-only adapter checks with explicitly gated native execution."""

    def inspect(self, adapter: RobotAdapter) -> ConformanceReport:
        manifest_holder: list[RobotManifest] = []
        state_holder: list[RobotState] = []

        def manifest_check() -> None:
            manifest = adapter.manifest()
            if not isinstance(manifest, RobotManifest):
                raise TypeError("manifest() must return RobotManifest")
            for capability in manifest.capabilities:
                Draft202012Validator.check_schema(capability.input_schema)
                Draft202012Validator.check_schema(capability.output_schema)
            manifest_holder.append(manifest)

        def state_check() -> None:
            state = adapter.state()
            if not isinstance(state, RobotState):
                raise TypeError("state() must return RobotState")
            state_holder.append(state)

        checks = [
            _check("adapter.manifest", manifest_check),
            _check("adapter.state", state_check),
        ]

        def identity_check() -> None:
            if not manifest_holder or not state_holder:
                raise ValueError("manifest and state must pass before identity comparison")
            if manifest_holder[0].robot_id != state_holder[0].robot_id:
                raise ValueError("manifest and state robot IDs differ")

        checks.append(_check("adapter.identity", identity_check))
        subject = (
            manifest_holder[0].robot_id if manifest_holder else type(adapter).__name__
        )
        return ConformanceReport(subject, tuple(checks))

    def exercise(
        self,
        adapter: RobotAdapter,
        assignments: tuple[Assignment, ...],
        *,
        allow_native_execution: bool = False,
    ) -> ConformanceReport:
        if not allow_native_execution:
            raise PermissionError(
                "native adapter execution requires allow_native_execution=True"
            )
        manifest = adapter.manifest()
        checks = []
        for assignment in assignments:
            check_id = f"adapter.assignment.{assignment.assignment_id}"

            def execute(item: Assignment = assignment) -> None:
                if item.step.assigned_robot_id != manifest.robot_id:
                    raise ValueError("fixture assignment targets a different robot")
                capability = manifest.capability(item.step.capability)
                if capability is None:
                    raise ValueError("fixture requests an unadvertised capability")
                Draft202012Validator(capability.input_schema).validate(item.step.inputs)
                outcome = adapter.accept(item)
                self._validate_outcome(item, manifest, capability, outcome)

            checks.append(_check(check_id, execute))
        return ConformanceReport(manifest.robot_id, tuple(checks))

    @staticmethod
    def _validate_outcome(
        assignment: Assignment, manifest: RobotManifest, capability, outcome: Outcome
    ) -> None:
        if not isinstance(outcome, Outcome):
            raise TypeError("accept() must return Outcome")
        if outcome.assignment_id != assignment.assignment_id:
            raise ValueError("outcome assignment ID differs from fixture")
        if outcome.robot_id != manifest.robot_id:
            raise ValueError("outcome robot ID differs from manifest")
        if outcome.status is AssignmentStatus.ACCEPTED:
            raise ValueError("adapter returned a non-terminal outcome")
        if outcome.status is AssignmentStatus.SUCCEEDED:
            Draft202012Validator(capability.output_schema).validate(outcome.outputs)


def validate_vector_suite(directory: str | Path) -> ConformanceReport:
    root = Path(directory)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    schema_path = root / manifest["schema"]
    schema = json.loads(schema_path.read_text())
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    checks = []
    for vector in manifest["vectors"]:
        path = root / vector["file"]

        def validate(item: dict[str, Any] = vector, vector_path: Path = path) -> None:
            file_bytes = vector_path.read_bytes()
            encoded = (
                bytes.fromhex(file_bytes.decode("ascii"))
                if item.get("encoding") == "hex"
                else file_bytes
            )
            expected_hash = item.get("sha256")
            if expected_hash and hashlib.sha256(encoded).hexdigest() != expected_hash:
                raise ValueError("vector SHA-256 does not match manifest")
            expected = item["expected"]
            try:
                envelope = decode_envelope(encoded)
                validator.validate(json.loads(encoded))
                if encode_envelope(envelope) != encoded:
                    raise ValueError("valid vector is not canonical JSON")
            except (ProtocolDecodeError, ValidationError, UnicodeDecodeError, json.JSONDecodeError):
                if expected == "invalid":
                    return
                raise
            if expected != "valid":
                raise ValueError("invalid vector was accepted")

        checks.append(_check(f"vector.{vector['id']}", validate))
    return ConformanceReport(manifest["suite"], tuple(checks))
