import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.inspector import InspectorRecorder, InspectorServer, InspectorStore
from ump.models import Mode, RobotManifest, RobotState, Safety, payload
from ump.transport import InMemoryBus, make_envelope


def messages():
    manifest = RobotManifest(
        "robot-inspector-1",
        "Example Robotics",
        "I1",
        "mobile_base",
        (),
    )
    state = RobotState(
        manifest.robot_id,
        Mode.IDLE,
        Safety.NORMAL,
        "Waiting for work",
        "Remain observable",
        0.0,
        "Inspector fixture robot is idle.",
    )
    return (
        make_envelope(
            "manifest",
            manifest.robot_id,
            "inspector-session",
            1,
            1_000,
            payload(manifest),
        ),
        make_envelope(
            "state",
            state.robot_id,
            "inspector-session",
            2,
            1_001,
            payload(state),
        ),
    )


class InspectorTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = InspectorStore(
            Path(self.temporary_directory.name) / "inspector.sqlite3"
        )

    def tearDown(self):
        self.store.close()
        self.temporary_directory.cleanup()

    def test_recorder_deduplicates_and_builds_latest_robot_read_model(self):
        bus = InMemoryBus()
        InspectorRecorder(bus, self.store)
        manifest, state = messages()
        bus.publish(manifest)
        bus.publish(state)
        bus.publish(state)
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot["event_count"], 2)
        self.assertEqual(snapshot["robots"][0]["manifest"]["model"], "I1")
        self.assertEqual(snapshot["robots"][0]["state"]["mode"], "idle")

    def test_http_api_and_static_ui_have_restrictive_headers(self):
        for item in messages():
            self.store.record(item)
        server = InspectorServer(self.store, port=0)
        address = server.start()
        try:
            with urlopen(
                f"http://{address.host}:{address.port}/api/snapshot?limit=10",
                timeout=2,
            ) as response:
                document = json.loads(response.read())
                self.assertEqual(document["event_count"], 2)
                self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
                self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
            with urlopen(f"http://{address.host}:{address.port}/", timeout=2) as response:
                body = response.read().decode()
                self.assertIn("UMP Inspector", body)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
        finally:
            server.stop()

    def test_remote_binding_is_refused(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            InspectorServer(self.store, "0.0.0.0", 0)

    def test_recorder_failure_does_not_reclassify_message_delivery(self):
        class FailingStore:
            def record(self, _envelope):
                raise OSError("recording disk unavailable")

        bus = InMemoryBus()
        delivered = []
        bus.subscribe("manifest", delivered.append)
        recorder = InspectorRecorder(bus, FailingStore())
        manifest, _ = messages()

        bus.publish(manifest)

        self.assertEqual([item.message_id for item in delivered], [manifest.message_id])
        self.assertEqual(delivered[0].payload["robot_id"], "robot-inspector-1")
        with self.assertRaisesRegex(RuntimeError, "recording failed"):
            recorder.require_healthy()


if __name__ == "__main__":
    unittest.main()
