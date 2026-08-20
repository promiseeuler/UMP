from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
import math
import re
from typing import Any
from urllib.parse import urlsplit

MAX_ID_BYTES = 128
MAX_TEXT_BYTES = 1_024
MAX_REFERENCE_URI_BYTES = 2_048
MAX_STRUCTURED_OUTPUT_BYTES = 16_384
FRAME_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-/]{0,127}$")
MEDIA_TYPE_PATTERN = re.compile(
    r"^[A-Za-z0-9!#$&^_.+\-]+/[A-Za-z0-9!#$&^_.+\-]+$"
)
ROBOTICS_SI_UNITS = frozenset(
    {
        "1",
        "m",
        "m^2",
        "m^3",
        "m/s",
        "m/s^2",
        "rad",
        "rad/s",
        "rad/s^2",
        "s",
        "Hz",
        "kg",
        "kg/m^3",
        "N",
        "N*m",
        "Pa",
        "K",
        "A",
        "V",
        "W",
        "J",
    }
)
SPATIAL_SI_UNITS = frozenset(
    {"m", "m^2", "m^3", "m/s", "m/s^2", "rad", "rad/s", "rad/s^2"}
)


def _required_text(value: str, field_name: str, maximum: int = MAX_TEXT_BYTES) -> None:
    size = len(value.encode("utf-8"))
    if not value.strip() or size > maximum:
        raise ValueError(f"{field_name} must contain 1 to {maximum} UTF-8 bytes")


def _identifier(value: str, field_name: str) -> None:
    _required_text(value, field_name, MAX_ID_BYTES)


def validate_capability_units(schema: dict[str, Any], schema_name: str) -> None:
    """Require explicit units and frames for numeric capability contract fields."""

    def visit(
        node: Any,
        path: str,
        enclosing_object: dict[str, Any] | None,
    ) -> None:
        if not isinstance(node, dict):
            return
        declared_type = node.get("type")
        types = (
            set(declared_type)
            if isinstance(declared_type, list)
            else {declared_type}
        )
        if types & {"number", "integer"}:
            unit = node.get("x-ump-unit")
            if unit not in ROBOTICS_SI_UNITS:
                raise ValueError(
                    f"{schema_name} numeric field {path} requires supported x-ump-unit"
                )
            if unit in SPATIAL_SI_UNITS:
                if enclosing_object is None:
                    raise ValueError(
                        f"{schema_name} spatial field {path} requires an enclosing frame"
                    )
                frame_field = enclosing_object.get("x-ump-frame-field")
                properties = enclosing_object.get("properties", {})
                required = enclosing_object.get("required", ())
                frame_schema = properties.get(frame_field, {}) if frame_field else {}
                if (
                    not isinstance(frame_field, str)
                    or frame_field not in required
                    or frame_schema.get("type") != "string"
                ):
                    raise ValueError(
                        f"{schema_name} spatial field {path} requires a named string frame field"
                    )
        current_object = node if "object" in types else enclosing_object
        for property_name, child in node.get("properties", {}).items():
            visit(child, f"{path}/properties/{property_name}", current_object)
        if "items" in node:
            visit(node["items"], f"{path}/items", current_object)
        for keyword in ("allOf", "anyOf", "oneOf"):
            for index, child in enumerate(node.get(keyword, ())):
                visit(child, f"{path}/{keyword}/{index}", current_object)
        for definition_name, child in node.get("$defs", {}).items():
            visit(child, f"{path}/$defs/{definition_name}", None)

    visit(schema, "$", None)


class Availability(str, Enum):
    AVAILABLE = "available"
    BUSY = "busy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class Mode(str, Enum):
    STARTING = "starting"
    IDLE = "idle"
    WORKING = "working"
    WAITING = "waiting"
    PAUSED = "paused"
    FAULTED = "faulted"
    OFFLINE = "offline"


class Safety(str, Enum):
    NORMAL = "normal"
    PROTECTIVE_STOP = "protective_stop"
    EMERGENCY_STOP = "emergency_stop"
    RECOVERY_REQUIRED = "recovery_required"
    UNKNOWN = "unknown"


class AssignmentStatus(str, Enum):
    ACCEPTED = "accepted"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class CancellationStatus(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ALREADY_TERMINAL = "already_terminal"
    UNKNOWN = "unknown"


class LeaseStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


@dataclass(frozen=True)
class PoseReference:
    """Frame-qualified SI pose; position is meters and orientation is xyzw."""

    frame_id: str
    position_m: tuple[float, float, float]
    orientation_xyzw: tuple[float, float, float, float]
    observed_at_ms: int

    def __post_init__(self) -> None:
        if not FRAME_ID_PATTERN.fullmatch(self.frame_id):
            raise ValueError("pose frame_id is outside the core profile")
        if len(self.position_m) != 3 or not all(
            math.isfinite(value) for value in self.position_m
        ):
            raise ValueError("pose position_m requires three finite SI meter values")
        if len(self.orientation_xyzw) != 4 or not all(
            math.isfinite(value) for value in self.orientation_xyzw
        ):
            raise ValueError("pose orientation_xyzw requires four finite values")
        norm = math.sqrt(sum(value * value for value in self.orientation_xyzw))
        if not math.isclose(norm, 1.0, rel_tol=1e-6, abs_tol=1e-6):
            raise ValueError("pose orientation_xyzw must be a normalized quaternion")
        if self.observed_at_ms < 0:
            raise ValueError("pose observed_at_ms cannot be negative")


@dataclass(frozen=True)
class SensorReference:
    """Metadata-only reference to externally authorized sensor content."""

    uri: str
    media_type: str
    byte_length: int
    sha256: str
    observed_at_ms: int
    expires_at_ms: int | None = None
    frame_id: str | None = None

    def __post_init__(self) -> None:
        if not self.uri or len(self.uri.encode("utf-8")) > MAX_REFERENCE_URI_BYTES:
            raise ValueError("sensor URI is outside the core profile")
        parsed = urlsplit(self.uri)
        if not parsed.scheme or parsed.scheme.lower() == "data":
            raise ValueError("sensor URI must be absolute and cannot embed data")
        if not MEDIA_TYPE_PATTERN.fullmatch(self.media_type):
            raise ValueError("sensor media_type is invalid")
        if type(self.byte_length) is not int or not 0 <= self.byte_length < 2**63:
            raise ValueError("sensor byte_length is outside the core profile")
        digest = self.sha256.lower()
        if len(digest) != 64 or any(value not in "0123456789abcdef" for value in digest):
            raise ValueError("sensor sha256 must be 64 hexadecimal characters")
        if self.sha256 != digest:
            raise ValueError("sensor sha256 must use lowercase hexadecimal")
        if self.observed_at_ms < 0:
            raise ValueError("sensor observed_at_ms cannot be negative")
        if self.expires_at_ms is not None and self.expires_at_ms < self.observed_at_ms:
            raise ValueError("sensor reference cannot expire before observation")
        if self.frame_id is not None and not FRAME_ID_PATTERN.fullmatch(self.frame_id):
            raise ValueError("sensor frame_id is outside the core profile")


@dataclass(frozen=True)
class Capability:
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    availability: Availability = Availability.AVAILABLE

    def __post_init__(self) -> None:
        _identifier(self.name, "capability name")
        _required_text(self.description, "capability description")
        namespace, separator, version = self.name.rpartition("/v")
        if not separator or not namespace or not version.isdigit():
            raise ValueError("capability requires a versioned name and description")
        validate_capability_units(self.input_schema, "capability input schema")
        validate_capability_units(self.output_schema, "capability output schema")


@dataclass(frozen=True)
class RobotManifest:
    robot_id: str
    manufacturer: str
    model: str
    robot_class: str
    capabilities: tuple[Capability, ...]
    adapter_version: str = "0.1.0"

    def __post_init__(self) -> None:
        _identifier(self.robot_id, "robot_id")
        _required_text(self.manufacturer, "manufacturer")
        _required_text(self.model, "model")
        _identifier(self.robot_class, "robot_class")
        _required_text(self.adapter_version, "adapter_version", 64)
        names = [capability.name for capability in self.capabilities]
        if len(names) != len(set(names)):
            raise ValueError("capability names must be unique")

    def capability(self, name: str) -> Capability | None:
        return next((item for item in self.capabilities if item.name == name), None)


@dataclass(frozen=True)
class RobotState:
    robot_id: str
    mode: Mode
    safety: Safety
    activity: str
    intent: str
    progress: float
    summary: str
    fresh_for_ms: int = 2_000
    blockers: tuple[str, ...] = ()
    resources: tuple[str, ...] = ()
    assignment_id: str | None = None
    pose: PoseReference | None = None
    sensor_references: tuple[SensorReference, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.robot_id, "robot_id")
        _required_text(self.activity, "activity")
        _required_text(self.intent, "intent")
        if not 0.0 <= self.progress <= 1.0:
            raise ValueError("progress must be between 0 and 1")
        _required_text(self.summary, "summary")
        if not 1 <= self.fresh_for_ms <= 60_000:
            raise ValueError("fresh_for_ms is outside the core profile")
        if len(self.resources) != len(set(self.resources)):
            raise ValueError("state resources must be unique")
        for resource in self.resources:
            _identifier(resource, "state resource")
        if len(self.sensor_references) > 32:
            raise ValueError("state exceeds 32 sensor references")
        reference_keys = [
            (reference.uri, reference.sha256) for reference in self.sensor_references
        ]
        if len(reference_keys) != len(set(reference_keys)):
            raise ValueError("state sensor references must be unique")


@dataclass(frozen=True)
class SharedGoal:
    goal_id: str
    description: str
    participant_ids: tuple[str, ...]
    constraints: dict[str, Any] = field(default_factory=dict)
    deadline_ms: int | None = None

    def __post_init__(self) -> None:
        _identifier(self.goal_id, "goal_id")
        _required_text(self.description, "goal description", 4_096)
        if not 1 <= len(self.participant_ids) <= 256:
            raise ValueError("goal requires 1 to 256 participants")
        if len(self.participant_ids) != len(set(self.participant_ids)):
            raise ValueError("goal participants must be unique")
        for participant_id in self.participant_ids:
            _identifier(participant_id, "participant_id")
        if self.deadline_ms is not None and self.deadline_ms < 0:
            raise ValueError("goal deadline cannot be negative")


@dataclass(frozen=True)
class PlanStep:
    step_id: str
    description: str
    assigned_robot_id: str
    capability: str
    inputs: Any
    completion_criteria: str
    depends_on: tuple[str, ...] = ()
    resources: tuple[str, ...] = ()
    not_before_ms: int | None = None
    deadline_ms: int | None = None

    def __post_init__(self) -> None:
        _identifier(self.step_id, "step_id")
        _required_text(self.description, "step description", 4_096)
        _identifier(self.assigned_robot_id, "assigned_robot_id")
        _identifier(self.capability, "capability")
        _required_text(self.completion_criteria, "completion criteria", 4_096)
        for dependency in self.depends_on:
            _identifier(dependency, "dependency")
        if len(self.resources) != len(set(self.resources)):
            raise ValueError("step resources must be unique")
        for resource in self.resources:
            _identifier(resource, "step resource")
        if self.not_before_ms is not None and self.not_before_ms < 0:
            raise ValueError("not_before_ms cannot be negative")
        if self.deadline_ms is not None and self.deadline_ms < 0:
            raise ValueError("step deadline cannot be negative")
        if (
            self.not_before_ms is not None
            and self.deadline_ms is not None
            and self.not_before_ms > self.deadline_ms
        ):
            raise ValueError("step execution window is invalid")


@dataclass(frozen=True)
class Plan:
    plan_id: str
    goal_id: str
    planner_id: str
    summary: str
    steps: tuple[PlanStep, ...]
    revision: int = 1
    supersedes_plan_id: str | None = None

    def __post_init__(self) -> None:
        _identifier(self.plan_id, "plan_id")
        _identifier(self.goal_id, "goal_id")
        _identifier(self.planner_id, "planner_id")
        _required_text(self.summary, "plan summary", 4_096)
        if self.revision < 1:
            raise ValueError("plan revision must be positive")
        if self.revision == 1 and self.supersedes_plan_id is not None:
            raise ValueError("initial plan revision cannot supersede another plan")
        if self.revision > 1:
            if self.supersedes_plan_id is None:
                raise ValueError("later plan revision must identify its predecessor")
            _identifier(self.supersedes_plan_id, "supersedes_plan_id")
            if self.supersedes_plan_id == self.plan_id:
                raise ValueError("plan revision cannot supersede itself")
        if not 1 <= len(self.steps) <= 256:
            raise ValueError("plan requires 1 to 256 steps")


@dataclass(frozen=True)
class Assignment:
    assignment_id: str
    goal_id: str
    plan_id: str
    step: PlanStep
    authority_lease_id: str | None = None

    def __post_init__(self) -> None:
        _identifier(self.assignment_id, "assignment_id")
        _identifier(self.goal_id, "goal_id")
        _identifier(self.plan_id, "plan_id")
        if self.authority_lease_id is not None:
            _identifier(self.authority_lease_id, "authority_lease_id")


@dataclass(frozen=True)
class AuthorityLease:
    lease_id: str
    grantor_robot_id: str
    issuer_id: str
    capabilities: tuple[str, ...]
    issued_at_ms: int
    expires_at_ms: int
    maximum_clock_uncertainty_ms: int = 0
    revision: int = 1
    status: LeaseStatus = LeaseStatus.ACTIVE

    def __post_init__(self) -> None:
        _identifier(self.lease_id, "lease_id")
        _identifier(self.grantor_robot_id, "grantor_robot_id")
        _identifier(self.issuer_id, "issuer_id")
        if not self.capabilities or len(self.capabilities) > 256:
            raise ValueError("lease requires 1 to 256 capabilities")
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("lease capabilities must be unique")
        for capability in self.capabilities:
            _identifier(capability, "lease capability")
        if self.issued_at_ms < 0 or self.expires_at_ms <= self.issued_at_ms:
            raise ValueError("lease time window is invalid")
        if not 0 <= self.maximum_clock_uncertainty_ms <= 60_000:
            raise ValueError("lease clock uncertainty is outside the core profile")
        if self.revision < 1:
            raise ValueError("lease revision must be positive")


@dataclass(frozen=True)
class AssignmentAcknowledgement:
    assignment_id: str
    robot_id: str
    status: AssignmentStatus
    description: str

    def __post_init__(self) -> None:
        _identifier(self.assignment_id, "assignment_id")
        _identifier(self.robot_id, "robot_id")
        _required_text(self.description, "acknowledgement description", 4_096)


@dataclass(frozen=True)
class AssignmentQuery:
    assignment_id: str
    robot_id: str

    def __post_init__(self) -> None:
        _identifier(self.assignment_id, "assignment_id")
        _identifier(self.robot_id, "robot_id")


@dataclass(frozen=True)
class CancellationRequest:
    assignment_id: str
    robot_id: str
    reason: str

    def __post_init__(self) -> None:
        _identifier(self.assignment_id, "assignment_id")
        _identifier(self.robot_id, "robot_id")
        _required_text(self.reason, "cancellation reason", 4_096)


@dataclass(frozen=True)
class CancellationAcknowledgement:
    assignment_id: str
    robot_id: str
    status: CancellationStatus
    description: str

    def __post_init__(self) -> None:
        _identifier(self.assignment_id, "assignment_id")
        _identifier(self.robot_id, "robot_id")
        _required_text(self.description, "cancellation description", 4_096)


@dataclass(frozen=True)
class AssignmentSnapshot:
    assignment_id: str
    robot_id: str
    status: AssignmentStatus
    description: str
    fingerprint: str
    outcome: Outcome | None = None

    def __post_init__(self) -> None:
        _identifier(self.assignment_id, "assignment_id")
        _identifier(self.robot_id, "robot_id")
        _required_text(self.description, "snapshot description", 4_096)
        _required_text(self.fingerprint, "fingerprint", 128)
        if self.outcome is not None:
            if self.outcome.assignment_id != self.assignment_id:
                raise ValueError("snapshot outcome assignment_id does not match")
            if self.outcome.robot_id != self.robot_id:
                raise ValueError("snapshot outcome robot_id does not match")
            if self.outcome.status is not self.status:
                raise ValueError("snapshot outcome status does not match")


@dataclass(frozen=True)
class Outcome:
    assignment_id: str
    robot_id: str
    succeeded: bool
    description: str
    status: AssignmentStatus | None = None
    outputs: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _identifier(self.assignment_id, "assignment_id")
        _identifier(self.robot_id, "robot_id")
        _required_text(self.description, "outcome description", 4_096)
        resolved = self.status or (
            AssignmentStatus.SUCCEEDED if self.succeeded else AssignmentStatus.FAILED
        )
        if resolved is AssignmentStatus.ACCEPTED:
            raise ValueError("accepted is not a terminal outcome status")
        if self.succeeded != (resolved is AssignmentStatus.SUCCEEDED):
            raise ValueError("outcome succeeded flag and terminal status disagree")
        if not isinstance(self.outputs, dict):
            raise ValueError("outcome outputs must be an object")
        try:
            encoded_outputs = json.dumps(
                self.outputs,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ValueError("outcome outputs must be JSON serializable") from error
        if len(encoded_outputs) > MAX_STRUCTURED_OUTPUT_BYTES:
            raise ValueError(
                f"outcome outputs exceed {MAX_STRUCTURED_OUTPUT_BYTES} UTF-8 bytes"
            )
        object.__setattr__(self, "status", resolved)


def payload(value: object) -> dict[str, Any]:
    """Convert a protocol model to JSON-compatible primitives."""
    return asdict(value)
