from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.integrations import ExternalTaskMapping
from ump.integrations.open_rmf import OpenRmfAdapter
from ump.models import Capability, Mode, RobotManifest, RobotState, Safety


class OpenRmfIntegrationTests(unittest.TestCase):
    def adapter(self):
        capability = Capability("ump.material.carry/v1", "Carry", {"type": "object"}, {"type": "object"})
        manifest = RobotManifest("robot-1", "fleet-1", "M1", "mobile_robot", (capability,))
        state = RobotState("robot-1", Mode.IDLE, Safety.UNKNOWN, "Idle", "Await", 0, "Idle")
        return OpenRmfAdapter(manifest, state, external_id="rmf-robot-1", read_only=False, authorizer=lambda *_: True, task_mappings=(ExternalTaskMapping("delivery", capability.name, {"dropoff": "destination"}),))

    def test_state_maps_level_pose_and_task(self):
        state, report = self.adapter().ingest_state({"name": "rmf-robot-1", "mode": "moving", "task_id": "task-1", "progress": 0.4, "location": {"x": 1, "y": 2, "yaw": 0, "level_name": "L1"}}, 10)
        self.assertEqual(state.pose.frame_id, "L1")
        self.assertEqual(state.progress, 0.4)
        self.assertTrue(report.passed)

    def test_task_mapping_preserves_rmf_as_facility_authority(self):
        assignment = self.adapter().translate_external_task({"category": "delivery", "description": {"dropoff": "dock"}}, issuer_id="rmf", lease_id="lease-1", assignment_id="assignment-1", issued_at_ms=1)
        self.assertEqual(assignment.step.inputs, {"destination": "dock"})
        self.assertIn("traffic", self.adapter().delegated_domains())


if __name__ == "__main__":
    unittest.main()
