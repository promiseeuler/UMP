from __future__ import annotations

from functools import lru_cache
from importlib.resources import files
import json

from .models import Capability


VOCABULARY_ID = "ump.standard/v1"


@lru_cache(maxsize=1)
def _catalog() -> dict[str, object]:
    resource = files("ump").joinpath("vocabulary_data/v1/catalog.json")
    value = json.loads(resource.read_text(encoding="utf-8"))
    if value.get("vocabulary") != VOCABULARY_ID:
        raise ValueError("standard vocabulary identifier does not match its path")
    return value


def standard_capability(name: str) -> Capability:
    for item in _catalog()["capabilities"]:
        if item["name"] == name:
            return Capability(
                name=item["name"],
                description=item["description"],
                input_schema=json.loads(json.dumps(item["input_schema"])),
                output_schema=json.loads(json.dumps(item["output_schema"])),
            )
    raise KeyError(f"unknown UMP standard capability: {name}")


def standard_capabilities() -> tuple[Capability, ...]:
    return tuple(
        standard_capability(item["name"])
        for item in _catalog()["capabilities"]
    )


def vocabulary_document() -> dict[str, object]:
    return json.loads(json.dumps(_catalog()))
