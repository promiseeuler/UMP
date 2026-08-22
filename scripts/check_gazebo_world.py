#!/usr/bin/env python3
"""Dependency-free structural smoke check for the reference Gazebo world."""

from pathlib import Path
import sys
import xml.etree.ElementTree as ET


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    path = Path(arguments[0] if arguments else "gazebo/warehouse.sdf")
    root = ET.parse(path).getroot()
    world = root.find("world")
    if world is None:
        raise ValueError("SDF does not contain a world")
    names = {model.get("name") for model in world.findall("model")}
    required = {"mobile-1", "inspector-1", "manipulator-1", "workcell", "floor"}
    if missing := sorted(required - names):
        raise ValueError(f"Gazebo world is missing models: {missing}")
    print(f"Gazebo world valid: {path} ({len(names)} models)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
