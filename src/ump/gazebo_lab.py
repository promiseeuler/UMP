"""Run the mixed-fleet UMP scenario against Gazebo native pose services."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import time

from .lab import VirtualRobotController, run_scenario


def _set_pose(model: str, x: float, y: float, z: float) -> None:
    request = f'name: "{model}", position: {{x: {x}, y: {y}, z: {z}}}'
    completed = subprocess.run(
        [
            "gz", "service", "-s", "/world/ump_warehouse/set_pose",
            "--reqtype", "gz.msgs.Pose", "--reptype", "gz.msgs.Boolean",
            "--timeout", "5000", "--req", request,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 or "data: true" not in completed.stdout:
        raise RuntimeError(f"Gazebo rejected pose for {model}: {completed.stderr or completed.stdout}")


def _configure(controllers: dict[str, VirtualRobotController]) -> None:
    def transport(assignment):
        destination = assignment.step.inputs["destination_m"]
        _set_pose("mobile-1", destination[0], destination[1], 0.35)
        _set_pose("payload", destination[0], destination[1], 0.8)
        return {"delivered": True, "final_position_m": destination, "frame_id": assignment.step.inputs["frame_id"], "native_backend": "gazebo"}

    def inspect(assignment):
        del assignment
        return {"ready": True, "observations": ["workcell_clear", "fixture_available"], "native_backend": "gazebo"}

    def place(assignment):
        _set_pose("payload", 8.0, 2.0, 1.2)
        return {"placed": True, "target": assignment.step.inputs["target"], "frame_id": assignment.step.inputs["frame_id"], "native_backend": "gazebo"}

    controllers["mobile-1"].set_native_executor(transport)
    controllers["inspector-1"].set_native_executor(inspect)
    controllers["manipulator-1"].set_native_executor(place)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ump.gazebo_lab")
    parser.add_argument("--world", default="/opt/ump/gazebo/warehouse.sdf")
    parser.add_argument("--workspace")
    parser.add_argument("--inspector-database")
    arguments = parser.parse_args(argv)
    server = subprocess.Popen(
        ["gz", "sim", "-s", "-r", arguments.world],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            topics = subprocess.run(["gz", "topic", "-l"], check=False, capture_output=True, text=True)
            if "/world/ump_warehouse/pose/info" in topics.stdout:
                break
            if server.poll() is not None:
                raise RuntimeError(f"Gazebo exited during startup: {server.stderr.read()}")
            time.sleep(0.25)
        else:
            raise TimeoutError("Gazebo world did not become ready")
        if arguments.workspace:
            result = run_scenario(
                workspace=arguments.workspace,
                controller_setup=_configure,
                inspector_database=arguments.inspector_database,
            )
        else:
            with tempfile.TemporaryDirectory() as directory:
                result = run_scenario(
                    workspace=directory,
                    controller_setup=_configure,
                    inspector_database=arguments.inspector_database,
                )
        print(json.dumps(result.as_dict(), sort_keys=True))
        return 0 if result.passed else 1
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
