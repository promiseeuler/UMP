"""Strict owner shared-goal document parsing and public schemas."""

from __future__ import annotations

from importlib.resources import files
import json
from typing import Any

from jsonschema import Draft202012Validator

from .models import SharedGoal


def goal_schema() -> dict[str, Any]:
    resource = files("ump").joinpath("goal_data/v1/goal.schema.json")
    return json.loads(resource.read_text(encoding="utf-8"))


def goal_batch_schema() -> dict[str, Any]:
    resource = files("ump").joinpath("goal_data/v1/goal-batch.schema.json")
    return json.loads(resource.read_text(encoding="utf-8"))


def _validate(document: Any, schema: dict[str, Any], description: str) -> None:
    errors = sorted(
        Draft202012Validator(schema).iter_errors(document),
        key=lambda item: list(item.path),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(item) for item in error.absolute_path) or "document"
        raise ValueError(f"{description} {location}: {error.message}")


def shared_goal_from_document(document: Any) -> SharedGoal:
    _validate(document, goal_schema(), "goal")
    return SharedGoal(
        goal_id=document["goal_id"],
        description=document["description"],
        participant_ids=tuple(document["participant_ids"]),
        constraints=document.get("constraints", {}),
        deadline_ms=document.get("deadline_ms"),
    )


def shared_goals_from_document(document: Any) -> tuple[SharedGoal, ...]:
    _validate(document, goal_batch_schema(), "goal batch")
    goals = tuple(shared_goal_from_document(item) for item in document)
    goal_ids = [goal.goal_id for goal in goals]
    if len(goal_ids) != len(set(goal_ids)):
        raise ValueError("goal batch IDs must be unique")
    return goals


def goal_validation_report(document: Any, *, batch: bool = False) -> dict[str, Any]:
    goals = (
        shared_goals_from_document(document)
        if batch
        else (shared_goal_from_document(document),)
    )
    return {
        "valid": True,
        "profile": "ump.shared-goal-batch/v1" if batch else "ump.shared-goal/v1",
        "goals": len(goals),
        "goal_ids": [goal.goal_id for goal in goals],
        "participants": sorted(
            {participant for goal in goals for participant in goal.participant_ids}
        ),
        "deadlines_present": sum(goal.deadline_ms is not None for goal in goals),
    }
