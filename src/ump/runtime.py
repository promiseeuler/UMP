from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Event, RLock, Thread
import time
from typing import Protocol
from uuid import uuid4

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from .adapter import RobotAdapter
from .authority import (
    AssignmentAuthorizer,
    AuthorizationError,
    DenyAllAuthorizer,
)
from .journal import AssignmentJournal, ClaimKind, MemoryAssignmentJournal
from .models import (
    Assignment,
    AssignmentAcknowledgement,
    AssignmentQuery,
    AssignmentSnapshot,
    AssignmentStatus,
    CancellationAcknowledgement,
    CancellationRequest,
    CancellationStatus,
    Outcome,
    RobotManifest,
    RobotState,
    payload,
)
from .transport import (
    OPERATIONAL_STREAM,
    SAFETY_STREAM,
    Envelope,
    MessageBus,
    make_envelope,
)


@dataclass
class PeerView:
    manifest: RobotManifest | None = None
    state: RobotState | None = None
    state_timestamp_ms: int = 0
    last_sequences: dict[str, int] = field(default_factory=dict)
    session_id: str | None = None


class Registry:
    def __init__(self, bus: MessageBus) -> None:
        self.peers: dict[str, PeerView] = {}
        self._lock = RLock()
        bus.subscribe("manifest", self._locked_manifest)
        bus.subscribe("state", self._locked_state)

    def _locked_manifest(self, envelope: Envelope) -> None:
        with self._lock:
            self._manifest(envelope)

    def _locked_state(self, envelope: Envelope) -> None:
        with self._lock:
            self._state(envelope)

    def _manifest(self, envelope: Envelope) -> None:
        data = envelope.payload
        from .models import Availability, Capability

        capabilities = tuple(
            Capability(
                name=item["name"],
                description=item["description"],
                input_schema=item.get("input_schema", {}),
                output_schema=item.get("output_schema", {}),
                availability=Availability(item.get("availability", "available")),
            )
            for item in data["capabilities"]
        )
        manifest = RobotManifest(
            robot_id=data["robot_id"],
            manufacturer=data["manufacturer"],
            model=data["model"],
            robot_class=data["robot_class"],
            capabilities=capabilities,
            adapter_version=data.get("adapter_version", "0.1.0"),
        )
        if manifest.robot_id != envelope.source_id:
            raise ValueError("manifest robot_id does not match authenticated source")
        peer = self.peers.setdefault(envelope.source_id, PeerView())
        if peer.session_id != envelope.session_id:
            peer.state = None
            peer.state_timestamp_ms = 0
            peer.last_sequences.clear()
        previous = peer.last_sequences.get(envelope.stream, 0)
        if envelope.sequence <= previous:
            return
        peer.manifest = manifest
        peer.session_id = envelope.session_id
        peer.last_sequences[envelope.stream] = envelope.sequence

    def _state(self, envelope: Envelope) -> None:
        from .models import Mode, PoseReference, Safety, SensorReference

        peer = self.peers.setdefault(envelope.source_id, PeerView())
        if peer.session_id != envelope.session_id or peer.manifest is None:
            raise ValueError("state received before manifest for this source session")
        previous = peer.last_sequences.get(envelope.stream, 0)
        if envelope.sequence <= previous:
            return
        data = envelope.payload
        if data.get("robot_id") != envelope.source_id:
            raise ValueError("state robot_id does not match authenticated source")
        pose_data = data.get("pose")
        pose = (
            PoseReference(
                frame_id=pose_data["frame_id"],
                position_m=tuple(pose_data["position_m"]),
                orientation_xyzw=tuple(pose_data["orientation_xyzw"]),
                observed_at_ms=pose_data["observed_at_ms"],
            )
            if pose_data is not None
            else None
        )
        sensor_references = tuple(
            SensorReference(
                uri=item["uri"],
                media_type=item["media_type"],
                byte_length=item["byte_length"],
                sha256=item["sha256"],
                observed_at_ms=item["observed_at_ms"],
                expires_at_ms=item.get("expires_at_ms"),
                frame_id=item.get("frame_id"),
            )
            for item in data.get("sensor_references", ())
        )
        state = RobotState(
            robot_id=data["robot_id"],
            mode=Mode(data["mode"]),
            safety=Safety(data["safety"]),
            activity=data["activity"],
            intent=data["intent"],
            progress=data["progress"],
            summary=data["summary"],
            fresh_for_ms=data.get("fresh_for_ms", 2_000),
            blockers=tuple(data.get("blockers", ())),
            resources=tuple(data.get("resources", ())),
            assignment_id=data.get("assignment_id"),
            pose=pose,
            sensor_references=sensor_references,
        )
        peer.last_sequences[envelope.stream] = envelope.sequence
        if envelope.timestamp_ms < peer.state_timestamp_ms:
            return
        peer.state = state
        peer.state_timestamp_ms = envelope.timestamp_ms

    def is_fresh(self, robot_id: str, now_ms: int) -> bool:
        with self._lock:
            peer = self.peers.get(robot_id)
            return bool(
                peer
                and peer.state
                and now_ms - peer.state_timestamp_ms <= peer.state.fresh_for_ms
            )


class CommunicationLossHandler(Protocol):
    def communication_lost(
        self, stale_peer_ids: tuple[str, ...], observed_at_ms: int
    ) -> None: ...

    def communication_restored(
        self, restored_peer_ids: tuple[str, ...], observed_at_ms: int
    ) -> None: ...


class CommunicationWatchdog:
    """Invokes manufacturer policy when required peer state becomes stale."""

    def __init__(
        self,
        registry: Registry,
        required_peer_ids: tuple[str, ...],
        handler: CommunicationLossHandler,
        *,
        clock_ms: Callable[[], int] | None = None,
        check_interval_s: float = 0.25,
    ) -> None:
        if not required_peer_ids or len(required_peer_ids) != len(set(required_peer_ids)):
            raise ValueError("communication watchdog peers must be non-empty and unique")
        if not 0.05 <= check_interval_s <= 60.0:
            raise ValueError("communication check interval must be between 0.05 and 60 seconds")
        self.registry = registry
        self.required_peer_ids = tuple(sorted(required_peer_ids))
        self.handler = handler
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1_000))
        self._check_interval_s = check_interval_s
        self._active_stale: tuple[str, ...] = ()
        self._lock = RLock()
        self._stop = Event()
        self._thread: Thread | None = None
        self.errors: list[Exception] = []

    @property
    def stale_peer_ids(self) -> tuple[str, ...]:
        with self._lock:
            return self._active_stale

    def evaluate(self, now_ms: int | None = None) -> tuple[str, ...]:
        observed_at_ms = self._clock_ms() if now_ms is None else now_ms
        stale = tuple(
            peer_id
            for peer_id in self.required_peer_ids
            if not self.registry.is_fresh(peer_id, observed_at_ms)
        )
        with self._lock:
            previous = self._active_stale
            if stale == previous:
                return stale
            try:
                if stale:
                    self.handler.communication_lost(stale, observed_at_ms)
                else:
                    self.handler.communication_restored(previous, observed_at_ms)
            except Exception as error:
                self.errors.append(error)
                del self.errors[:-1_000]
                return previous
            self._active_stale = stale
            return stale

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                raise RuntimeError("communication watchdog is already running")
            self._stop.clear()
            self.evaluate()
            self._thread = Thread(
                target=self._run,
                name="ump-communication-watchdog",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            thread = self._thread
            if thread is None:
                return
            self._stop.set()
        thread.join(timeout=max(2.0, self._check_interval_s * 2))
        with self._lock:
            self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(self._check_interval_s):
            self.evaluate()


class Participant:
    def __init__(
        self,
        adapter: RobotAdapter,
        bus: MessageBus,
        journal: AssignmentJournal | None = None,
        authorizer: AssignmentAuthorizer | None = None,
        clock_ms: Callable[[], int] | None = None,
        execution_workers: int = 0,
        communication_watchdog: CommunicationWatchdog | None = None,
    ) -> None:
        if not 0 <= execution_workers <= 32:
            raise ValueError("execution_workers must be between 0 and 32")
        self.adapter = adapter
        self.bus = bus
        self.robot_id = adapter.manifest().robot_id
        self._sequences = {OPERATIONAL_STREAM: 0, SAFETY_STREAM: 0}
        self._publication_lock = RLock()
        self._last_published_safety = None
        self.session_id = str(uuid4())
        self.journal = journal or MemoryAssignmentJournal()
        self.authorizer = authorizer or DenyAllAuthorizer()
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1_000))
        self._executor = (
            ThreadPoolExecutor(
                max_workers=execution_workers,
                thread_name_prefix=f"ump-native-{self.robot_id}",
            )
            if execution_workers
            else None
        )
        self._communication_watchdog = communication_watchdog
        bus.subscribe("assignment", self._assignment)
        bus.subscribe("assignment_query", self._assignment_query)
        bus.subscribe("cancellation_request", self._cancellation_request)
        if self._communication_watchdog is not None:
            self._communication_watchdog.start()

    def _send(
        self,
        message_type: str,
        body: object,
        now_ms: int,
        correlation_id: str | None = None,
        stream: str = OPERATIONAL_STREAM,
    ) -> None:
        with self._publication_lock:
            self._sequences[stream] += 1
            self.bus.publish(
                make_envelope(
                    message_type,
                    self.robot_id,
                    self.session_id,
                    self._sequences[stream],
                    now_ms,
                    payload(body),
                    correlation_id,
                    stream,
                )
            )

    def _state_snapshot(self, state: RobotState | None) -> RobotState:
        snapshot = self.adapter.state() if state is None else state
        if not isinstance(snapshot, RobotState):
            raise TypeError("adapter state must be RobotState")
        if snapshot.robot_id != self.robot_id:
            raise ValueError("adapter state robot identity differs")
        return snapshot

    def announce(self, now_ms: int, *, state: RobotState | None = None) -> None:
        self._send("manifest", self.adapter.manifest(), now_ms)
        self.publish_state(now_ms, state=state)

    def publish_state(
        self,
        now_ms: int,
        correlation_id: str | None = None,
        *,
        state: RobotState | None = None,
    ) -> None:
        snapshot = self._state_snapshot(state)
        with self._publication_lock:
            if (
                self._last_published_safety is not None
                and snapshot.safety is not self._last_published_safety
            ):
                self._send(
                    "state",
                    snapshot,
                    now_ms,
                    correlation_id,
                    SAFETY_STREAM,
                )
            self._last_published_safety = snapshot.safety
            self._send("state", snapshot, now_ms, correlation_id)

    def publish_safety_state(
        self,
        now_ms: int,
        correlation_id: str | None = None,
        *,
        state: RobotState | None = None,
    ) -> None:
        snapshot = self._state_snapshot(state)
        with self._publication_lock:
            self._last_published_safety = snapshot.safety
            self._send(
                "state",
                snapshot,
                now_ms,
                correlation_id,
                SAFETY_STREAM,
            )

    def close(self) -> None:
        if self._communication_watchdog is not None:
            self._communication_watchdog.stop()
        if self._executor is not None:
            self._executor.shutdown(wait=True)
        self.journal.close()
        self.authorizer.close()

    def _assignment(self, envelope: Envelope) -> None:
        data = envelope.payload
        step_data = data["step"]
        if step_data["assigned_robot_id"] != self.robot_id:
            return
        from .models import PlanStep

        assignment = Assignment(
            assignment_id=data["assignment_id"],
            goal_id=data["goal_id"],
            plan_id=data["plan_id"],
            step=PlanStep(
                step_id=step_data["step_id"],
                description=step_data["description"],
                assigned_robot_id=step_data["assigned_robot_id"],
                capability=step_data["capability"],
                inputs=step_data["inputs"],
                completion_criteria=step_data["completion_criteria"],
                depends_on=tuple(step_data.get("depends_on", ())),
                resources=tuple(step_data.get("resources", ())),
                not_before_ms=step_data.get("not_before_ms"),
                deadline_ms=step_data.get("deadline_ms"),
            ),
            authority_lease_id=data.get("authority_lease_id"),
        )
        now_ms = self._clock_ms()
        if assignment.step.not_before_ms is not None and now_ms < assignment.step.not_before_ms:
            self._reject(
                assignment,
                envelope,
                "Assignment execution window has not opened",
                now_ms,
            )
            return
        if assignment.step.deadline_ms is not None and now_ms > assignment.step.deadline_ms:
            self._reject(
                assignment,
                envelope,
                "Assignment deadline has passed",
                now_ms,
            )
            return
        try:
            self.authorizer.authorize(
                envelope.source_id,
                self.robot_id,
                assignment,
                now_ms,
            )
        except AuthorizationError as error:
            self._reject(
                assignment,
                envelope,
                f"Assignment authority rejected: {error}",
                now_ms,
            )
            return
        capability = self.adapter.manifest().capability(assignment.step.capability)
        if capability is None:
            self._reject(
                assignment,
                envelope,
                "Assignment requests an unadvertised capability",
                now_ms,
            )
            return
        try:
            Draft202012Validator.check_schema(capability.input_schema)
            Draft202012Validator(capability.input_schema).validate(assignment.step.inputs)
        except (SchemaError, ValidationError):
            self._reject(
                assignment,
                envelope,
                "Assignment input does not conform to the advertised schema",
                now_ms,
            )
            return
        claim = self.journal.claim(assignment, envelope.source_id, now_ms)
        if claim.kind is ClaimKind.REPLAY:
            assert claim.outcome is not None
            self._acknowledge(
                assignment.assignment_id,
                claim.outcome.status,
                "Stored terminal outcome replayed without native execution",
                envelope,
                now_ms,
            )
            self._send("outcome", claim.outcome, now_ms, envelope.correlation_id)
            return
        if claim.kind is ClaimKind.CONFLICT:
            self._acknowledge(
                assignment.assignment_id,
                AssignmentStatus.REJECTED,
                "Assignment ID was previously bound to a different payload",
                envelope,
                now_ms,
            )
            self._send(
                "outcome",
                Outcome(
                    assignment.assignment_id,
                    self.robot_id,
                    False,
                    "Idempotency conflict: assignment ID reused with different content",
                    AssignmentStatus.REJECTED,
                ),
                now_ms,
                envelope.correlation_id,
            )
            return
        if claim.kind is ClaimKind.RESOURCE_CONFLICT:
            self._acknowledge(
                assignment.assignment_id,
                AssignmentStatus.REJECTED,
                "One or more robot-local resources are reserved by active work",
                envelope,
                now_ms,
            )
            self._send(
                "outcome",
                Outcome(
                    assignment.assignment_id,
                    self.robot_id,
                    False,
                    "Robot-local resource reservation conflict",
                    AssignmentStatus.REJECTED,
                ),
                now_ms,
                envelope.correlation_id,
            )
            return
        if claim.kind is ClaimKind.UNCERTAIN:
            self._acknowledge(
                assignment.assignment_id,
                AssignmentStatus.UNKNOWN,
                "Previous execution may have occurred; operator reconciliation is required",
                envelope,
                now_ms,
            )
            self._send(
                "outcome",
                Outcome(
                    assignment.assignment_id,
                    self.robot_id,
                    False,
                    "Execution outcome is unknown after interruption; not retried",
                    AssignmentStatus.UNKNOWN,
                ),
                now_ms,
                envelope.correlation_id,
            )
            return
        self._acknowledge(
            assignment.assignment_id,
            AssignmentStatus.ACCEPTED,
            "Assignment durably accepted for native adapter execution",
            envelope,
            now_ms,
        )
        if self._executor is not None:
            self._executor.submit(self._execute_assignment, assignment, envelope)
            return
        self._execute_assignment(assignment, envelope)

    def _execute_assignment(self, assignment: Assignment, envelope: Envelope) -> None:
        try:
            outcome = self.adapter.accept(assignment)
            if not isinstance(outcome, Outcome):
                raise TypeError("adapter must return Outcome")
            if outcome.assignment_id != assignment.assignment_id:
                raise ValueError("adapter outcome assignment identity differs")
            if outcome.robot_id != self.robot_id:
                raise ValueError("adapter outcome robot identity differs")
            capability = self.adapter.manifest().capability(assignment.step.capability)
            if capability is None:
                raise ValueError("adapter no longer advertises the assigned capability")
            if outcome.status is AssignmentStatus.SUCCEEDED:
                Draft202012Validator.check_schema(capability.output_schema)
                Draft202012Validator(capability.output_schema).validate(outcome.outputs)
        except Exception as error:
            outcome = Outcome(
                assignment.assignment_id,
                self.robot_id,
                False,
                f"Native adapter outcome is unknown after {type(error).__name__}",
                AssignmentStatus.UNKNOWN,
            )
        completed_at_ms = self._clock_ms()
        latest = self.journal.lookup(assignment.assignment_id)
        if latest is not None and latest.outcome is not None:
            outcome = latest.outcome
        else:
            self.journal.complete(assignment, outcome, completed_at_ms)
        self._send("outcome", outcome, completed_at_ms, envelope.correlation_id)
        self.publish_state(completed_at_ms, envelope.correlation_id)

    def _assignment_query(self, envelope: Envelope) -> None:
        query = AssignmentQuery(
            assignment_id=envelope.payload["assignment_id"],
            robot_id=envelope.payload["robot_id"],
        )
        if query.robot_id != self.robot_id:
            return
        record = self.journal.lookup(query.assignment_id)
        if record is None or record.issuer_id != envelope.source_id:
            return
        description = (
            record.outcome.description
            if record.outcome is not None
            else "Participant journal has no terminal outcome"
        )
        self._send(
            "assignment_snapshot",
            AssignmentSnapshot(
                assignment_id=query.assignment_id,
                robot_id=self.robot_id,
                status=record.status,
                description=description,
                fingerprint=record.fingerprint,
                outcome=record.outcome,
            ),
            self._clock_ms(),
            envelope.correlation_id,
        )

    def _cancellation_request(self, envelope: Envelope) -> None:
        request = CancellationRequest(
            assignment_id=envelope.payload["assignment_id"],
            robot_id=envelope.payload["robot_id"],
            reason=envelope.payload["reason"],
        )
        if request.robot_id != self.robot_id:
            return
        record = self.journal.lookup(request.assignment_id)
        if record is None or record.issuer_id != envelope.source_id:
            return
        if record.outcome is not None:
            self._send_cancellation_ack(
                request,
                CancellationStatus.ALREADY_TERMINAL,
                "Assignment already has a terminal outcome",
                envelope,
            )
            self._send(
                "outcome", record.outcome, self._clock_ms(), envelope.correlation_id
            )
            return
        if record.status is AssignmentStatus.UNKNOWN:
            self._send_cancellation_ack(
                request,
                CancellationStatus.UNKNOWN,
                "Assignment execution state is unknown; cancellation cannot be confirmed",
                envelope,
            )
            return
        cancelled, description = self.adapter.cancel(
            request.assignment_id, request.reason
        )
        if not cancelled:
            self._send_cancellation_ack(
                request, CancellationStatus.REJECTED, description, envelope
            )
            return
        now_ms = self._clock_ms()
        outcome = Outcome(
            request.assignment_id,
            self.robot_id,
            False,
            description,
            AssignmentStatus.CANCELLED,
        )
        self.journal.complete(record.assignment, outcome, now_ms)
        self._send_cancellation_ack(
            request, CancellationStatus.ACCEPTED, description, envelope
        )
        self._send("outcome", outcome, now_ms, envelope.correlation_id)

    def _send_cancellation_ack(
        self,
        request: CancellationRequest,
        status: CancellationStatus,
        description: str,
        envelope: Envelope,
    ) -> None:
        self._send(
            "cancellation_ack",
            CancellationAcknowledgement(
                request.assignment_id, self.robot_id, status, description
            ),
            self._clock_ms(),
            envelope.correlation_id,
        )

    def _reject(
        self,
        assignment: Assignment,
        envelope: Envelope,
        description: str,
        now_ms: int,
    ) -> None:
        self._acknowledge(
            assignment.assignment_id,
            AssignmentStatus.REJECTED,
            description,
            envelope,
            now_ms,
        )
        self._send(
            "outcome",
            Outcome(
                assignment.assignment_id,
                self.robot_id,
                False,
                description,
                AssignmentStatus.REJECTED,
            ),
            now_ms,
            envelope.correlation_id,
        )

    def _acknowledge(
        self,
        assignment_id: str,
        status: AssignmentStatus,
        description: str,
        envelope: Envelope,
        now_ms: int,
    ) -> None:
        self._send(
            "assignment_ack",
            AssignmentAcknowledgement(
                assignment_id=assignment_id,
                robot_id=self.robot_id,
                status=status,
                description=description,
            ),
            now_ms,
            envelope.correlation_id,
        )
