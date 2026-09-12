#!/usr/bin/env python3
"""Run the reference mixed-fleet scenario slowly enough for live inspection."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from ump.lab import VirtualRobotController, run_scenario


def _configure(
    controllers: dict[str, VirtualRobotController], step_duration_s: float
) -> None:
    def carry(assignment):
        time.sleep(step_duration_s)
        destination = list(assignment.step.inputs["destination_m"])
        return {
            "delivered": True,
            "final_position_m": destination,
            "frame_id": assignment.step.inputs["frame_id"],
        }

    def inspect(_assignment):
        time.sleep(step_duration_s)
        return {"ready": True, "observations": ["workcell_clear", "fixture_available"]}

    def place(assignment):
        time.sleep(step_duration_s)
        return {
            "placed": True,
            "target": assignment.step.inputs["target"],
            "frame_id": assignment.step.inputs["frame_id"],
        }

    controllers["mobile-1"].set_native_executor(carry)
    controllers["inspector-1"].set_native_executor(inspect)
    controllers["manipulator-1"].set_native_executor(place)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default="/tmp/ump-live-demo")
    parser.add_argument("--database", default="/tmp/ump-live-demo.sqlite3")
    parser.add_argument("--output", default="/tmp/ump-live-demo-result.json")
    parser.add_argument("--step-duration", type=float, default=6.0)
    arguments = parser.parse_args()
    if not 0.5 <= arguments.step_duration <= 30:
        parser.error("--step-duration must be between 0.5 and 30 seconds")

    result = run_scenario(
        workspace=arguments.workspace,
        inspector_database=arguments.database,
        controller_setup=lambda controllers: _configure(
            controllers, arguments.step_duration
        ),
    )
    Path(arguments.output).write_text(
        json.dumps(result.as_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
