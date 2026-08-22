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
    "StandardsAdapter",
    "TaskAuthorizationError",
    "integration_config_schema",
    "load_integration_config",
    "validate_integration_config",
]
