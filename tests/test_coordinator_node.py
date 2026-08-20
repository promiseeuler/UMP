from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

from ump.authority import AllowAllAuthorizer
from ump.collaboration import Coordinator
from ump.cli import coordinator_main
from ump.coordinator_node import (
    CoordinatorService,
    ParticipantContextTimeout,
    RunCompletionTimeout,
    parse_authority_leases,
)
from ump.coordinator_store import CoordinatorStore, RunStatus
from ump.demo import WarehousePlanner
from ump.models import (
    Assignment,
    AssignmentStatus,
    Plan,
    PlanStep,
    RobotManifest,
    SharedGoal,
)
from ump.network import (
    PeerEndpoint,
    SqliteReplayProtector,
    TlsNetworkBus,
    create_client_context,
    create_server_context,
)
from ump.delivery import SqliteOutbox
from ump.runtime import Participant, Registry
from ump.simulation import SimulatedRobot
from ump.transport import InMemoryBus
from ump.vocabulary import standard_capability


def run_openssl(*arguments, directory):
    subprocess.run(
        ["openssl", *arguments],
        cwd=directory,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def create_ca(directory: Path) -> tuple[Path, Path]:
    key = directory / "ca.key"
    certificate = directory / "ca.crt"
    run_openssl(
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        str(key),
        "-out",
        str(certificate),
        "-days",
        "1",
        "-sha256",
        "-subj",
        "/CN=UMP Coordinator Test CA",
        directory=directory,
    )
    return certificate, key


def create_leaf(
    directory: Path, ca_certificate: Path, ca_key: Path, identity: str
) -> tuple[Path, Path]:
    key = directory / f"{identity}.key"
    request = directory / f"{identity}.csr"
    certificate = directory / f"{identity}.crt"
    extensions = directory / f"{identity}.ext"
    extensions.write_text(f"subjectAltName=URI:urn:ump:robot:{identity}\n")
    run_openssl(
        "req",
        "-new",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        str(key),
        "-out",
        str(request),
        "-subj",
        f"/CN={identity}",
        directory=directory,
    )
    run_openssl(
        "x509",
        "-req",
        "-in",
        str(request),
        "-CA",
        str(ca_certificate),
        "-CAkey",
        str(ca_key),
        "-CAcreateserial",
        "-out",
        str(certificate),
        "-days",
        "1",
        "-sha256",
        "-extfile",
        str(extensions),
        directory=directory,
    )
    return certificate, key


def tls_bus(
    directory: Path,
    identity: str,
    certificate: Path,
    key: Path,
    ca_certificate: Path,
) -> TlsNetworkBus:
    return TlsNetworkBus(
        identity,
        "127.0.0.1",
        0,
        create_server_context(certificate, key, ca_certificate),
        create_client_context(certificate, key, ca_certificate),
        replay_protector=SqliteReplayProtector(directory / f"{identity}-replay.sqlite3"),
        inbox_path=directory / f"{identity}-inbox.sqlite3",
        outbox=SqliteOutbox(directory / f"{identity}-outbox.sqlite3"),
    )


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
    def test_submits_and_waits_for_a_goal_batch(self):
        bus = ServiceBus()
        registry = Registry(bus)
        coordinator = Coordinator(
            bus.robot_id,
            bus,
            registry,
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
        participant_ids = tuple(registry.peers)
        goals = (
            SharedGoal(
                "goal-batch-1",
                "Move the first package to storage",
                participant_ids,
                deadline_ms=60_000,
            ),
            SharedGoal(
                "goal-batch-2",
                "Move the second package to storage",
                participant_ids,
                deadline_ms=60_000,
            ),
        )
        service.start()

        plans = service.submit_many(
            goals, WarehousePlanner(), participant_timeout_s=0.1
        )
        snapshots = service.wait_for_completions(
            tuple(plan.plan_id for plan in plans), timeout_s=0.1
        )

        self.assertEqual(len(plans), 2)
        self.assertEqual(
            tuple(snapshot.goal_id for snapshot in snapshots),
            ("goal-batch-1", "goal-batch-2"),
        )
        self.assertTrue(
            all(snapshot.status is RunStatus.SUCCEEDED for snapshot in snapshots)
        )
        service.close()
        for robot in robots:
            robot.close()

    def test_service_queries_unknown_work_and_waits_for_known_resolution(self):
        bus = ServiceBus()
        registry = Registry(bus)
        coordinator = Coordinator(
            bus.robot_id, bus, registry, require_authority=False
        )
        robot_manifest = RobotManifest(
            "robot-1",
            "Example Robotics",
            "R1",
            "mobile_robot",
            (standard_capability("ump.navigation.inspect-route/v1"),),
        )
        participant = Participant(
            SimulatedRobot(robot_manifest),
            bus,
            authorizer=AllowAllAuthorizer(),
            clock_ms=lambda: 1_100,
        )
        participant.announce(1_000)
        goal = SharedGoal("goal-reconcile-1", "Inspect the route", ("robot-1",))
        step = PlanStep(
            "inspect",
            "Inspect the route",
            "robot-1",
            "ump.navigation.inspect-route/v1",
            {"from": "intake", "to": "storage"},
            "A route report is available",
        )
        plan = Plan("plan-reconcile-1", goal.goal_id, "planner-1", "Inspect", (step,))
        assignment = Assignment("assignment-1", goal.goal_id, plan.plan_id, step)
        coordinator.store.create_run(goal, plan, (assignment,), 1_000)
        coordinator.store.mark_dispatched(assignment.assignment_id, 1_001)
        coordinator.store.record_outcome(
            assignment.assignment_id, AssignmentStatus.UNKNOWN, 1_002
        )
        service = CoordinatorService(
            bus, coordinator, registry, clock_ms=lambda: 1_100, poll_interval_s=0.01
        )
        service.start()

        queried = service.reconcile(plan.plan_id, participant_timeout_s=0.1)

        self.assertEqual(queried, (assignment.assignment_id,))
        self.assertTrue(
            any(item.message_type == "assignment_query" for item in bus.trace)
        )
        with self.assertRaisesRegex(RunCompletionTimeout, "unresolved"):
            service.wait_for_resolution(plan.plan_id, timeout_s=0.01)
        coordinator.store.reconcile_outcome(
            assignment.assignment_id, AssignmentStatus.SUCCEEDED, 1_200
        )
        self.assertEqual(
            service.wait_for_resolution(plan.plan_id, timeout_s=0.1).status,
            RunStatus.SUCCEEDED,
        )
        service.close()
        participant.close()

    def test_service_publishes_owner_cancellation_request(self):
        bus = ServiceBus()
        registry = Registry(bus)
        coordinator = Coordinator(
            bus.robot_id, bus, registry, require_authority=False
        )
        goal = SharedGoal("goal-cancel-1", "Perform bounded work", ("robot-1",))
        step = PlanStep(
            "work",
            "Perform bounded work",
            "robot-1",
            "ump.test.work/v1",
            {},
            "Work reaches a terminal state",
        )
        plan = Plan("plan-cancel-1", goal.goal_id, "planner-1", "Work", (step,))
        assignment = Assignment("assignment-1", goal.goal_id, plan.plan_id, step)
        coordinator.store.create_run(goal, plan, (assignment,), 1_000)
        coordinator.store.mark_dispatched(assignment.assignment_id, 1_001)
        service = CoordinatorService(
            bus, coordinator, registry, clock_ms=lambda: 1_100
        )
        service.start()

        requested = service.cancel(plan.plan_id, "Owner stopped supervised work")

        self.assertEqual(requested, (assignment.assignment_id,))
        cancellation = next(
            item for item in bus.trace if item.message_type == "cancellation_request"
        )
        self.assertEqual(cancellation.payload["reason"], "Owner stopped supervised work")
        service.close()

    def test_full_collaboration_completes_over_mutual_tls(self):
        with TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            ca_certificate, ca_key = create_ca(directory)
            identities = (
                "owner-coordinator-1",
                "robot-humanoid-1",
                "robot-quadruped-1",
                "robot-mobile-arm-1",
            )
            credentials = {
                identity: create_leaf(directory, ca_certificate, ca_key, identity)
                for identity in identities
            }
            buses = {
                identity: tls_bus(
                    directory,
                    identity,
                    *credentials[identity],
                    ca_certificate,
                )
                for identity in identities
            }
            coordinator_bus = buses["owner-coordinator-1"]
            registry = Registry(coordinator_bus)
            store = CoordinatorStore(directory / "coordinator.sqlite3")
            coordinator = Coordinator(
                coordinator_bus.robot_id,
                coordinator_bus,
                registry,
                store=store,
                authority_lease_ids={
                    identity: f"lease-{identity}" for identity in identities[1:]
                },
            )
            now_ms = 1_100
            service = CoordinatorService(
                coordinator_bus,
                coordinator,
                registry,
                clock_ms=lambda: now_ms,
                poll_interval_s=0.01,
            )
            coordinator_host, coordinator_port = service.start()
            robot_participants = []
            robot_buses = []
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
            try:
                for manifest in manifests:
                    robot_bus = buses[manifest.robot_id]
                    robot_host, robot_port = robot_bus.start()
                    robot_buses.append(robot_bus)
                    robot_bus.add_peer(
                        PeerEndpoint(
                            coordinator_bus.robot_id,
                            coordinator_host,
                            coordinator_port,
                            allowed_message_types=(
                                "manifest",
                                "state",
                                "assignment_ack",
                                "outcome",
                                "assignment_snapshot",
                                "cancellation_ack",
                            ),
                            allowed_capabilities=tuple(
                                capability.name for capability in manifest.capabilities
                            ),
                        )
                    )
                    coordinator_bus.add_peer(
                        PeerEndpoint(
                            manifest.robot_id,
                            robot_host,
                            robot_port,
                            allowed_message_types=(
                                "goal",
                                "plan",
                                "assignment",
                                "assignment_query",
                                "cancellation_request",
                            ),
                        )
                    )
                    participant = Participant(
                        SimulatedRobot(manifest),
                        robot_bus,
                        authorizer=AllowAllAuthorizer(),
                        clock_ms=lambda: now_ms,
                    )
                    robot_participants.append(participant)
                    participant.announce(now_ms)

                goal = SharedGoal(
                    "goal-tls-1",
                    "Move the sealed package to storage over the UMP network",
                    tuple(manifest.robot_id for manifest in manifests),
                    deadline_ms=60_000,
                )
                plan = service.submit(
                    goal, WarehousePlanner(), participant_timeout_s=3.0
                )
                snapshot = service.wait_for_completion(plan.plan_id, timeout_s=5.0)

                self.assertEqual(snapshot.status, RunStatus.SUCCEEDED)
                self.assertTrue(
                    all(
                        bus.delivery_metrics.failed_attempts == 0
                        for bus in buses.values()
                    )
                )
                self.assertTrue(all(not bus.errors for bus in buses.values()))
            finally:
                service.close()
                for robot_bus in robot_buses:
                    robot_bus.stop()
                for participant in robot_participants:
                    participant.close()

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
            output = StringIO()
            with redirect_stdout(output):
                exit_code = coordinator_main(
                    [
                        "runs",
                        "--database",
                        str(Path(directory) / "coordinator.sqlite3"),
                        "--status",
                        "succeeded",
                        "--limit",
                        "10",
                    ]
                )
            self.assertEqual(exit_code, 0)
            history = json.loads(output.getvalue())
            self.assertEqual([item["plan_id"] for item in history], [plan.plan_id])
            self.assertEqual(history[0]["created_at_ms"], 1_100)

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
