from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.benchmark import _benchmark_credentials
from ump.cli import node_main
from ump.credentials import SqliteCredentialStore
from ump.models import (
    Assignment,
    Availability,
    Capability,
    Mode,
    Outcome,
    RobotManifest,
    RobotState,
    Safety,
)
from ump.node import ParticipantService, load_adapter
from ump.transport import make_envelope


class Adapter:
    def manifest(self):
        return RobotManifest("robot-1", "Example", "R1", "mobile_robot", ())

    def state(self):
        return RobotState(
            "robot-1",
            Mode.IDLE,
            Safety.NORMAL,
            "Waiting",
            "Publish awareness",
            0.0,
            "Robot one is idle.",
        )

    def accept(self, assignment: Assignment):
        return Outcome(
            assignment.assignment_id,
            "robot-1",
            False,
            "No capabilities are advertised",
        )

    def cancel(self, assignment_id, reason):
        del assignment_id, reason
        return False, "No active UMP assignment"


class PreflightAdapter(Adapter):
    def manifest(self):
        return RobotManifest(
            "benchmark-client", "Example", "P1", "mobile_robot", ()
        )

    def state(self):
        return RobotState(
            "benchmark-client",
            Mode.IDLE,
            Safety.NORMAL,
            "Waiting",
            "Validate deployment preflight",
            0.0,
            "Benchmark client adapter is ready for preflight.",
        )


def create_preflight_adapter(_config_path=None):
    return PreflightAdapter()


class MutableSafetyAdapter(Adapter):
    def __init__(self):
        self.safety = Safety.NORMAL
        self.state_reads = 0

    def state(self):
        self.state_reads += 1
        return RobotState(
            "robot-1",
            Mode.IDLE,
            self.safety,
            "Waiting",
            "Publish awareness",
            0.0,
            f"Robot one safety state is {self.safety.value}.",
        )


class MutableAvailabilityAdapter(Adapter):
    def __init__(self):
        self.availability = Availability.AVAILABLE

    def manifest(self):
        return RobotManifest(
            "robot-1",
            "Example",
            "R1",
            "mobile_robot",
            (
                Capability(
                    "example.observe/v1",
                    "Observe a bounded semantic target",
                    {"type": "object"},
                    {"type": "object"},
                    self.availability,
                ),
            ),
        )


class CommunicationPolicyAdapter(Adapter):
    def __init__(self):
        self.communication_events = []

    def communication_lost(self, stale_peer_ids, observed_at_ms):
        self.communication_events.append(("lost", stale_peer_ids, observed_at_ms))

    def communication_restored(self, restored_peer_ids, observed_at_ms):
        self.communication_events.append(
            ("restored", restored_peer_ids, observed_at_ms)
        )


class Resource:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

    def authorize(self, *arguments):
        del arguments

    def claim(self, *arguments):
        raise AssertionError("read-only node should receive no assignments")


class Bus:
    def __init__(self, robot_id="robot-1"):
        self.robot_id = robot_id
        self.handlers = {}
        self.messages = []
        self.started = False
        self.stopped = False

    def subscribe(self, message_type, handler):
        self.handlers.setdefault(message_type, []).append(handler)

    def publish(self, envelope):
        self.messages.append(envelope)
        for handler in self.handlers.get(envelope.message_type, ()):
            handler(envelope)

    def start(self):
        self.started = True
        return "127.0.0.1", 7443

    def stop(self):
        self.stopped = True


class BoundedStop:
    def __init__(self, publications):
        self.publications = publications
        self.waits = 0

    def wait(self, interval):
        self.asserted_interval = interval
        self.waits += 1
        return self.waits > self.publications


class ParticipantServiceTests(unittest.TestCase):
    def preflight_fixture(self, root: Path):
        ca, _, _, certificate, key = _benchmark_credentials(root / "issued")
        credential_database = root / "credentials.sqlite3"
        store = SqliteCredentialStore(
            "benchmark-client", credential_database, root / "managed"
        )
        now_ms = int(time.time() * 1_000)
        generation = store.enroll(certificate, key, ca, now_ms)
        generation = store.activate(generation.generation, now_ms)
        store.close()
        network_databases = {
            name: root / f"network-{name}.sqlite3"
            for name in ("replay", "inbox", "outbox")
        }
        network = root / "network.json"
        network.write_text(
            json.dumps(
                {
                    "robot_id": "benchmark-client",
                    "bind_host": "127.0.0.1",
                    "bind_port": 0,
                    "certificate_path": str(generation.certificate_path),
                    "private_key_path": str(generation.private_key_path),
                    "ca_path": str(generation.ca_path),
                    "replay_database_path": str(network_databases["replay"]),
                    "inbox_database_path": str(network_databases["inbox"]),
                    "outbox_database_path": str(network_databases["outbox"]),
                    "peers": [],
                }
            )
        )
        arguments = [
            "--network",
            str(network),
            "--adapter",
            "tests.test_node:create_preflight_adapter",
            "--assignment-database",
            str(root / "assignments.sqlite3"),
            "--authority-database",
            str(root / "authority.sqlite3"),
            "--credential-database",
            str(credential_database),
            "--credential-directory",
            str(root / "managed"),
            "--preflight",
        ]
        return arguments, network_databases

    def test_node_preflight_validates_without_creating_runtime_databases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "issued").mkdir()
            arguments, network_databases = self.preflight_fixture(root)
            output = StringIO()
            with redirect_stdout(output):
                status = node_main(arguments)

            self.assertEqual(status, 0)
            report = json.loads(output.getvalue())
            self.assertTrue(report["valid"])
            self.assertEqual(report["mode"], "preflight")
            self.assertEqual(len(report["checks"]), 9)
            self.assertFalse((root / "assignments.sqlite3").exists())
            self.assertFalse((root / "authority.sqlite3").exists())
            self.assertTrue(all(not path.exists() for path in network_databases.values()))

    def test_node_preflight_rejects_database_collision_and_loose_private_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "issued").mkdir()
            arguments, _ = self.preflight_fixture(root)
            assignment_index = arguments.index("--assignment-database") + 1
            authority_index = arguments.index("--authority-database") + 1
            arguments[authority_index] = arguments[assignment_index]
            errors = StringIO()
            with redirect_stderr(errors):
                status = node_main(arguments)
            self.assertEqual(status, 2)
            self.assertIn("unique by role", errors.getvalue())

            arguments, _ = self.preflight_fixture(root)
            network = json.loads(Path(arguments[1]).read_text())
            Path(network["private_key_path"]).chmod(0o644)
            errors = StringIO()
            with redirect_stderr(errors):
                status = node_main(arguments)
            self.assertEqual(status, 2)
            self.assertIn("group/world", errors.getvalue())

    def test_service_announces_before_periodic_state_and_closes_resources(self):
        bus = Bus()
        journal = Resource()
        authority = Resource()
        clock_values = iter((1_000, 1_100, 1_200))
        service = ParticipantService(
            Adapter(),
            bus,
            journal,
            authority,
            state_hz=2.0,
            clock_ms=lambda: next(clock_values),
        )
        self.assertEqual(service.start(), ("127.0.0.1", 7443))
        stop = BoundedStop(2)
        service.run(stop)
        service.close()
        service.close()

        self.assertEqual(
            [message.message_type for message in bus.messages],
            ["manifest", "state", "state", "state"],
        )
        self.assertEqual(stop.asserted_interval, 0.5)
        self.assertTrue(bus.stopped)
        self.assertTrue(journal.closed)
        self.assertTrue(authority.closed)

    def test_service_rejects_adapter_network_identity_mismatch(self):
        with self.assertRaisesRegex(ValueError, "identities differ"):
            ParticipantService(Adapter(), Bus("robot-2"), Resource(), Resource())

    def test_required_peers_require_manufacturer_communication_policy(self):
        with self.assertRaisesRegex(TypeError, "CommunicationLossHandler"):
            ParticipantService(
                Adapter(),
                Bus(),
                Resource(),
                Resource(),
                required_peer_ids=("peer-1",),
            )

    def test_required_peer_loss_and_restoration_reach_adapter_policy(self):
        adapter = CommunicationPolicyAdapter()
        bus = Bus()
        service = ParticipantService(
            adapter,
            bus,
            Resource(),
            Resource(),
            required_peer_ids=("peer-1",),
            communication_check_interval_s=60.0,
            clock_ms=lambda: 1_000,
        )
        service.start()
        self.assertEqual(
            adapter.communication_events,
            [("lost", ("peer-1",), 1_000)],
        )
        bus.publish(
            make_envelope(
                "manifest",
                "peer-1",
                "peer-session",
                1,
                1_000,
                {
                    "robot_id": "peer-1",
                    "manufacturer": "Peer Manufacturer",
                    "model": "P1",
                    "robot_class": "mobile_robot",
                    "adapter_version": "1.0.0",
                    "capabilities": [],
                },
            )
        )
        bus.publish(
            make_envelope(
                "state",
                "peer-1",
                "peer-session",
                2,
                1_000,
                {
                    "robot_id": "peer-1",
                    "mode": "idle",
                    "safety": "normal",
                    "activity": "Waiting",
                    "intent": "Publish state",
                    "progress": 0.0,
                    "summary": "Peer one is available.",
                    "fresh_for_ms": 100,
                },
            )
        )
        self.assertEqual(service.evaluate_communication(1_050), ())
        service.close()
        self.assertEqual(
            adapter.communication_events[-1],
            ("restored", ("peer-1",), 1_050),
        )

    def test_safety_transition_uses_priority_stream_before_operational_state(self):
        adapter = MutableSafetyAdapter()
        bus = Bus()
        service = ParticipantService(adapter, bus, Resource(), Resource())
        service.start()
        adapter.safety = Safety.PROTECTIVE_STOP
        service.run(BoundedStop(1))
        service.close()

        states = [message for message in bus.messages if message.message_type == "state"]
        self.assertEqual(
            [message.stream for message in states],
            ["operational", "safety", "operational"],
        )
        self.assertEqual(states[1].payload["safety"], "protective_stop")
        self.assertEqual(states[1].sequence, 1)
        self.assertEqual(states[2].sequence, 3)
        self.assertEqual(adapter.state_reads, 2)

    def test_changed_capability_availability_is_published_before_state(self):
        adapter = MutableAvailabilityAdapter()
        bus = Bus()
        service = ParticipantService(adapter, bus, Resource(), Resource())
        service.start()
        adapter.availability = Availability.UNAVAILABLE
        service.run(BoundedStop(1))
        service.run(BoundedStop(1))
        service.close()

        self.assertEqual(
            [message.message_type for message in bus.messages],
            ["manifest", "state", "manifest", "state", "state"],
        )
        changed = bus.messages[2]
        self.assertEqual(
            changed.payload["capabilities"][0]["availability"], "unavailable"
        )
        self.assertEqual(changed.sequence, 3)

    def test_service_checks_credential_health_at_start_and_before_publication(self):
        checks = []

        def health_check():
            checks.append("checked")

        service = ParticipantService(
            Adapter(),
            Bus(),
            Resource(),
            Resource(),
            state_hz=10.0,
            health_check=health_check,
        )
        service.start()
        service.run(BoundedStop(2))
        service.close()
        self.assertEqual(checks, ["checked", "checked", "checked"])

    def test_failed_startup_health_check_does_not_open_network_listener(self):
        bus = Bus()

        def revoked():
            raise RuntimeError("active credential is revoked")

        service = ParticipantService(
            Adapter(), bus, Resource(), Resource(), health_check=revoked
        )
        with self.assertRaisesRegex(RuntimeError, "revoked"):
            service.start()
        service.close()
        self.assertFalse(bus.started)

    def test_documented_example_factory_loads_as_adapter(self):
        adapter = load_adapter("examples.read_only_adapter:create_adapter")
        self.assertEqual(adapter.manifest().capabilities, ())

    def test_adapter_loader_rejects_invalid_specification(self):
        with self.assertRaisesRegex(ValueError, "module:factory"):
            load_adapter("missing-separator")


if __name__ == "__main__":
    unittest.main()
