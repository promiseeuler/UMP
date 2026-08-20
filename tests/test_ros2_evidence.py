from contextlib import redirect_stderr, redirect_stdout
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ump.cli import ros2_evidence_main
from ump.ros2_evidence import (
    Ros2EvidenceValidationError,
    validate_ros2_smoke_report,
)


REVISION = "a" * 40


def smoke_report(world: Path) -> dict:
    def result(assignment_id, status, *, robot_id=None, completed=False):
        value = {"assignment_id": assignment_id, "status": status}
        if robot_id is not None:
            value["robot_id"] = robot_id
        if completed:
            value["outputs"] = {"completed": True}
        return value

    return {
        "profile": "ump.ros2-gazebo-smoke/v1",
        "passed": True,
        "world": {
            "path": "/installed/three_robot_world.sdf",
            "sha256": sha256(world.read_bytes()).hexdigest(),
            "service": "/world/ump_conformance/control",
        },
        "checks": {
            "gazebo_world_ready": True,
            "three_action_servers_ready": True,
            "native_action_success": True,
            "ump_adapter_success": True,
            "native_cancellation": True,
            "capability_rejection": True,
        },
        "results": [
            result("smoke-inspect", "succeeded", completed=True),
            result(
                "smoke-adapter-carry",
                "succeeded",
                robot_id="robot-humanoid-1",
                completed=True,
            ),
            result("smoke-carry", "succeeded", completed=True),
            result("smoke-place", "cancelled"),
            result("smoke-reject", "rejected"),
        ],
        "environment": {
            "hostname": "native-runner",
            "platform": "Linux",
            "python": "3.13",
            "ros_distro": "jazzy",
            "repository_revision": REVISION,
        },
    }


class Ros2EvidenceTests(unittest.TestCase):
    def write_fixture(self, directory: Path) -> tuple[Path, Path, dict]:
        world = directory / "world.sdf"
        world.write_text("<sdf version='1.10'/>", encoding="utf-8")
        document = smoke_report(world)
        report = directory / "report.json"
        report.write_text(json.dumps(document), encoding="utf-8")
        return report, world, document

    def test_validates_outcomes_world_and_revision(self):
        with TemporaryDirectory() as name:
            report, world, _ = self.write_fixture(Path(name))
            result = validate_ros2_smoke_report(
                report, world_path=world, expected_revision=REVISION
            )

        self.assertTrue(result["valid"])
        self.assertEqual(result["checks_verified"], 6)
        self.assertEqual(result["results_verified"], 5)

    def test_rejects_forged_status_and_incomplete_check_set(self):
        with TemporaryDirectory() as name:
            directory = Path(name)
            report, world, document = self.write_fixture(directory)
            document["results"][3]["status"] = "succeeded"
            report.write_text(json.dumps(document))
            with self.assertRaisesRegex(Ros2EvidenceValidationError, "status"):
                validate_ros2_smoke_report(report, world_path=world)

            document = smoke_report(world)
            document["checks"].pop("capability_rejection")
            report.write_text(json.dumps(document))
            with self.assertRaisesRegex(Ros2EvidenceValidationError, "incomplete"):
                validate_ros2_smoke_report(report, world_path=world)

    def test_rejects_wrong_world_revision_and_ros_distribution(self):
        with TemporaryDirectory() as name:
            directory = Path(name)
            report, world, document = self.write_fixture(directory)
            world.write_text("changed")
            with self.assertRaisesRegex(Ros2EvidenceValidationError, "digest"):
                validate_ros2_smoke_report(report, world_path=world)

            report, world, document = self.write_fixture(directory)
            with self.assertRaisesRegex(Ros2EvidenceValidationError, "revision"):
                validate_ros2_smoke_report(
                    report, world_path=world, expected_revision="different"
                )

            document["environment"]["ros_distro"] = "rolling"
            report.write_text(json.dumps(document))
            with self.assertRaisesRegex(Ros2EvidenceValidationError, "Jazzy"):
                validate_ros2_smoke_report(report)

    def test_cli_emits_machine_readable_result_and_controlled_error(self):
        with TemporaryDirectory() as name:
            report, world, _ = self.write_fixture(Path(name))
            output = StringIO()
            with redirect_stdout(output):
                status = ros2_evidence_main(
                    [str(report), "--world", str(world), "--revision", REVISION]
                )
            self.assertEqual(status, 0)
            self.assertTrue(json.loads(output.getvalue())["valid"])

            errors = StringIO()
            with redirect_stderr(errors):
                status = ros2_evidence_main([str(report), "--revision", "wrong"])
            self.assertEqual(status, 2)
            self.assertIn("revision", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
