"""Transport-independent collaboration planner contract and trusted loader."""

from __future__ import annotations

from collections.abc import Mapping
from importlib import import_module
from pathlib import Path
from typing import Protocol, runtime_checkable

from .models import Plan, RobotManifest, RobotState, SharedGoal


@runtime_checkable
class Planner(Protocol):
    """Proposes an untrusted high-level plan from authorized UMP context."""

    planner_id: str

    def propose(
        self,
        goal: SharedGoal,
        manifests: Mapping[str, RobotManifest],
        states: Mapping[str, RobotState],
    ) -> Plan: ...


def load_planner(
    specification: str, config_path: str | Path | None = None
) -> Planner:
    """Load a trusted planner factory from ``module:attribute``."""
    module_name, separator, attribute_name = specification.partition(":")
    if not separator or not module_name or not attribute_name:
        raise ValueError("planner must use module:factory syntax")
    try:
        factory = getattr(import_module(module_name), attribute_name)
    except (ImportError, AttributeError) as error:
        raise ValueError(f"planner factory cannot be loaded: {specification}") from error
    if not callable(factory):
        raise ValueError("planner factory must be callable")
    planner = factory(Path(config_path) if config_path is not None else None)
    if not isinstance(planner, Planner):
        raise TypeError("planner factory did not return a Planner")
    if not isinstance(planner.planner_id, str) or not planner.planner_id.strip():
        raise ValueError("planner_id must be non-empty")
    return planner
