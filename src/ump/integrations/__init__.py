"""Standards-compatible adapters that preserve the UMP safety boundary."""

from .base import (
    ExternalTaskMapping,
    FieldMapping,
    FieldMappingStatus,
    IntegrationProvenance,
    IntegrationUnavailableError,
    MappingReport,
    StandardsAdapter,
    TaskAuthorizationError,
    mapping_report_schema,
    validate_mapping_report,
)
from .config import (
    IntegrationConfig,
    IntegrationConfigError,
    integration_config_schema,
    load_integration_config,
    validate_integration_config,
)

__all__ = [
    "ExternalTaskMapping",
    "FieldMapping",
    "FieldMappingStatus",
    "IntegrationConfig",
    "IntegrationConfigError",
    "IntegrationProvenance",
    "IntegrationUnavailableError",
    "MappingReport",
    "mapping_report_schema",
    "StandardsAdapter",
    "TaskAuthorizationError",
    "integration_config_schema",
    "load_integration_config",
    "validate_integration_config",
    "validate_mapping_report",
]
