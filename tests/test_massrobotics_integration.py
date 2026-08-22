from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.integrations.massrobotics import MassRoboticsAdapter
from ump.models import Mode, RobotManifest, RobotState, Safety


class MassRoboticsIntegrationTests(unittest.TestCase):
    def adapter(self):
        manifest = RobotManifest("robot-1", "Vendor", "M1", "mobile_robot", ())
        state = RobotState("robot-1", Mode.IDLE, Safety.UNKNOWN, "Idle", "Unknown", 0, "Idle")
        return MassRoboticsAdapter(manifest, state, external_id="amr-1")

    def test_status_maps_identity_pose_health_and_battery_without_guessing_intent(self):
        adapter = self.adapter()
        state, report = adapter.ingest_state(
            {
                "uuid": "amr-1",
                "operationalState": "working",
                "taskId": "task-1",
                "batteryPercentage": 72,
                "location": {"x": 1.0, "y": 2.0, "angle": 0.5, "planarDatum": "map"},
            },
            100,
        )
        self.assertEqual(state.battery.level, 0.72)
        self.assertEqual(state.pose.frame_id, "map")
        self.assertIn("Unknown", state.intent)
        self.assertTrue(report.passed)

    def test_wrong_external_identity_is_rejected(self):
        with self.assertRaises(ValueError):
            self.adapter().ingest_state({"uuid": "other"}, 100)

    def test_round_trip_exports_standard_status_shape(self):
        adapter = self.adapter()
        document, report = adapter.export_state()
        self.assertEqual(document["uuid"], "amr-1")
        self.assertTrue(report.passed)


if __name__ == "__main__":
    unittest.main()
