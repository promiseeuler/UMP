from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.integrations import TaskAuthorizationError
from ump.integrations.opc_ua import OpcUaClientProfile, OpcUaRoboticsAdapter, OpcUaServerProfile
from ump.models import Mode, RobotManifest, RobotState, Safety


class OpcUaIntegrationTests(unittest.TestCase):
    def adapter(self):
        manifest = RobotManifest("robot-1", "Vendor", "Arm", "industrial_robot", ())
        state = RobotState("robot-1", Mode.IDLE, Safety.NORMAL, "Idle", "Await", 0, "Idle")
        return OpcUaRoboticsAdapter(manifest, state, external_id="serial-1")

    def test_client_ingests_health_and_battery_nodes(self):
        state, report = OpcUaClientProfile(self.adapter()).ingest_nodes({"SerialNumber": "serial-1", "OperatingMode": "working", "Health": "healthy", "BatteryLevel": 80}, 10)
        self.assertEqual(state.health.value, "healthy")
        self.assertEqual(state.battery.level, 0.8)
        self.assertTrue(report.passed)

    def test_server_disclosure_is_read_only_and_filtered(self):
        server = OpcUaServerProfile(self.adapter(), ("SerialNumber", "Health"))
        self.assertEqual(set(server.address_space()), {"SerialNumber", "Health"})
        self.assertEqual(server.methods, ())

    def test_job_control_is_not_invented(self):
        with self.assertRaises(TaskAuthorizationError):
            self.adapter().translate_external_task({}, issuer_id="plant", lease_id="lease", assignment_id="a", issued_at_ms=1)


if __name__ == "__main__":
    unittest.main()
