from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.integrations.ros2 import Ros2SemanticBridge
from ump.models import Capability, Mode, RobotManifest, RobotState, Safety
from ump.ros2 import Ros2RobotAdapter, SemanticStateStore


class NullBackend:
    def execute(self, binding, assignment):
        raise AssertionError("not used")

    def cancel(self, assignment_id, reason):
        return False, "not used"


class Ros2SemanticIntegrationTests(unittest.TestCase):
    def bridge(self):
        capability = Capability("ump.observe/v1", "Observe")
        manifest = RobotManifest("robot-1", "Vendor", "M1", "mobile_robot", (capability,))
        state = RobotState("robot-1", Mode.IDLE, Safety.NORMAL, "Idle", "Await", 0, "Idle")
        return Ros2SemanticBridge(Ros2RobotAdapter(manifest, SemanticStateStore(state), {}, NullBackend()))

    def test_awareness_messages_preserve_protocol_and_identity(self):
        document, report = self.bridge().export_state(100)
        self.assertEqual(document["protocol"], "ump/0.1")
        self.assertEqual(document["robot_id"], "robot-1")
        self.assertTrue(report.passed)

    def test_wrong_protocol_is_rejected(self):
        with self.assertRaises(ValueError):
            self.bridge().ingest_manifest({"protocol": "ump/9", "robot_id": "robot-1"}, 1)


if __name__ == "__main__":
    unittest.main()
