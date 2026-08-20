from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ump.authority import AllowAllAuthorizer
from ump.collaboration import Coordinator
from ump.cli import coordinator_main
from ump.coordinator_node import (
    CoordinatorService,
    ParticipantContextTimeout,
    parse_authority_leases,
)
from ump.coordinator_store import CoordinatorStore, RunStatus
from ump.demo import WarehousePlanner
from ump.models import RobotManifest, SharedGoal
from ump.runtime import Participant, Registry
from ump.simulation import SimulatedRobot
from ump.transport import InMemoryBus
from ump.vocabulary import standard_capability


class ServiceBus(InMemoryBus):
    robot_id = "owner-coordinator-1"

    def __init__(self):
        super().__init__()
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True
        return "127.0.0.1", 7443

    def stop(self):
        self.stopped = True


def participants(bus):
    manifests = (
        RobotManifest(
            "robot-humanoid-1",
            "Example Humanoid Co",
            "H1",
            "humanoid",
            (standard_capability("ump.material.carry/v1"),),
        ),
        RobotManifest(
            "robot-quadruped-1",
            "Example Quadruped Co",
            "Q1",
            "quadruped",
            (standard_capability("ump.navigation.inspect-route/v1"),),
        ),
        RobotManifest(
            "robot-mobile-arm-1",
            "Example Manipulation Co",
            "A1",
            "mobile_arm",
            (standard_capability("ump.manipulation.place/v1"),),
        ),
    )
    return tuple(
        Participant(
            SimulatedRobot(manifest),
            bus,
            authorizer=AllowAllAuthorizer(),
            clock_ms=lambda: 1_100,
        )
        for manifest in manifests
    )


class CoordinatorServiceTests(unittest.TestCase):
    def test_submits_goal_and_reports_durable_terminal_run(self):
        with TemporaryDirectory() as directory:
            bus = ServiceBus()
            registry = Registry(bus)
            store = CoordinatorStore(Path(directory) / "coordinator.sqlite3")
            coordinator = Coordinator(
                bus.robot_id,
                bus,
                registry,
                store=store,
                authority_lease_ids={
                    "robot-humanoid-1": "lease-humanoid",
                    "robot-quadruped-1": "lease-quadruped",
                    "robot-mobile-arm-1": "lease-arm",
                },
            )
            service = CoordinatorService(
                bus, coordinator, registry, clock_ms=lambda: 1_100
            )
            robots = participants(bus)
            for robot in robots:
                robot.announce(1_000)
            goal = SharedGoal(
                "goal-1",
                "Move the package to storage",
                tuple(registry.peers),
                deadline_ms=60_000,
            )

            self.assertEqual(service.start(), ("127.0.0.1", 7443))
            plan = service.submit(goal, WarehousePlanner(), participant_timeout_s=0.1)
            snapshot = service.wait_for_completion(plan.plan_id, timeout_s=0.1)

            self.assertEqual(snapshot.status, RunStatus.SUCCEEDED)
            self.assertTrue(bus.started)
            service.close()
            self.assertTrue(bus.stopped)
            output = StringIO()
            with redirect_stdout(output):
                exit_code = coordinator_main(
                    [
                        "status",
                        "--database",
                        str(Path(directory) / "coordinator.sqlite3"),
                        "--plan-id",
                        plan.plan_id,
                    ]
                )
            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "succeeded")

    def test_missing_participant_context_times_out_before_planning(self):
        bus = ServiceBus()
        registry = Registry(bus)
        coordinator = Coordinator(
            bus.robot_id,
            bus,
            registry,
            authority_lease_ids={"missing-robot": "lease-1"},
        )
        service = CoordinatorService(
            bus, coordinator, registry, clock_ms=lambda: 1_100, poll_interval_s=0.01
        )
        service.start()

        with self.assertRaisesRegex(ParticipantContextTimeout, "missing-robot"):
            service.wait_for_participants(("missing-robot",), timeout_s=0.01)
        self.assertFalse(any(message.message_type == "goal" for message in bus.trace))
        service.close()

    def test_authority_lease_arguments_are_unique_and_explicit(self):
        self.assertEqual(
            parse_authority_leases(["robot-1=lease-1", "robot-2=lease-2"]),
            {"robot-1": "lease-1", "robot-2": "lease-2"},
        )
        with self.assertRaisesRegex(ValueError, "ROBOT_ID=LEASE_ID"):
            parse_authority_leases(["lease-1"])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            parse_authority_leases(["robot-1=lease-1", "robot-1=lease-2"])


if __name__ == "__main__":
    unittest.main()
