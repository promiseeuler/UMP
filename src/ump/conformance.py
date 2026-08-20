from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib.metadata import packages_distributions, version
from importlib.resources import files
import inspect
import json
from pathlib import Path
import platform
import re
import sys
import time
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from .adapter import RobotAdapter
from .models import Assignment, AssignmentStatus, Outcome, RobotManifest, RobotState
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


class AdapterEvidenceValidationError(ValueError):
    pass


def adapter_evidence_schema() -> dict[str, Any]:
    resource = files("ump").joinpath("adapter_evidence_data/v1/schema.json")
    return json.loads(resource.read_text(encoding="utf-8"))


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


def inspect_adapter_evidence(
    adapter: RobotAdapter,
    specification: str,
    *,
    repository_revision: str,
    observed_at_ms: int | None = None,
) -> dict[str, Any]:
    """Run read-only checks and bind the report to the loaded implementation."""
    if re.fullmatch(r"[0-9a-f]{40}", repository_revision) is None:
        raise ValueError("repository revision must be a full lowercase commit SHA")
    report = AdapterConformanceHarness().inspect(adapter)
    try:
        implementation_path = Path(inspect.getfile(type(adapter))).resolve()
    except (OSError, TypeError) as error:
        raise ValueError("adapter implementation file cannot be identified") from error
    if not implementation_path.is_file():
        raise ValueError("adapter implementation file does not exist")
    implementation_digest = hashlib.sha256(implementation_path.read_bytes()).hexdigest()
    module_root = type(adapter).__module__.partition(".")[0]
    distributions = []
    for distribution in sorted(packages_distributions().get(module_root, ())):
        try:
            distributions.append({"name": distribution, "version": version(distribution)})
        except Exception:
            distributions.append({"name": distribution, "version": "unknown"})

    manifest: RobotManifest | None = None
    try:
        candidate = adapter.manifest()
        if isinstance(candidate, RobotManifest):
            manifest = candidate
    except Exception:
        pass
    return {
        "profile": "ump.adapter-conformance/v1",
        "repository_revision": repository_revision,
        "mode": "read_only",
        "observed_at_ms": (
            int(time.time() * 1_000) if observed_at_ms is None else observed_at_ms
        ),
        "adapter": {
            "specification": specification,
            "implementation": {
                "path": str(implementation_path),
                "sha256": implementation_digest,
            },
            "module": type(adapter).__module__,
            "class": type(adapter).__qualname__,
            "distributions": distributions,
        },
        "robot": (
            {
                "robot_id": manifest.robot_id,
                "manufacturer": manifest.manufacturer,
                "model": manifest.model,
                "robot_class": manifest.robot_class,
                "adapter_version": manifest.adapter_version,
                "capabilities": [item.name for item in manifest.capabilities],
            }
            if manifest is not None
            else None
        ),
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
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "executable": sys.executable,
        },
    }


def validate_adapter_evidence(
    report_path: str | Path,
    *,
    implementation_path: str | Path | None = None,
) -> dict[str, Any]:
    """Validate a retained read-only adapter report and optional source binding."""
    path = Path(report_path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AdapterEvidenceValidationError(
            f"adapter evidence cannot be read: {error}"
        ) from error
    if not isinstance(document, dict):
        raise AdapterEvidenceValidationError("adapter evidence must be a JSON object")
    errors = sorted(
        Draft202012Validator(adapter_evidence_schema()).iter_errors(document),
        key=lambda item: list(item.path),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(item) for item in error.absolute_path) or "document"
        raise AdapterEvidenceValidationError(f"{location}: {error.message}")

    checks = document["checks"]
    expected_checks = {"adapter.manifest", "adapter.state", "adapter.identity"}
    check_ids = [check["id"] for check in checks]
    if len(check_ids) != len(set(check_ids)) or set(check_ids) != expected_checks:
        raise AdapterEvidenceValidationError(
            "adapter evidence must contain each read-only check exactly once"
        )
    checks_passed = all(check["passed"] for check in checks)
    if document["passed"] is not checks_passed:
        raise AdapterEvidenceValidationError(
            "adapter evidence summary disagrees with its checks"
        )
    source_verified = False
    if implementation_path is not None:
        source = Path(implementation_path)
        try:
            actual_digest = hashlib.sha256(source.read_bytes()).hexdigest()
        except OSError as error:
            raise AdapterEvidenceValidationError(
                f"adapter implementation cannot be read: {error}"
            ) from error
        expected_digest = document["adapter"]["implementation"]["sha256"]
        if actual_digest != expected_digest:
            raise AdapterEvidenceValidationError(
                "adapter implementation digest does not match the report"
            )
        source_verified = True
    return {
        "valid": True,
        "passed": checks_passed,
        "validation_scope": "report_structure_and_optional_source_binding",
        "profile": document["profile"],
        "repository_revision": document["repository_revision"],
        "subject": document["subject"],
        "robot_id": (
            document["robot"]["robot_id"] if document["robot"] is not None else None
        ),
        "implementation_sha256": document["adapter"]["implementation"]["sha256"],
        "implementation_source_verified": source_verified,
        "checks_verified": len(checks),
    }


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
