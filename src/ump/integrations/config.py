from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .base import ExternalTaskMapping


class IntegrationConfigError(ValueError):
    pass


@dataclass(frozen=True)
class IntegrationConfig:
    integration_type: str
    standard_version: str
    endpoint: str
    external_id: str
    robot_id: str
    read_only: bool = True
    disclosure: tuple[str, ...] = ()
    task_mappings: tuple[ExternalTaskMapping, ...] = ()
    options: dict[str, Any] | None = None


def integration_config_schema() -> dict[str, Any]:
    resource = files("ump").joinpath("integration_data/v1/config.schema.json")
    return json.loads(resource.read_text(encoding="utf-8"))


def load_integration_config(path: str | Path) -> IntegrationConfig:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        Draft202012Validator(integration_config_schema()).validate(document)
        mappings = tuple(
            ExternalTaskMapping(
                external_type=item["external_type"],
                capability=item["capability"],
                input_fields=item.get("input_fields", {}),
            )
            for item in document.get("task_mappings", ())
        )
        return IntegrationConfig(
            integration_type=document["type"],
            standard_version=document["standard_version"],
            endpoint=document["endpoint"],
            external_id=document["identity"]["external_id"],
            robot_id=document["identity"]["robot_id"],
            read_only=document.get("mode", "read_only") == "read_only",
            disclosure=tuple(document.get("disclosure", ())),
            task_mappings=mappings,
            options=document.get("options", {}),
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise IntegrationConfigError(f"integration config is invalid: {error}") from error


def validate_integration_config(path: str | Path) -> dict[str, Any]:
    config = load_integration_config(path)
    return {
        "valid": True,
        "profile": "ump.integration-config/v1",
        "type": config.integration_type,
        "standard_version": config.standard_version,
        "robot_id": config.robot_id,
        "external_id": config.external_id,
        "read_only": config.read_only,
        "task_mapping_count": len(config.task_mappings),
    }
