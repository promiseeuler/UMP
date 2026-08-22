from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.integrations import ExternalTaskMapping, FieldMapping, FieldMappingStatus, MappingReport
from ump.integrations.base import TaskAuthorizationError, require_task_authorization
from ump.integrations.config import load_integration_config


class IntegrationBaseTests(unittest.TestCase):
    def test_mapping_report_preserves_loss_information(self):
        report = MappingReport(
            "massrobotics",
            "1.0",
            "external_to_ump",
            "amr-1",
            100,
            (
                FieldMapping("battery", FieldMappingStatus.MAPPED),
                FieldMapping("intent", FieldMappingStatus.UNSUPPORTED, "not provided"),
            ),
        )
        self.assertTrue(report.passed)
        self.assertEqual(report.as_dict()["fields"][1]["status"], "unsupported")

    def test_external_tasks_are_read_only_and_authorized_by_default(self):
        with self.assertRaises(TaskAuthorizationError):
            require_task_authorization(
                read_only=True,
                authorizer=None,
                robot_id="robot-1",
                issuer_id="issuer-1",
                lease_id="lease-1",
                capability="ump.inspect/v1",
            )

    def test_configuration_is_discriminated_and_bounded(self):
        document = {
            "profile": "ump.integration-config/v1",
            "type": "massrobotics",
            "standard_version": "1.0",
            "endpoint": "ws://127.0.0.1:9000",
            "identity": {"external_id": "amr-1", "robot_id": "robot-1"},
            "task_mappings": [
                {"external_type": "move", "capability": "ump.move/v1"}
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "integration.json"
            path.write_text(json.dumps(document))
            config = load_integration_config(path)
        self.assertTrue(config.read_only)
        self.assertIsInstance(config.task_mappings[0], ExternalTaskMapping)


if __name__ == "__main__":
    unittest.main()
