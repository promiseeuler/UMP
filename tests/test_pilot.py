from contextlib import redirect_stderr, redirect_stdout
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ump.cli import pilot_main
from ump.pilot import PilotValidationError, validate_pilot_bundle


ROOT = Path(__file__).parents[1]
REVISION = "a" * 40


def evidence(directory: Path, name: str, content: str = "reviewed") -> dict[str, str]:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {
        "artifact": name,
        "sha256": sha256(content.encode()).hexdigest(),
    }


def conformance_evidence(directory: Path, name: str, robot_id: str) -> dict[str, str]:
    document = {
        "profile": "ump.adapter-conformance/v1",
        "repository_revision": REVISION,
        "mode": "read_only",
        "observed_at_ms": 1_000,
        "adapter": {
            "specification": "vendor.adapter:create_adapter",
            "implementation": {"path": "adapter.py", "sha256": "b" * 64},
            "module": "vendor.adapter",
            "class": "RobotAdapter",
            "distributions": [],
        },
        "robot": {
            "robot_id": robot_id,
            "manufacturer": "Example Robotics",
            "model": "Pilot Fixture",
            "robot_class": "test",
            "adapter_version": "1.0.0",
            "capabilities": [],
        },
        "subject": robot_id,
        "passed": True,
        "checks": [
            {"id": check_id, "passed": True, "description": "passed"}
            for check_id in (
                "adapter.manifest",
                "adapter.state",
                "adapter.identity",
            )
        ],
        "environment": {
            "python": "3.13",
            "implementation": "CPython",
            "platform": "Linux",
            "executable": "/usr/bin/python3",
        },
    }
    return evidence(directory, name, json.dumps(document, sort_keys=True))


def manifest(directory: Path, phase: str = "read_only") -> dict:
    participants = [
        {
            "robot_id": "physical-robot-1",
            "deployment": "physical",
            "adapter_id": "vendor-a.adapter/v1",
            "conformance_evidence": conformance_evidence(
                directory, "physical-1.json", "physical-robot-1"
            ),
        },
        {
            "robot_id": "physical-robot-2",
            "deployment": "physical",
            "adapter_id": "vendor-b.adapter/v1",
            "conformance_evidence": conformance_evidence(
                directory, "physical-2.json", "physical-robot-2"
            ),
        },
        {
            "robot_id": "simulated-robot-1",
            "deployment": "simulation",
            "adapter_id": "ump.simulation/v1",
            "conformance_evidence": conformance_evidence(
                directory, "simulation.json", "simulated-robot-1"
            ),
        },
    ]
    safety = {}
    if phase == "supervised_assignment":
        for participant in participants[:2]:
            participant["assignment_evidence"] = evidence(
                directory, f"{participant['robot_id']}-assignment.json"
            )
        safety = {
            name: evidence(directory, f"safety/{name}.txt")
            for name in (
                "adapter_safety_review",
                "bounded_work_area",
                "emergency_stop_test",
                "operator_approval",
            )
        }
    return {
        "protocol": "ump.hardware-pilot/v1",
        "repository_revision": REVISION,
        "pilot_id": "pilot-warehouse-1",
        "phase": phase,
        "site": "Supervised test facility",
        "supervisor": "operator-1",
        "conducted_at_ms": 1_000,
        "participants": participants,
        "safety_evidence": safety,
    }


def write_manifest(directory: Path, document: dict) -> Path:
    path = directory / "pilot.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


class PilotValidationTests(unittest.TestCase):
    def test_read_only_pilot_verifies_topology_and_artifact_integrity(self):
        with TemporaryDirectory() as name:
            directory = Path(name)
            report = validate_pilot_bundle(
                write_manifest(directory, manifest(directory))
            )

        self.assertTrue(report["valid"])
        self.assertEqual(
            report["validation_scope"],
            "schema_topology_conformance_and_evidence_integrity",
        )
        self.assertEqual(report["physical_participants"], 2)
        self.assertEqual(report["repository_revision"], REVISION)
        self.assertEqual(report["simulated_participants"], 1)
        self.assertEqual(report["evidence_artifacts_verified"], 3)

    def test_supervised_phase_requires_safety_and_physical_assignment_evidence(self):
        with TemporaryDirectory() as name:
            directory = Path(name)
            document = manifest(directory, "supervised_assignment")
            document["safety_evidence"].pop("emergency_stop_test")
            with self.assertRaisesRegex(PilotValidationError, "emergency_stop_test"):
                validate_pilot_bundle(write_manifest(directory, document))

            document = manifest(directory, "supervised_assignment")
            document["participants"][0].pop("assignment_evidence")
            with self.assertRaisesRegex(PilotValidationError, "physical-robot-1"):
                validate_pilot_bundle(write_manifest(directory, document))

    def test_rejects_wrong_pilot_topology(self):
        with TemporaryDirectory() as name:
            directory = Path(name)
            document = manifest(directory)
            document["participants"][1]["deployment"] = "simulation"
            with self.assertRaisesRegex(PilotValidationError, "two physical"):
                validate_pilot_bundle(write_manifest(directory, document))

    def test_read_only_phase_and_evidence_gates_cannot_be_misrepresented(self):
        with TemporaryDirectory() as name:
            directory = Path(name)
            document = manifest(directory)
            document["participants"][0]["assignment_evidence"] = evidence(
                directory, "assignment.json"
            )
            with self.assertRaisesRegex(PilotValidationError, "read-only"):
                validate_pilot_bundle(write_manifest(directory, document))

            document = manifest(directory, "supervised_assignment")
            document["safety_evidence"]["operator_approval"] = document[
                "safety_evidence"
            ]["adapter_safety_review"]
            with self.assertRaisesRegex(PilotValidationError, "distinct"):
                validate_pilot_bundle(write_manifest(directory, document))

    def test_rejects_tampered_or_escaping_evidence(self):
        with TemporaryDirectory() as name:
            directory = Path(name)
            document = manifest(directory)
            path = write_manifest(directory, document)
            (directory / "physical-1.json").write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(PilotValidationError, "digest"):
                validate_pilot_bundle(path)

            document = manifest(directory)
            document["participants"][0]["conformance_evidence"]["artifact"] = "../outside"
            with self.assertRaises(PilotValidationError):
                validate_pilot_bundle(write_manifest(directory, document))

    def test_rejects_conformance_for_another_robot_or_revision(self):
        with TemporaryDirectory() as name:
            directory = Path(name)
            document = manifest(directory)
            conformance = json.loads((directory / "physical-1.json").read_text())
            conformance["repository_revision"] = "b" * 40
            document["participants"][0]["conformance_evidence"] = evidence(
                directory,
                "physical-1.json",
                json.dumps(conformance, sort_keys=True),
            )
            with self.assertRaisesRegex(PilotValidationError, "revision differs"):
                validate_pilot_bundle(write_manifest(directory, document))

            document = manifest(directory)
            conformance = json.loads((directory / "physical-1.json").read_text())
            conformance["robot"]["robot_id"] = "different-robot"
            document["participants"][0]["conformance_evidence"] = evidence(
                directory,
                "physical-1.json",
                json.dumps(conformance, sort_keys=True),
            )
            with self.assertRaisesRegex(PilotValidationError, "identity differs"):
                validate_pilot_bundle(write_manifest(directory, document))

    def test_cli_emits_machine_readable_report_and_failure_status(self):
        with TemporaryDirectory() as name:
            directory = Path(name)
            path = write_manifest(directory, manifest(directory))
            output = StringIO()
            with redirect_stdout(output):
                status = pilot_main(["validate", str(path)])
            self.assertEqual(status, 0)
            self.assertTrue(json.loads(output.getvalue())["valid"])

            (directory / "physical-1.json").write_text("changed", encoding="utf-8")
            errors = StringIO()
            with redirect_stderr(errors):
                status = pilot_main(["validate", str(path)])
            self.assertEqual(status, 2)
            self.assertIn("digest", errors.getvalue())

    def test_public_and_packaged_schemas_are_identical(self):
        public = json.loads(
            (ROOT / "schemas" / "ump-hardware-pilot-v1.schema.json").read_text()
        )
        packaged = json.loads(
            (ROOT / "src" / "ump" / "pilot_data" / "v1" / "schema.json").read_text()
        )
        self.assertEqual(public, packaged)


if __name__ == "__main__":
    unittest.main()
