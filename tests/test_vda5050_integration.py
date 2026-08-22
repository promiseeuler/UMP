from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.integrations import ExternalTaskMapping, TaskAuthorizationError
from ump.integrations.vda5050 import Vda5050Adapter
from ump.models import Capability, Mode, RobotManifest, RobotState, Safety


def adapter(*, read_only=True, authorizer=None):
    capability = Capability("ump.material.carry/v1", "Carry", {"type": "object"}, {"type": "object"})
    manifest = RobotManifest("robot-1", "Vendor", "M1", "mobile_robot", (capability,))
    state = RobotState("robot-1", Mode.IDLE, Safety.NORMAL, "Idle", "Await", 0, "Idle")
    return Vda5050Adapter(
        manifest, state, external_id="serial-1", read_only=read_only,
        authorizer=authorizer,
        task_mappings=(ExternalTaskMapping("transport", capability.name, {"destination": "destination"}),),
    )


class Vda5050IntegrationTests(unittest.TestCase):
    def test_state_maps_safety_pose_battery_and_health(self):
        state, report = adapter().ingest_state({
            "serialNumber": "serial-1", "operatingMode": "AUTOMATIC",
            "batteryState": {"batteryCharge": 50, "charging": False},
            "agvPosition": {"x": 1, "y": 2, "theta": 0, "mapId": "warehouse"},
            "safetyState": {"eStop": "NONE", "fieldViolation": False}, "errors": [],
        }, 50)
        self.assertEqual(state.battery.level, 0.5)
        self.assertEqual(state.pose.frame_id, "warehouse")
        self.assertTrue(report.passed)

    def test_order_requires_task_mode_mapping_and_authority(self):
        with self.assertRaises(TaskAuthorizationError):
            adapter().translate_external_task(
                {"orderType": "transport", "destination": "dock"}, issuer_id="rmf",
                lease_id="lease-1", assignment_id="assignment-1", issued_at_ms=1,
            )
        assignment = adapter(read_only=False, authorizer=lambda issuer, lease, capability: True).translate_external_task(
            {"orderType": "transport", "destination": "dock"}, issuer_id="rmf",
            lease_id="lease-1", assignment_id="assignment-1", issued_at_ms=1,
        )
        self.assertEqual(assignment.step.inputs, {"destination": "dock"})

    def test_topic_is_versioned_and_identity_scoped(self):
        self.assertEqual(adapter().topic("state"), "uagv/v3/Vendor/serial-1/state")


if __name__ == "__main__":
    unittest.main()
