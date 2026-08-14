import json
from pathlib import Path
import tempfile
import unittest

from verify_inspector import EXPECTED_IDS, REQUIRED_SECTIONS, verify


class InspectorVerifierTests(unittest.TestCase):
    def fixture(self, directory: Path) -> None:
        for name, machine_id in EXPECTED_IDS.items():
            value = {section: {} for section in REQUIRED_SECTIONS}
            value["machine"] = {"machine_id": machine_id}
            value["events"] = {"tasks": [], "coordination": []}
            value["tasks"] = {}
            (directory / f"{name}.json").write_text(json.dumps(value))
        mobile = json.loads((directory / "mobile.json").read_text())
        arm = json.loads((directory / "arm.json").read_text())
        for index in range(3):
            mobile["tasks"][f"mobile-{index}"] = {
                "task_id": f"mobile-{index}", "state": "succeeded"
            }
            arm["tasks"][f"arm-{index}"] = {
                "task_id": f"arm-{index}", "state": "succeeded"
            }
        (directory / "mobile.json").write_text(json.dumps(mobile))
        (directory / "arm.json").write_text(json.dumps(arm))
        zone = json.loads((directory / "zone.json").read_text())
        zone["reservations"] = {"zone": {"state": "released"}}
        zone["handoffs"] = {
            "handoff": {
                "state": "committed",
                "authoritative_owner_machine_id": "ump:machine:mobile-base-1",
            }
        }
        zone["events"]["coordination"] = [{}] * 8
        (directory / "zone.json").write_text(json.dumps(zone))

    def test_accepts_complete_nominal_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.fixture(directory)
            self.assertTrue(verify(directory)["passed"])

    def test_rejects_failed_task_and_secret_material(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.fixture(directory)
            mobile = json.loads((directory / "mobile.json").read_text())
            mobile["tasks"]["mobile-0"]["state"] = "failed"
            mobile["private_key"] = "secret"
            (directory / "mobile.json").write_text(json.dumps(mobile))
            result = verify(directory)
            self.assertFalse(result["passed"])
            self.assertIn("six_successful_tasks", result["failed_checks"])
            self.assertIn("no_secret_material", result["failed_checks"])

    def test_accepts_expected_fault_convergence(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.fixture(directory)
            mobile = json.loads((directory / "mobile.json").read_text())
            arm = json.loads((directory / "arm.json").read_text())
            zone = json.loads((directory / "zone.json").read_text())
            mobile["faults"] = {
                "failed_or_unknown_task_ids": ["s4-blocked-navigation"]
            }
            mobile["runtime"] = {"safety": "emergency_stop"}
            mobile["tasks"]["s4-post-emergency-gate"] = {
                "task_id": "s4-post-emergency-gate", "state": "accepted"
            }
            arm["faults"] = {"failed_or_unknown_task_ids": ["s4-failed-grasp"]}
            zone["faults"] = {
                "failed_or_unknown_handoff_ids": [
                    "s4-drop-handoff", "s4-physical-intrusion"
                ]
            }
            (directory / "mobile.json").write_text(json.dumps(mobile))
            (directory / "arm.json").write_text(json.dumps(arm))
            (directory / "zone.json").write_text(json.dumps(zone))
            self.assertTrue(verify(directory, "fault")["passed"])


if __name__ == "__main__":
    unittest.main()
