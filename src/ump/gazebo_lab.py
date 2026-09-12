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


def _move_model(
    model: str,
    start: tuple[float, float, float],
    destination: tuple[float, float, float],
    *,
    duration_s: float,
    steps: int = 24,
) -> None:
    for index in range(1, steps + 1):
        progress = index / steps
        point = tuple(
            origin + ((target - origin) * progress)
            for origin, target in zip(start, destination, strict=True)
        )
        _set_pose(model, point[0], point[1], point[2])
        if duration_s:
            time.sleep(duration_s / steps)


def _transport_payload(
    destination: tuple[float, float, float], *, duration_s: float, steps: int = 32
) -> None:
    start = (-6.0, -2.0, 0.42)
    for index in range(1, steps + 1):
        progress = index / steps
        x = start[0] + ((destination[0] - start[0]) * progress)
        y = start[1] + ((destination[1] - start[1]) * progress)
        _set_pose("mobile-1", x, y, 0.42)
        _set_pose("payload", x, y, 1.12)
        if duration_s:
            time.sleep(duration_s / steps)


def _configure(
    controllers: dict[str, VirtualRobotController], *, demo_duration_s: float = 0.0
) -> None:
    def transport(assignment):
        destination = tuple(assignment.step.inputs["destination_m"])
        _transport_payload(destination, duration_s=demo_duration_s)
        return {"delivered": True, "final_position_m": destination, "frame_id": assignment.step.inputs["frame_id"], "native_backend": "gazebo"}

    def inspect(assignment):
        del assignment
        _move_model(
            "inspector-1", (-2.0, 3.0, 0.72), (7.0, 3.2, 0.72),
            duration_s=demo_duration_s * 0.65,
        )
        return {"ready": True, "observations": ["workcell_clear", "fixture_available"], "native_backend": "gazebo"}

    def place(assignment):
        _move_model(
            "payload", (8.0, 2.0, 1.12), (8.0, 2.0, 0.64),
            duration_s=demo_duration_s * 0.45,
            steps=14,
        )
        return {"placed": True, "target": assignment.step.inputs["target"], "frame_id": assignment.step.inputs["frame_id"], "native_backend": "gazebo"}

    controllers["mobile-1"].set_native_executor(transport)
    controllers["inspector-1"].set_native_executor(inspect)
    controllers["manipulator-1"].set_native_executor(place)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ump.gazebo_lab")
    parser.add_argument("--world", default="/opt/ump/gazebo/warehouse.sdf")
    parser.add_argument("--workspace")
    parser.add_argument("--inspector-database")
    parser.add_argument(
        "--demo-duration", type=float, default=0.0,
        help="Seconds used for each primary robot movement.",
    )
    parser.add_argument(
        "--reuse-server", action="store_true",
        help="Use an already running Gazebo world and leave it running.",
    )
    arguments = parser.parse_args(argv)
    if arguments.demo_duration < 0 or arguments.demo_duration > 120:
        parser.error("--demo-duration must be between 0 and 120 seconds")
    server = None
    if not arguments.reuse_server:
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
            if server is not None and server.poll() is not None:
                raise RuntimeError(f"Gazebo exited during startup: {server.stderr.read()}")
            time.sleep(0.25)
        else:
            raise TimeoutError("Gazebo world did not become ready")
        if arguments.workspace:
            result = run_scenario(
                workspace=arguments.workspace,
                controller_setup=lambda controllers: _configure(
                    controllers, demo_duration_s=arguments.demo_duration
                ),
                inspector_database=arguments.inspector_database,
            )
        else:
            with tempfile.TemporaryDirectory() as directory:
                result = run_scenario(
                    workspace=directory,
                    controller_setup=lambda controllers: _configure(
                        controllers, demo_duration_s=arguments.demo_duration
                    ),
                    inspector_database=arguments.inspector_database,
                )
        print(json.dumps(result.as_dict(), sort_keys=True))
        return 0 if result.passed else 1
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
