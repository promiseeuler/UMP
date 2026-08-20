from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from importlib.resources import files
from pathlib import Path
import platform
import subprocess
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from .benchmark import run_tls_loopback_benchmark
from .collaboration import Coordinator, PlanValidationError
from .conformance import AdapterConformanceHarness
from .demo import WarehousePlanner, build_demo
from .models import Plan, PlanStep, SharedGoal
from .runtime import CommunicationWatchdog, Registry


PROFILE = "ump.simulated-qualification/v1"
REQUIRED_CHECK_IDS = frozenset(
    {
        "simulation.adapters.read-only-conformance",
        "simulation.authority.deny-by-default",
        "simulation.awareness.all-peers",
        "simulation.collaboration.completed",
        "simulation.collaboration.dependencies",
        "simulation.communication.loss-restoration",
        "simulation.plan.cycle-rejected",
        "simulation.trace.correlated",
        "simulation.transport.mutual-tls-loopback",
    }
)


class SimulatedQualificationError(ValueError):
    pass


def simulated_qualification_schema() -> dict[str, Any]:
    resource = files("ump").joinpath(
        "simulated_qualification_data/v1/schema.json"
    )
    return json.loads(resource.read_text(encoding="utf-8"))


def _repository_revision(project_root: Path) -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise SimulatedQualificationError(
            "project root must be a readable Git checkout"
        ) from error


class _CommunicationEvents:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def communication_lost(
        self, peer_ids: tuple[str, ...], observed_at_ms: int
    ) -> None:
        self.events.append(
            {"event": "lost", "peer_ids": list(peer_ids), "observed_at_ms": observed_at_ms}
        )

    def communication_restored(
        self, peer_ids: tuple[str, ...], observed_at_ms: int
    ) -> None:
        self.events.append(
            {
                "event": "restored",
                "peer_ids": list(peer_ids),
                "observed_at_ms": observed_at_ms,
            }
        )


class _CyclicPlanner:
    planner_id = "ump.reference.cyclic-test-planner/v1"

    def propose(self, goal, manifests, states) -> Plan:
        del states
        first = next(
            robot_id
            for robot_id, manifest in manifests.items()
            if manifest.capability("ump.material.carry/v1") is not None
        )
        second = next(
            robot_id
            for robot_id, manifest in manifests.items()
            if manifest.capability("ump.navigation.inspect-route/v1") is not None
        )
        return Plan(
            plan_id="plan-cycle-rejection",
            goal_id=goal.goal_id,
            planner_id=self.planner_id,
            summary="Deliberately cyclic plan used to verify rejection.",
            steps=(
                PlanStep(
                    "cycle-a",
                    "First invalid cyclic step",
                    first,
                    "ump.material.carry/v1",
                    {"object": "package-1", "destination": "storage"},
                    "Never dispatched",
                    ("cycle-b",),
                ),
                PlanStep(
                    "cycle-b",
                    "Second invalid cyclic step",
                    second,
                    "ump.navigation.inspect-route/v1",
                    {"from": "intake", "to": "storage"},
                    "Never dispatched",
                    ("cycle-a",),
                ),
            ),
        )


def _goal(registry: Registry, goal_id: str = "goal-simulated-qualification") -> SharedGoal:
    return SharedGoal(
        goal_id=goal_id,
        description="Move the sealed package from intake to storage shelf A",
        participant_ids=tuple(sorted(registry.peers)),
        constraints={"keep_upright": True},
        deadline_ms=60_000,
    )


def _collaboration_scenario() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    bus, registry, participants = build_demo()
    observers = {participant.robot_id: Registry(bus) for participant in participants}
    for participant in participants:
        participant.announce(now_ms=1_100)

    robot_ids = tuple(sorted(registry.peers))
    awareness_passed = all(
        set(observer.peers) == set(robot_ids)
        and all(peer.manifest is not None and peer.state is not None for peer in observer.peers.values())
        for observer in observers.values()
    )
    goal = _goal(registry)
    coordinator = Coordinator(
        "ump-coordinator-simulation",
        bus,
        registry,
        authority_lease_ids={robot_id: "simulation-only-authority" for robot_id in robot_ids},
    )
    try:
        plan = coordinator.execute(goal, WarehousePlanner(), now_ms=1_100)
        snapshot = coordinator.store.snapshot(plan.plan_id)
    finally:
        coordinator.close()
        for participant in participants:
            participant.close()

    message_counts = Counter(envelope.message_type for envelope in bus.trace)
    assignment_index: dict[str, int] = {}
    outcome_index: dict[str, int] = {}
    assignment_step: dict[str, str] = {}
    for index, envelope in enumerate(bus.trace):
        if envelope.message_type == "assignment":
            assignment_id = envelope.payload["assignment_id"]
            assignment_index[assignment_id] = index
            assignment_step[assignment_id] = envelope.payload["step"]["step_id"]
        elif envelope.message_type == "outcome":
            outcome_index[envelope.payload["assignment_id"]] = index
    indexes_by_step = {
        step_id: (assignment_index[assignment_id], outcome_index[assignment_id])
        for assignment_id, step_id in assignment_step.items()
    }
    dependency_order_passed = (
        indexes_by_step["inspect-route"][1] < indexes_by_step["carry-package"][0]
        and indexes_by_step["carry-package"][1] < indexes_by_step["place-package"][0]
    )
    final_states = {
        robot_id: registry.peers[robot_id].state.summary
        for robot_id in robot_ids
        if registry.peers[robot_id].state is not None
    }
    scenario = {
        "participants": robot_ids,
        "robot_classes": sorted(
            registry.peers[robot_id].manifest.robot_class for robot_id in robot_ids
        ),
        "goal_id": goal.goal_id,
        "plan_id": plan.plan_id,
        "planner_id": plan.planner_id,
        "run_status": snapshot.status.value,
        "message_counts": dict(sorted(message_counts.items())),
        "final_state_summaries": final_states,
    }
    checks = [
        {
            "id": "simulation.awareness.all-peers",
            "passed": awareness_passed,
            "description": "Every simulated robot observer received all manifests and semantic states.",
        },
        {
            "id": "simulation.collaboration.completed",
            "passed": snapshot.status.value == "succeeded" and len(snapshot.steps) == 3,
            "description": "The shared goal completed across three heterogeneous participants.",
        },
        {
            "id": "simulation.collaboration.dependencies",
            "passed": dependency_order_passed,
            "description": "Dependent assignments were dispatched only after prerequisite outcomes.",
        },
        {
            "id": "simulation.trace.correlated",
            "passed": message_counts["assignment"] == 3
            and message_counts["assignment_ack"] == 3
            and message_counts["outcome"] == 3,
            "description": "The trace contains an acknowledgement and outcome for every assignment.",
        },
    ]
    return scenario, checks


def _boundary_scenarios() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    bus, registry, participants = build_demo()
    robot_ids = tuple(sorted(registry.peers))
    goal = _goal(registry, "goal-boundary-checks")

    no_authority = Coordinator("coordinator-no-authority", bus, registry)
    try:
        try:
            no_authority.submit(goal, WarehousePlanner(), now_ms=1_100)
            authority_rejected = False
        except PlanValidationError as error:
            authority_rejected = "missing authority leases" in str(error)
    finally:
        no_authority.close()

    cyclic = Coordinator(
        "coordinator-cycle",
        bus,
        registry,
        authority_lease_ids={robot_id: "simulation-only-authority" for robot_id in robot_ids},
    )
    try:
        try:
            cyclic.submit(goal, _CyclicPlanner(), now_ms=1_100)
            cycle_rejected = False
        except PlanValidationError as error:
            cycle_rejected = "dependency cycle" in str(error)
    finally:
        cyclic.close()

    handler = _CommunicationEvents()
    watchdog = CommunicationWatchdog(
        registry,
        (robot_ids[0],),
        handler,
        check_interval_s=1.0,
    )
    initial = watchdog.evaluate(1_100)
    stale = watchdog.evaluate(4_000)
    participants[0].announce(now_ms=4_100)
    restored = watchdog.evaluate(4_100)
    for participant in participants:
        participant.close()

    communication_passed = (
        initial == ()
        and stale == (robot_ids[0],)
        and restored == ()
        and [event["event"] for event in handler.events] == ["lost", "restored"]
    )
    scenario = {
        "authority_without_lease": "rejected" if authority_rejected else "accepted",
        "cyclic_plan": "rejected" if cycle_rejected else "accepted",
        "communication_events": handler.events,
    }
    checks = [
        {
            "id": "simulation.authority.deny-by-default",
            "passed": authority_rejected,
            "description": "Assignments without robot-local authority leases were rejected.",
        },
        {
            "id": "simulation.plan.cycle-rejected",
            "passed": cycle_rejected,
            "description": "A cyclic microtask plan was rejected before dispatch.",
        },
        {
            "id": "simulation.communication.loss-restoration",
            "passed": communication_passed,
            "description": "Stale peer state invoked loss policy and fresh state invoked restoration.",
        },
    ]
    return scenario, checks


def run_simulated_qualification(
    project_root: str | Path = ".", *, tls_samples: int = 25
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    collaboration, collaboration_checks = _collaboration_scenario()
    boundaries, boundary_checks = _boundary_scenarios()
    tls = run_tls_loopback_benchmark(samples=tls_samples).as_dict()

    _, _, adapters = build_demo()
    try:
        conformance = [
            AdapterConformanceHarness().inspect(participant.adapter)
            for participant in adapters
        ]
    finally:
        for participant in adapters:
            participant.close()
    conformance_passed = all(report.passed for report in conformance)
    checks = collaboration_checks + boundary_checks + [
        {
            "id": "simulation.transport.mutual-tls-loopback",
            "passed": tls["passed"],
            "description": "Same-host TCP loopback messages passed mutual-TLS and latency checks.",
        },
        {
            "id": "simulation.adapters.read-only-conformance",
            "passed": conformance_passed,
            "description": "All three simulated adapters passed read-only contract inspection.",
        },
    ]
    report = {
        "profile": PROFILE,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repository_revision": _repository_revision(root),
        "scope": {
            "environment": "simulation",
            "qualification_substitute": False,
            "limitations": [
                "No physical robot dynamics or actuator behavior is exercised.",
                "The mutual-TLS measurement uses one host and does not qualify a two-host LAN.",
                "Adapters and reviewers are not independent manufacturers or external assessors.",
            ],
        },
        "scenarios": {
            "collaboration": collaboration,
            "boundaries": boundaries,
            "tls_loopback": tls,
            "adapter_conformance": [
                {
                    "robot_id": report.subject,
                    "passed": report.passed,
                    "checks": [
                        {"id": check.check_id, "passed": check.passed}
                        for check in report.checks
                    ],
                }
                for report in conformance
            ],
        },
        "checks": checks,
        "passed": all(check["passed"] for check in checks),
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
        },
    }
    Draft202012Validator(simulated_qualification_schema()).validate(report)
    return report


def validate_simulated_qualification(document: dict[str, Any]) -> None:
    try:
        Draft202012Validator(simulated_qualification_schema()).validate(document)
    except ValidationError as error:
        raise SimulatedQualificationError(f"report is invalid: {error.message}") from error
    checks = document["checks"]
    check_ids = [check["id"] for check in checks]
    if len(check_ids) != len(set(check_ids)) or set(check_ids) != REQUIRED_CHECK_IDS:
        raise SimulatedQualificationError(
            "report checks do not match the simulated qualification profile"
        )
    if document["passed"] != (bool(checks) and all(check["passed"] for check in checks)):
        raise SimulatedQualificationError("report pass flag does not match its checks")
    if document["scope"]["qualification_substitute"] is not False:
        raise SimulatedQualificationError("simulation cannot claim production qualification")
