from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.models import Assignment, Mode, Outcome, RobotManifest, RobotState, Safety
from ump.node import ParticipantService, load_adapter


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
