from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.authority import AllowAllAuthorizer
from ump.conformance import AdapterConformanceHarness
from ump.cli import vocabulary_main
from ump.models import Assignment, AssignmentStatus, Outcome, PlanStep, RobotManifest, payload
from ump.runtime import Participant
from ump.simulation import SimulatedRobot
from ump.transport import InMemoryBus, make_envelope
from ump.vocabulary import standard_capabilities, standard_capability


class InvalidOutputAdapter(SimulatedRobot):
    def accept(self, assignment):
        return Outcome(
            assignment.assignment_id,
            self.manifest().robot_id,
            True,
            "Controller claimed success without required structured evidence",
            outputs={},
        )


class VocabularyTests(unittest.TestCase):
    def test_catalog_has_three_versioned_high_level_capabilities(self):
        capabilities = standard_capabilities()
        self.assertEqual(
            {item.name for item in capabilities},
            {
                "ump.navigation.inspect-route/v1",
                "ump.material.carry/v1",
                "ump.manipulation.place/v1",
            },
        )
        for item in capabilities:
            Draft202012Validator.check_schema(item.input_schema)
            Draft202012Validator.check_schema(item.output_schema)

    def test_carry_schema_accepts_semantic_request_and_rejects_vendor_guesswork(self):
        capability = standard_capability("ump.material.carry/v1")
        validator = Draft202012Validator(capability.input_schema)
        validator.validate(
            {
                "object": "package-1",
                "destination": "storage",
                "payload_mass": 2.5,
                "keep_upright": True,
            }
        )
        with self.assertRaises(ValidationError):
            validator.validate({"object": "package-1", "vendor_speed": 7})

    def test_place_schema_requires_frame_for_optional_metric_position(self):
        capability = standard_capability("ump.manipulation.place/v1")
        Draft202012Validator(capability.input_schema).validate(
            {
                "object": "package-1",
                "target": "shelf-a",
                "placement": {
                    "frame_id": "warehouse",
                    "x": 1.0,
                    "y": 2.0,
                    "z": 0.8,
                },
            }
        )

    def test_conformance_rejects_missing_required_structured_output(self):
        capability = standard_capability("ump.material.carry/v1")
        manifest = RobotManifest(
            "robot-1", "Example", "M1", "mobile", (capability,)
        )
        assignment = Assignment(
            "assignment-1",
            "goal-1",
            "plan-1",
            PlanStep(
                "carry",
                "Carry package",
                "robot-1",
                capability.name,
                {"object": "package-1", "destination": "storage"},
                "Package arrives",
            ),
        )
        report = AdapterConformanceHarness().exercise(
            InvalidOutputAdapter(manifest),
            (assignment,),
            allow_native_execution=True,
        )
        self.assertFalse(report.passed)

    def test_runtime_turns_invalid_native_output_into_unknown(self):
        capability = standard_capability("ump.material.carry/v1")
        manifest = RobotManifest(
            "robot-1", "Example", "M1", "mobile", (capability,)
        )
        assignment = Assignment(
            "assignment-1",
            "goal-1",
            "plan-1",
            PlanStep(
                "carry",
                "Carry package",
                "robot-1",
                capability.name,
                {"object": "package-1", "destination": "storage"},
                "Package arrives",
            ),
        )
        bus = InMemoryBus()
        participant = Participant(
            InvalidOutputAdapter(manifest),
            bus,
            authorizer=AllowAllAuthorizer(),
            clock_ms=lambda: 2_000,
        )
        participant.announce(1_000)
        bus.publish(
            make_envelope(
                "assignment",
                "coordinator-1",
                "session-1",
                1,
                1_100,
                payload(assignment),
            )
        )
        outcome = next(
            item for item in reversed(bus.trace) if item.message_type == "outcome"
        )
        self.assertEqual(outcome.payload["status"], AssignmentStatus.UNKNOWN.value)
        self.assertEqual(outcome.payload["outputs"], {})
        participant.close()

    def test_outcome_outputs_are_bounded_and_json_serializable(self):
        with self.assertRaisesRegex(ValueError, "JSON serializable"):
            Outcome("assignment-1", "robot-1", True, "Done", outputs={"bad": object()})
        with self.assertRaisesRegex(ValueError, "exceed"):
            Outcome("assignment-1", "robot-1", True, "Done", outputs={"data": "x" * 17_000})

    def test_vocabulary_cli_lists_and_validates_machine_readable_contracts(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(vocabulary_main(["list"]), 0)
        listing = json.loads(output.getvalue())
        self.assertEqual(listing["vocabulary"], "ump.standard/v1")
        self.assertEqual(len(listing["capabilities"]), 3)

        with tempfile.TemporaryDirectory() as directory:
            document = Path(directory) / "carry.json"
            document.write_text('{"object":"package-1","destination":"storage"}')
            output = io.StringIO()
            with redirect_stdout(output):
                result = vocabulary_main(
                    ["validate-input", "ump.material.carry/v1", str(document)]
                )
            self.assertEqual(result, 0)
            self.assertTrue(json.loads(output.getvalue())["valid"])
            document.write_text('{"object":"package-1"}')
            self.assertEqual(
                vocabulary_main(
                    ["validate-input", "ump.material.carry/v1", str(document)]
                ),
                2,
            )


if __name__ == "__main__":
    unittest.main()
