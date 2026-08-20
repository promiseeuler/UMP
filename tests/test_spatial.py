from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest

from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.models import (
    Capability,
    Mode,
    PoseReference,
    RobotState,
    Safety,
    SensorReference,
    payload,
)
from ump.runtime import Registry
from ump.transport import InMemoryBus, encode_envelope, make_envelope


ROOT = Path(__file__).parents[1]
DIGEST = "a" * 64


class SpatialInteroperabilityTests(unittest.TestCase):
    def test_capability_numeric_fields_require_supported_explicit_units(self):
        with self.assertRaisesRegex(ValueError, "requires supported x-ump-unit"):
            Capability(
                "ump.test.measure/v1",
                "Measure a value",
                {"type": "object", "properties": {"value": {"type": "number"}}},
                {"type": "object"},
            )
        dimensionless = Capability(
            "ump.test.count/v1",
            "Count objects",
            {
                "type": "object",
                "properties": {
                    "count": {"type": "integer", "x-ump-unit": "1"}
                },
            },
            {"type": "object"},
        )
        self.assertEqual(dimensionless.input_schema["properties"]["count"]["x-ump-unit"], "1")

    def test_spatial_capability_fields_require_explicit_frame_property(self):
        schema = {
            "type": "object",
            "properties": {
                "distance": {"type": "number", "x-ump-unit": "m"},
            },
        }
        with self.assertRaisesRegex(ValueError, "requires a named string frame field"):
            Capability(
                "ump.test.distance/v1",
                "Measure distance",
                schema,
                {"type": "object"},
            )
        schema["x-ump-frame-field"] = "frame_id"
        schema["properties"]["frame_id"] = {"type": "string"}
        schema["required"] = ["distance", "frame_id"]
        capability = Capability(
            "ump.test.distance/v1",
            "Measure distance",
            schema,
            {"type": "object"},
        )
        self.assertEqual(capability.input_schema["x-ump-frame-field"], "frame_id")
    def pose(self):
        return PoseReference(
            frame_id="map/facility-a",
            position_m=(1.25, -2.5, 0.75),
            orientation_xyzw=(0.0, 0.0, 0.0, 1.0),
            observed_at_ms=1_000,
        )

    def reference(self):
        return SensorReference(
            uri="https://robot.local/objects/scan-1",
            media_type="application/vnd.ump.pointcloud+binary",
            byte_length=12_345_678,
            sha256=DIGEST,
            observed_at_ms=1_000,
            expires_at_ms=2_000,
            frame_id="sensor/lidar-front",
        )

    def state(self):
        return RobotState(
            robot_id="spatial-robot",
            mode=Mode.IDLE,
            safety=Safety.NORMAL,
            activity="Observing the shared workspace",
            intent="Publish bounded spatial metadata",
            progress=0.0,
            summary="Spatial robot is idle in the facility map frame.",
            pose=self.pose(),
            sensor_references=(self.reference(),),
        )

    def test_spatial_state_round_trips_through_schema_and_registry(self):
        bus = InMemoryBus()
        registry = Registry(bus)
        manifest = make_envelope(
            "manifest",
            "spatial-robot",
            "session-1",
            1,
            1_000,
            {
                "robot_id": "spatial-robot",
                "manufacturer": "UMP Test",
                "model": "S1",
                "robot_class": "sensor",
                "adapter_version": "0.1.0",
                "capabilities": [],
            },
        )
        state = make_envelope(
            "state",
            "spatial-robot",
            "session-1",
            2,
            1_000,
            payload(self.state()),
        )
        schema = json.loads((ROOT / "schemas" / "ump-v0.schema.json").read_text())
        Draft202012Validator(schema).validate(json.loads(encode_envelope(state)))
        bus.publish(manifest)
        bus.publish(state)
        observed = registry.peers["spatial-robot"].state
        self.assertEqual(observed, self.state())

    def test_pose_requires_frame_finite_meters_and_normalized_quaternion(self):
        with self.assertRaisesRegex(ValueError, "frame_id"):
            replace(self.pose(), frame_id="/invalid leading frame")
        with self.assertRaisesRegex(ValueError, "finite"):
            replace(self.pose(), position_m=(float("inf"), 0.0, 0.0))
        with self.assertRaisesRegex(ValueError, "normalized"):
            replace(self.pose(), orientation_xyzw=(0.0, 0.0, 0.0, 2.0))

    def test_sensor_reference_is_metadata_only_and_integrity_bound(self):
        with self.assertRaisesRegex(ValueError, "cannot embed data"):
            replace(self.reference(), uri="data:image/png;base64,AAAA")
        with self.assertRaisesRegex(ValueError, "lowercase"):
            replace(self.reference(), sha256="A" * 64)
        with self.assertRaisesRegex(ValueError, "expire before"):
            replace(self.reference(), expires_at_ms=999)

    def test_state_bounds_and_deduplicates_sensor_references(self):
        with self.assertRaisesRegex(ValueError, "must be unique"):
            replace(self.state(), sensor_references=(self.reference(), self.reference()))
        references = tuple(
            replace(self.reference(), uri=f"urn:ump:sensor:{index}")
            for index in range(33)
        )
        with self.assertRaisesRegex(ValueError, "exceeds 32"):
            replace(self.state(), sensor_references=references)


if __name__ == "__main__":
    unittest.main()
