"""Deterministic hardware-readiness laboratory for UMP adapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from enum import Enum
import hashlib
import json
from pathlib import Path
import platform
import random
import time
from typing import Any, Callable, Iterable

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from jsonschema import Draft202012Validator

from .authority import SqliteAuthorityStore
from .collaboration import Coordinator
from .models import (
    Assignment,
    AuthorityLease,
    BatteryState,
    BatteryStatus,
    Capability,
    Health,
    Mode,
    Outcome,
    Plan,
    PlanStep,
    PoseReference,
    RobotManifest,
    RobotState,
    Safety,
    SharedGoal,
)
from .runtime import Participant, Registry
from .transport import InMemoryBus


class ControllerState(str, Enum):
    OFFLINE = "offline"
    IDLE = "idle"
    EXECUTING = "executing"
    DISCONNECTED = "disconnected"
    FAULTED = "faulted"
    EMERGENCY_STOP = "emergency_stop"


@dataclass
class DeterministicClock:
    now_ms: int = 1_000

    def __call__(self) -> int:
        return self.now_ms

    def advance(self, milliseconds: int) -> int:
        if milliseconds < 0:
            raise ValueError("clock cannot move backwards")
        self.now_ms += milliseconds
        return self.now_ms


@dataclass(frozen=True)
class FaultProfile:
    profile_id: str
    seed: int = 1
    latency_ms: int = 0
    jitter_ms: int = 0
    loss_rate: float = 0.0
    duplicate_rate: float = 0.0
    reorder_rate: float = 0.0
    partitioned_sources: tuple[str, ...] = ()
    broker_restart_at: int | None = None

    def __post_init__(self) -> None:
        if not self.profile_id.strip():
            raise ValueError("fault profile_id is required")
        if self.latency_ms < 0 or self.jitter_ms < 0:
            raise ValueError("fault timing cannot be negative")
        for name, value in (
            ("loss_rate", self.loss_rate),
            ("duplicate_rate", self.duplicate_rate),
            ("reorder_rate", self.reorder_rate),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> "FaultProfile":
        validate_fault_profile(document)
        return cls(
            profile_id=document["profile_id"],
            seed=document.get("seed", 1),
            latency_ms=document.get("latency_ms", 0),
            jitter_ms=document.get("jitter_ms", 0),
            loss_rate=document.get("loss_rate", 0.0),
            duplicate_rate=document.get("duplicate_rate", 0.0),
            reorder_rate=document.get("reorder_rate", 0.0),
            partitioned_sources=tuple(document.get("partitioned_sources", ())),
            broker_restart_at=document.get("broker_restart_at"),
        )

    def as_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(asdict(self)))


@dataclass(frozen=True)
class DeliveryDecision:
    sequence: int
    delivered: bool
    copies: int
    delay_ms: int
    reordered: bool
    reason: str


class FaultInjector:
    """Produces reproducible network decisions without changing protocol data."""

    def __init__(self, profile: FaultProfile) -> None:
        self.profile = profile
        self._random = random.Random(profile.seed)
        self._sequence = 0

    def decide(self, source_id: str) -> DeliveryDecision:
        self._sequence += 1
        if source_id in self.profile.partitioned_sources:
            return DeliveryDecision(self._sequence, False, 0, 0, False, "partition")
        if self._random.random() < self.profile.loss_rate:
            return DeliveryDecision(self._sequence, False, 0, 0, False, "loss")
        jitter = self._random.randint(-self.profile.jitter_ms, self.profile.jitter_ms)
        delay = max(0, self.profile.latency_ms + jitter)
        copies = 2 if self._random.random() < self.profile.duplicate_rate else 1
        reordered = self._random.random() < self.profile.reorder_rate
        return DeliveryDecision(self._sequence, True, copies, delay, reordered, "delivered")


@dataclass(frozen=True)
class ControllerLimits:
    minimum_battery_level: float = 0.15
    accepted_frame_id: str = "warehouse/map"


class VirtualRobotController:
    """Manufacturer-controller emulator exposed only through RobotAdapter methods."""

    def __init__(
        self,
        manifest: RobotManifest,
        *,
        clock: Callable[[], int],
        pose: tuple[float, float, float],
        battery_level: float | None,
        limits: ControllerLimits = ControllerLimits(),
    ) -> None:
        self._manifest = manifest
        self._clock = clock
        self._limits = limits
        self._controller_state = ControllerState.IDLE
        self._connected = True
        self._health = Health.HEALTHY
        self._safety = Safety.NORMAL
        self._battery_level = battery_level
        self._pose = pose
        self._active_assignment: str | None = None
        self._cancelled: set[str] = set()
        self.trace: list[dict[str, Any]] = []

    @property
    def controller_state(self) -> ControllerState:
        return self._controller_state

    def manifest(self) -> RobotManifest:
        return self._manifest

    def state(self) -> RobotState:
        now_ms = self._clock()
        mode = {
            ControllerState.IDLE: Mode.IDLE,
            ControllerState.EXECUTING: Mode.WORKING,
            ControllerState.DISCONNECTED: Mode.OFFLINE,
            ControllerState.OFFLINE: Mode.OFFLINE,
            ControllerState.FAULTED: Mode.FAULTED,
            ControllerState.EMERGENCY_STOP: Mode.FAULTED,
        }[self._controller_state]
        battery = None
        if self._battery_level is not None:
            battery = BatteryState(
                self._battery_level,
                BatteryStatus.DISCHARGING,
                now_ms,
                estimated_runtime_s=int(self._battery_level * 7_200),
            )
        return RobotState(
            robot_id=self._manifest.robot_id,
            mode=mode,
            safety=self._safety,
            activity="Executing native capability" if mode is Mode.WORKING else "Awaiting work",
            intent="Complete accepted assignment" if mode is Mode.WORKING else "Await authorized assignment",
            progress=0.5 if mode is Mode.WORKING else 0.0,
            summary=f"{self._manifest.robot_class} controller is {self._controller_state.value}",
            health=self._health,
            battery=battery,
            assignment_id=self._active_assignment,
            pose=PoseReference(
                self._limits.accepted_frame_id,
                self._pose,
                (0.0, 0.0, 0.0, 1.0),
                now_ms,
            ),
        )

    def set_connected(self, connected: bool) -> None:
        self._connected = connected
        self._controller_state = ControllerState.IDLE if connected else ControllerState.DISCONNECTED
        self._record("connection", connected=connected)

    def set_health(self, health: Health) -> None:
        self._health = health
        self._controller_state = ControllerState.FAULTED if health is Health.FAULTED else ControllerState.IDLE
        self._record("health", health=health.value)

    def set_safety(self, safety: Safety) -> None:
        self._safety = safety
        self._controller_state = (
            ControllerState.EMERGENCY_STOP if safety is Safety.EMERGENCY_STOP else ControllerState.IDLE
        )
        self._record("safety", safety=safety.value)

    def set_battery_level(self, level: float | None) -> None:
        if level is not None and not 0.0 <= level <= 1.0:
            raise ValueError("battery level must be between 0 and 1")
        self._battery_level = level
        self._record("battery", level=level)

    def accept(self, assignment: Assignment) -> Outcome:
        reason = self._rejection_reason(assignment)
        if reason:
            self._record("rejected", assignment_id=assignment.assignment_id, reason=reason)
            return Outcome(
                assignment.assignment_id,
                self._manifest.robot_id,
                False,
                reason,
                status=None,
            )
        self._controller_state = ControllerState.EXECUTING
        self._active_assignment = assignment.assignment_id
        self._record("accepted", assignment_id=assignment.assignment_id)
        outputs = self._execute_native(assignment)
        self._controller_state = ControllerState.IDLE
        self._active_assignment = None
        self._record("succeeded", assignment_id=assignment.assignment_id)
        return Outcome(
            assignment.assignment_id,
            self._manifest.robot_id,
            True,
            assignment.step.completion_criteria,
            outputs=outputs,
        )

    def cancel(self, assignment_id: str, reason: str) -> tuple[bool, str]:
        if assignment_id != self._active_assignment:
            return False, "Native controller has no matching active assignment"
        self._cancelled.add(assignment_id)
        self._active_assignment = None
        self._controller_state = ControllerState.IDLE
        self._record("cancelled", assignment_id=assignment_id, reason=reason)
        return True, "Native controller accepted cancellation"

    def _rejection_reason(self, assignment: Assignment) -> str | None:
        if not self._connected:
            return "Native controller is disconnected"
        if self._health is Health.FAULTED:
            return "Native controller health is faulted"
        if self._safety is not Safety.NORMAL:
            return f"Native safety state is {self._safety.value}"
        if self._battery_level is not None and self._battery_level < self._limits.minimum_battery_level:
            return "Native controller battery is below its operating threshold"
        if self._manifest.capability(assignment.step.capability) is None:
            return "Native controller rejected an unsupported capability"
        frame = assignment.step.inputs.get("frame_id") if isinstance(assignment.step.inputs, dict) else None
        if frame is not None and frame != self._limits.accepted_frame_id:
            return f"Native controller does not accept coordinate frame {frame}"
        return None

    def _execute_native(self, assignment: Assignment) -> dict[str, Any]:
        inputs = assignment.step.inputs
        capability = assignment.step.capability
        if capability == "ump.material.carry/v1":
            destination = tuple(inputs["destination_m"])
            self._pose = destination
            return {"delivered": True, "final_position_m": list(destination), "frame_id": inputs["frame_id"]}
        if capability == "ump.inspection.verify-workcell/v1":
            return {"ready": True, "observations": ["workcell_clear", "fixture_available"]}
        if capability == "ump.manipulation.place/v1":
            return {"placed": True, "target": inputs["target"], "frame_id": inputs["frame_id"]}
        return {"completed": True}

    def _record(self, event: str, **detail: Any) -> None:
        self.trace.append({"event": event, "observed_at_ms": self._clock(), **detail})


@dataclass(frozen=True)
class ScenarioStepDefinition:
    step_id: str
    robot_id: str
    capability: str
    description: str
    inputs: dict[str, Any]
    completion_criteria: str
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScenarioDefinition:
    scenario_id: str
    description: str
    participants: tuple[str, ...]
    steps: tuple[ScenarioStepDefinition, ...]

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> "ScenarioDefinition":
        validate_scenario(document)
        return cls(
            scenario_id=document["scenario_id"],
            description=document["description"],
            participants=tuple(document["participants"]),
            steps=tuple(
                ScenarioStepDefinition(
                    step_id=item["step_id"],
                    robot_id=item["robot_id"],
                    capability=item["capability"],
                    description=item["description"],
                    inputs=item.get("inputs", {}),
                    completion_criteria=item["completion_criteria"],
                    depends_on=tuple(item.get("depends_on", ())),
                )
                for item in document["steps"]
            ),
        )


@dataclass(frozen=True)
class ScenarioResult:
    scenario_id: str
    passed: bool
    started_at_ms: int
    finished_at_ms: int
    plan_id: str | None
    step_statuses: dict[str, str]
    traces: dict[str, tuple[dict[str, Any], ...]]
    failure: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(asdict(self)))


@dataclass(frozen=True)
class HardwareReadinessReport:
    profile: str
    generated_at_ms: int
    passed: bool
    runtime_versions: dict[str, str]
    configuration_sha256: str
    scenario: dict[str, Any]
    latency_ms: dict[str, float]
    delivery_counts: dict[str, int]
    fault_outcomes: tuple[dict[str, Any], ...]
    mapping_warnings: tuple[str, ...]
    artifact_sha256: dict[str, str]
    public_key: str | None = None
    signature: str | None = None

    def unsigned_document(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if key not in {"public_key", "signature"}}

    def as_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(asdict(self)))


def _schema(name: str) -> dict[str, Any]:
    path = Path(__file__).with_name("lab_data") / "v1" / name
    return json.loads(path.read_text(encoding="utf-8"))


def scenario_schema() -> dict[str, Any]:
    return _schema("scenario.schema.json")


def fault_profile_schema() -> dict[str, Any]:
    return _schema("fault-profile.schema.json")


def readiness_report_schema() -> dict[str, Any]:
    return _schema("readiness-report.schema.json")


def validate_scenario(document: dict[str, Any]) -> dict[str, Any]:
    Draft202012Validator(scenario_schema()).validate(document)
    return document


def validate_fault_profile(document: dict[str, Any]) -> dict[str, Any]:
    Draft202012Validator(fault_profile_schema()).validate(document)
    return document


def validate_readiness_report(document: dict[str, Any]) -> dict[str, Any]:
    Draft202012Validator(readiness_report_schema()).validate(document)
    return document


def _capability(name: str, description: str) -> Capability:
    return Capability(name, description, {"type": "object"}, {"type": "object"})


def mixed_fleet_controllers(clock: Callable[[], int]) -> dict[str, VirtualRobotController]:
    definitions = (
        ("mobile-1", "mobile_robot", "AMR-EMU", "ump.material.carry/v1", "Carry material", (0.0, 0.0, 0.0), 0.82),
        ("inspector-1", "quadruped_inspector", "Q-EMU", "ump.inspection.verify-workcell/v1", "Verify workcell", (4.0, 1.0, 0.0), 0.76),
        ("manipulator-1", "robot_arm", "ARM-EMU", "ump.manipulation.place/v1", "Place material", (8.0, 2.0, 0.0), None),
    )
    return {
        robot_id: VirtualRobotController(
            RobotManifest(robot_id, "UMP Reference Lab", model, robot_class, (_capability(capability, description),), "0.1.0-lab"),
            clock=clock,
            pose=pose,
            battery_level=battery,
        )
        for robot_id, robot_class, model, capability, description, pose, battery in definitions
    }


def default_scenario() -> ScenarioDefinition:
    path = Path(__file__).with_name("lab_data") / "v1" / "warehouse-inspection-transfer.json"
    return ScenarioDefinition.from_document(json.loads(path.read_text(encoding="utf-8")))


class _ScenarioPlanner:
    planner_id = "ump.lab.deterministic/v1"

    def __init__(self, definition: ScenarioDefinition) -> None:
        self.definition = definition

    def propose(self, goal, manifests, states) -> Plan:
        del manifests, states
        return Plan(
            f"{self.definition.scenario_id}-plan",
            goal.goal_id,
            self.planner_id,
            self.definition.description,
            tuple(
                PlanStep(item.step_id, item.description, item.robot_id, item.capability, item.inputs, item.completion_criteria, item.depends_on)
                for item in self.definition.steps
            ),
        )


def run_scenario(
    definition: ScenarioDefinition | None = None,
    *,
    workspace: str | Path,
    controller_setup: Callable[[dict[str, VirtualRobotController]], None] | None = None,
) -> ScenarioResult:
    scenario = definition or default_scenario()
    clock = DeterministicClock()
    controllers = mixed_fleet_controllers(clock)
    if controller_setup is not None:
        controller_setup(controllers)
    root = Path(workspace)
    root.mkdir(parents=True, exist_ok=True)
    bus = InMemoryBus()
    registry = Registry(bus)
    participants: list[Participant] = []
    authority_stores: list[SqliteAuthorityStore] = []
    lease_ids: dict[str, str] = {}
    started_at_ms = clock()
    plan_id: str | None = None
    failure: str | None = None
    statuses: dict[str, str] = {}
    try:
        capabilities_by_robot = {
            controller.manifest().robot_id: tuple(
                step.capability
                for step in scenario.steps
                if step.robot_id == controller.manifest().robot_id
            )
            for controller in controllers.values()
        }
        for robot_id in scenario.participants:
            controller = controllers[robot_id]
            store = SqliteAuthorityStore(robot_id, root / f"{robot_id}-authority.sqlite3")
            lease_id = f"{robot_id}-lab-lease"
            store.grant(AuthorityLease(lease_id, robot_id, "lab-coordinator", capabilities_by_robot[robot_id], 0, 60_000), clock())
            authority_stores.append(store)
            lease_ids[robot_id] = lease_id
            participant = Participant(controller, bus, authorizer=store, clock_ms=clock)
            participant.announce(clock())
            participants.append(participant)
        goal = SharedGoal(f"{scenario.scenario_id}-goal", scenario.description, scenario.participants)
        coordinator = Coordinator("lab-coordinator", bus, registry, authority_lease_ids=lease_ids)
        plan = coordinator.execute(goal, _ScenarioPlanner(scenario), clock())
        plan_id = plan.plan_id
        snapshot = coordinator.snapshot(plan.plan_id)
        statuses = {
            step_id: status.value for step_id, status in snapshot.steps.items()
        }
        passed = all(value == "succeeded" for value in statuses.values())
        coordinator.close()
    except Exception as error:
        passed = False
        failure = f"{type(error).__name__}: {error}"
    finally:
        for participant in participants:
            participant.close()
    clock.advance(100)
    return ScenarioResult(
        scenario.scenario_id,
        passed,
        started_at_ms,
        clock(),
        plan_id,
        statuses,
        {robot_id: tuple(controller.trace) for robot_id, controller in controllers.items()},
        failure,
    )


def run_fault_probe(profile: FaultProfile, messages: int = 100) -> tuple[dict[str, Any], ...]:
    injector = FaultInjector(profile)
    return tuple(asdict(injector.decide("mobile-1")) for _ in range(messages))


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def create_readiness_report(
    scenario: ScenarioResult,
    *,
    configuration: dict[str, Any],
    artifacts: Iterable[str | Path] = (),
    fault_outcomes: Iterable[dict[str, Any]] = (),
    generated_at_ms: int | None = None,
) -> HardwareReadinessReport:
    canonical_configuration = json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode()
    outcomes = tuple(fault_outcomes)
    delivered = sum(1 for item in outcomes if item.get("delivered"))
    dropped = len(outcomes) - delivered
    return HardwareReadinessReport(
        "ump-hardware-readiness/v1",
        generated_at_ms if generated_at_ms is not None else int(time.time() * 1_000),
        scenario.passed,
        {"python": platform.python_version(), "ump": "0.1.0", "platform": platform.platform()},
        hashlib.sha256(canonical_configuration).hexdigest(),
        scenario.as_dict(),
        {"p50": 0.0, "p95": 0.0, "p99": 0.0},
        {"attempted": len(outcomes), "delivered": delivered, "dropped": dropped},
        outcomes,
        (),
        {str(Path(path)): file_sha256(path) for path in artifacts},
    )


def _canonical_unsigned(report: HardwareReadinessReport | dict[str, Any]) -> bytes:
    document = report.unsigned_document() if isinstance(report, HardwareReadinessReport) else {
        key: value for key, value in report.items() if key not in {"public_key", "signature"}
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sign_readiness_report(report: HardwareReadinessReport, private_key: Ed25519PrivateKey) -> HardwareReadinessReport:
    public_key = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    signature = private_key.sign(_canonical_unsigned(report)).hex()
    return replace(report, public_key=public_key, signature=signature)


def verify_readiness_report(document: dict[str, Any]) -> None:
    validate_readiness_report(document)
    if not document.get("public_key") or not document.get("signature"):
        raise ValueError("readiness report is not signed")
    Ed25519PublicKey.from_public_bytes(bytes.fromhex(document["public_key"])).verify(
        bytes.fromhex(document["signature"]), _canonical_unsigned(document)
    )


def generate_signing_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def load_private_key(path: str | Path) -> Ed25519PrivateKey:
    value = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
    if not isinstance(value, Ed25519PrivateKey):
        raise TypeError("readiness signing key must be Ed25519")
    return value


def write_private_key(path: str | Path, key: Ed25519PrivateKey) -> None:
    Path(path).write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
