"""Run one dependency-free standards mapping fixture without external services."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.integrations.massrobotics import MassRoboticsAdapter
from ump.integrations.open_rmf import OpenRmfAdapter
from ump.integrations.opc_ua import OpcUaRoboticsAdapter
from ump.integrations.ros2 import Ros2SemanticBridge
from ump.integrations.vda5050 import Vda5050Adapter
from ump.models import Mode, RobotManifest, RobotState, Safety
from ump.ros2 import Ros2RobotAdapter, SemanticStateStore


class DemoBackend:
    def execute(self, binding, assignment):
        raise RuntimeError("mapping demo never executes native work")

    def cancel(self, assignment_id, reason):
        return False, "mapping demo never executes native work"


def base(robot_id: str):
    manifest = RobotManifest(robot_id, "Example", "M1", "mobile_robot", ())
    state = RobotState(robot_id, Mode.IDLE, Safety.UNKNOWN, "Idle", "Await", 0.0, "Idle")
    return manifest, state


def run(standard: str):
    manifest, state = base(f"robot-{standard.replace('_', '-')}")
    if standard == "massrobotics":
        adapter = MassRoboticsAdapter(manifest, state, external_id="amr-1")
        return adapter.ingest_state({"uuid": "amr-1", "operationalState": "idle", "batteryPercentage": 75}, 1_000)
    if standard == "vda5050":
        adapter = Vda5050Adapter(manifest, state, external_id="agv-1")
        return adapter.ingest_state({"serialNumber": "agv-1", "operatingMode": "AUTOMATIC", "safetyState": {"eStop": "NONE"}, "errors": []}, 1_000)
    if standard == "open_rmf":
        adapter = OpenRmfAdapter(manifest, state, external_id="rmf-1")
        return adapter.ingest_state({"name": "rmf-1", "mode": "idle"}, 1_000)
    if standard == "opc_ua":
        adapter = OpcUaRoboticsAdapter(manifest, state, external_id="opc-1")
        return adapter.ingest_state({"SerialNumber": "opc-1", "OperatingMode": "idle", "Health": "healthy"}, 1_000)
    ros_adapter = Ros2RobotAdapter(manifest, SemanticStateStore(state), {}, DemoBackend())
    return Ros2SemanticBridge(ros_adapter).export_state(1_000)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("standard", choices=("massrobotics", "vda5050", "open_rmf", "ros2", "opc_ua"))
    arguments = parser.parse_args(argv)
    value, report = run(arguments.standard)
    print(json.dumps({"mapped_robot_id": value.robot_id if hasattr(value, "robot_id") else value["robot_id"], "report": report.as_dict()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
