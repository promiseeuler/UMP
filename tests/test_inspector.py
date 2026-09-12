from contextlib import redirect_stderr
import base64
from hashlib import sha256
from io import StringIO
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.cli import inspector_main
from ump.inspector import (
    InspectorRecorder,
    InspectorServer,
    InspectorStore,
    InspectorStoreError,
    ReadOnlyInspectorStore,
)
from ump.integrations import FieldMapping, FieldMappingStatus, IntegrationProvenance, MappingReport
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
    def test_integration_provenance_is_separate_truthful_read_model(self):
        with tempfile.TemporaryDirectory() as directory:
            store = InspectorStore(Path(directory) / "inspector.sqlite3")
            report = MappingReport(
                "massrobotics", "1.0", "external_to_ump", "amr-1", 100,
                (FieldMapping("battery", FieldMappingStatus.MAPPED),),
            )
            store.record_integration(
                "robot-1", IntegrationProvenance("massrobotics", "1.0", "amr-1", 100, report)
            )
            robot = store.snapshot()["robots"][0]
            self.assertEqual(robot["integration"]["source_standard"], "massrobotics")
            self.assertTrue(robot["integration"]["report"]["passed"])
            store.close()

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
        self.assertEqual(snapshot["robots"][0]["state_observed_at_ms"], 1_001)
        self.assertEqual(snapshot["robots"][0]["reconnect_count"], 0)

    def test_lab_events_remain_separate_from_observed_robot_telemetry(self):
        self.store.record_lab_event(
            "network_partition",
            "injected",
            2_000,
            robot_id="robot-inspector-1",
            detail={"duration_ms": 500},
        )
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot["event_count"], 0)
        self.assertEqual(snapshot["robots"], [])
        self.assertEqual(snapshot["lab_events"][0]["event_type"], "network_partition")

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
                self.assertIn("Fleet awareness", body)
                self.assertIn("Read only", body)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
        finally:
            server.stop()

    def test_remote_binding_is_refused(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            InspectorServer(self.store, "0.0.0.0", 0)

    def test_remote_binding_requires_authentication_and_tls(self):
        with self.assertRaisesRegex(ValueError, "authentication and TLS"):
            InspectorServer(self.store, "0.0.0.0", 0, allow_remote=True)

    def test_optional_basic_token_protects_local_inspector(self):
        token = "correct-horse-battery-staple-token"
        server = InspectorServer(self.store, port=0, auth_token=token)
        address = server.start()
        url = f"http://{address.host}:{address.port}/api/snapshot"
        try:
            with self.assertRaises(HTTPError) as failure:
                urlopen(url, timeout=2)
            self.assertEqual(failure.exception.code, 401)
            encoded = base64.b64encode(f"ump:{token}".encode()).decode()
            request = Request(url, headers={"Authorization": f"Basic {encoded}"})
            with urlopen(request, timeout=2) as response:
                self.assertEqual(json.loads(response.read())["event_count"], 0)
        finally:
            server.stop()

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

    def test_read_only_store_serves_existing_data_without_mutation(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            path = directory / "events.sqlite3"
            writer = InspectorStore(path)
            for item in messages():
                writer.record(item)
            writer.close()
            before = sha256(path.read_bytes()).hexdigest()
            files_before = sorted(item.name for item in directory.iterdir())

            reader = ReadOnlyInspectorStore(path)
            snapshot = reader.snapshot()
            with self.assertRaisesRegex(InspectorStoreError, "cannot record"):
                reader.record(messages()[0])
            reader.close()

            self.assertEqual(snapshot["event_count"], 2)
            self.assertEqual(before, sha256(path.read_bytes()).hexdigest())
            self.assertEqual(
                files_before, sorted(item.name for item in directory.iterdir())
            )

    def test_read_only_store_rejects_missing_and_incompatible_databases(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            missing = directory / "missing" / "events.sqlite3"
            with self.assertRaisesRegex(InspectorStoreError, "does not exist"):
                ReadOnlyInspectorStore(missing)
            self.assertFalse(missing.parent.exists())

            incompatible = directory / "incompatible.sqlite3"
            connection = sqlite3.connect(incompatible)
            connection.execute("CREATE TABLE protocol_events (message_id TEXT)")
            connection.close()
            with self.assertRaisesRegex(InspectorStoreError, "missing columns"):
                ReadOnlyInspectorStore(incompatible)

    def test_inspector_cli_fails_closed_without_creating_database(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "absent" / "events.sqlite3"
            errors = StringIO()
            with redirect_stderr(errors):
                self.assertEqual(inspector_main(["--database", str(path)]), 2)
            self.assertIn("does not exist", errors.getvalue())
            self.assertFalse(path.parent.exists())


if __name__ == "__main__":
    unittest.main()
