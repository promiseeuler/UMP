from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from ..adapter import RobotAdapter
from ..models import Assignment, RobotManifest, RobotState


class IntegrationUnavailableError(RuntimeError):
    """Raised only when a selected integration runtime is unavailable."""


class TaskAuthorizationError(PermissionError):
    """Raised when an external task cannot cross the robot authority boundary."""


class FieldMappingStatus(str, Enum):
    MAPPED = "mapped"
    DEFAULTED = "defaulted"
    UNSUPPORTED = "unsupported"
    REJECTED = "rejected"


@dataclass(frozen=True)
class FieldMapping:
    field: str
    status: FieldMappingStatus
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.field or len(self.field) > 256:
            raise ValueError("mapping field must be a bounded non-empty path")
        if len(self.detail) > 1_024:
            raise ValueError("mapping detail exceeds 1024 characters")


@dataclass(frozen=True)
class MappingReport:
    standard: str
    standard_version: str
    direction: str
    source_id: str
    observed_at_ms: int
    fields: tuple[FieldMapping, ...]
    correlation_id: str | None = None
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.direction not in {"external_to_ump", "ump_to_external"}:
            raise ValueError("mapping direction is invalid")
        if not self.standard or not self.standard_version or not self.source_id:
            raise ValueError("mapping provenance is incomplete")
        if self.observed_at_ms < 0:
            raise ValueError("mapping observation time cannot be negative")
        if len(self.fields) > 256 or len(self.warnings) > 64:
            raise ValueError("mapping report exceeds bounded profile")

    @property
    def passed(self) -> bool:
        return all(item.status is not FieldMappingStatus.REJECTED for item in self.fields)

    def as_dict(self) -> dict[str, Any]:
        return {
            "standard": self.standard,
            "standard_version": self.standard_version,
            "direction": self.direction,
            "source_id": self.source_id,
            "observed_at_ms": self.observed_at_ms,
            "correlation_id": self.correlation_id,
            "passed": self.passed,
            "fields": [
                {"field": item.field, "status": item.status.value, "detail": item.detail}
                for item in self.fields
            ],
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class IntegrationProvenance:
    source_standard: str
    source_version: str
    external_id: str
    mapped_at_ms: int
    report: MappingReport


@dataclass(frozen=True)
class ExternalTaskMapping:
    external_type: str
    capability: str
    input_fields: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.external_type or not self.capability:
            raise ValueError("external task mapping requires type and capability")
        namespace, separator, version = self.capability.rpartition("/v")
        if not separator or not namespace or not version.isdigit():
            raise ValueError("task mapping capability must be versioned")
        if len(self.input_fields) > 64:
            raise ValueError("task mapping exceeds 64 fields")

    def map_input(self, document: Mapping[str, Any]) -> dict[str, Any]:
        mapped: dict[str, Any] = {}
        for external, ump_name in self.input_fields.items():
            if external in document:
                mapped[ump_name] = document[external]
        return mapped


TaskAuthorizer = Callable[[str, str, str], bool]


@runtime_checkable
class StandardsAdapter(RobotAdapter, Protocol):
    standard: str
    standard_version: str

    def ingest_manifest(
        self, document: Mapping[str, Any], observed_at_ms: int
    ) -> tuple[RobotManifest, MappingReport]: ...

    def ingest_state(
        self, document: Mapping[str, Any], observed_at_ms: int
    ) -> tuple[RobotState, MappingReport]: ...

    def export_manifest(self) -> tuple[dict[str, Any], MappingReport]: ...

    def export_state(self) -> tuple[dict[str, Any], MappingReport]: ...

    def translate_external_task(
        self,
        document: Mapping[str, Any],
        *,
        issuer_id: str,
        lease_id: str,
        assignment_id: str,
        issued_at_ms: int,
    ) -> Assignment: ...


def require_task_authorization(
    *,
    read_only: bool,
    authorizer: TaskAuthorizer | None,
    robot_id: str,
    issuer_id: str,
    lease_id: str,
    capability: str,
) -> None:
    if read_only:
        raise TaskAuthorizationError("integration is configured read-only")
    if authorizer is None or not authorizer(issuer_id, lease_id, capability):
        raise TaskAuthorizationError(
            f"authority lease does not permit {capability} for {robot_id}"
        )
