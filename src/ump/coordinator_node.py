"""Owner-facing service for submitting collaborative goals over UMP."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from threading import Event
import time

from .collaboration import Coordinator
from .coordinator_store import RunSnapshot, RunStatus
from .models import Plan, SharedGoal
from .network import TlsNetworkBus
from .planner import Planner
from .runtime import Registry


class ParticipantContextTimeout(TimeoutError):
    """Raised when all declared goal participants do not become usable in time."""


class RunCompletionTimeout(TimeoutError):
    """Raised when a submitted plan remains active beyond the owner timeout."""


class CoordinatorService:
    """Owns the network and durable coordinator lifecycle for one submission."""

    def __init__(
        self,
        bus: TlsNetworkBus,
        coordinator: Coordinator,
        registry: Registry,
        *,
        clock_ms: Callable[[], int] | None = None,
        poll_interval_s: float = 0.1,
        health_check: Callable[[], None] | None = None,
    ) -> None:
        if coordinator.bus is not bus or coordinator.registry is not registry:
            raise ValueError("coordinator, registry, and network bus must be shared")
        if coordinator.coordinator_id != bus.robot_id:
            raise ValueError("coordinator and network identities differ")
        if not 0.01 <= poll_interval_s <= 10.0:
            raise ValueError("poll interval must be between 0.01 and 10 seconds")
        self.bus = bus
        self.coordinator = coordinator
        self.registry = registry
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1_000))
        self._poll_interval_s = poll_interval_s
        self._health_check = health_check or (lambda: None)
        self._started = False
        self._closed = False

    def start(self) -> tuple[str, int]:
        if self._closed:
            raise RuntimeError("coordinator service is closed")
        if self._started:
            raise RuntimeError("coordinator service is already started")
        self._health_check()
        endpoint = self.bus.start()
        self._started = True
        return endpoint

    def wait_for_participants(
        self,
        participant_ids: tuple[str, ...],
        timeout_s: float,
        stop: Event | None = None,
    ) -> None:
        if not self._started:
            raise RuntimeError("coordinator service is not started")
        if timeout_s <= 0:
            raise ValueError("participant timeout must be positive")
        stop = stop or Event()
        deadline = time.monotonic() + timeout_s
        while True:
            now_ms = self._clock_ms()
            missing = self.registry.missing_context(participant_ids, now_ms)
            if not missing:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ParticipantContextTimeout(
                    f"participants did not provide manifest and fresh state: {missing}"
                )
            if stop.wait(min(self._poll_interval_s, remaining)):
                raise InterruptedError("coordinator submission interrupted")

    def submit(
        self,
        goal: SharedGoal,
        planner: Planner,
        *,
        participant_timeout_s: float = 30.0,
        stop: Event | None = None,
    ) -> Plan:
        self.wait_for_participants(goal.participant_ids, participant_timeout_s, stop)
        self._health_check()
        return self.coordinator.submit(goal, planner, self._clock_ms())

    def submit_many(
        self,
        goals: tuple[SharedGoal, ...],
        planner: Planner,
        *,
        participant_timeout_s: float = 30.0,
        stop: Event | None = None,
    ) -> tuple[Plan, ...]:
        participant_ids = tuple(
            dict.fromkeys(
                participant_id
                for goal in goals
                for participant_id in goal.participant_ids
            )
        )
        self.wait_for_participants(participant_ids, participant_timeout_s, stop)
        self._health_check()
        return self.coordinator.submit_many(goals, planner, self._clock_ms())

    def wait_for_completion(
        self,
        plan_id: str,
        timeout_s: float,
        stop: Event | None = None,
    ) -> RunSnapshot:
        if timeout_s <= 0:
            raise ValueError("completion timeout must be positive")
        stop = stop or Event()
        deadline = time.monotonic() + timeout_s
        while True:
            snapshot = self.coordinator.snapshot(plan_id)
            if snapshot.status is not RunStatus.ACTIVE:
                return snapshot
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RunCompletionTimeout(f"plan remains active: {plan_id}")
            if stop.wait(min(self._poll_interval_s, remaining)):
                raise InterruptedError("coordinator wait interrupted")

    def wait_for_completions(
        self,
        plan_ids: tuple[str, ...],
        timeout_s: float,
        stop: Event | None = None,
    ) -> tuple[RunSnapshot, ...]:
        if not plan_ids or len(plan_ids) != len(set(plan_ids)):
            raise ValueError("completion wait requires unique plan identifiers")
        if timeout_s <= 0:
            raise ValueError("completion timeout must be positive")
        stop = stop or Event()
        deadline = time.monotonic() + timeout_s
        while True:
            snapshots = tuple(
                self.coordinator.snapshot(plan_id) for plan_id in plan_ids
            )
            active = tuple(
                snapshot.plan_id
                for snapshot in snapshots
                if snapshot.status is RunStatus.ACTIVE
            )
            if not active:
                return snapshots
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RunCompletionTimeout(f"plans remain active: {active}")
            if stop.wait(min(self._poll_interval_s, remaining)):
                raise InterruptedError("coordinator batch wait interrupted")

    def wait_for_resolution(
        self,
        plan_id: str,
        timeout_s: float,
        stop: Event | None = None,
    ) -> RunSnapshot:
        """Wait until restart uncertainty is resolved to a known terminal run."""
        if timeout_s <= 0:
            raise ValueError("reconciliation timeout must be positive")
        stop = stop or Event()
        deadline = time.monotonic() + timeout_s
        while True:
            snapshot = self.coordinator.snapshot(plan_id)
            if snapshot.status in {
                RunStatus.SUCCEEDED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }:
                return snapshot
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RunCompletionTimeout(f"plan remains unresolved: {plan_id}")
            if stop.wait(min(self._poll_interval_s, remaining)):
                raise InterruptedError("coordinator reconciliation interrupted")

    def cancel(
        self,
        plan_id: str,
        reason: str,
        *,
        stop: Event | None = None,
    ) -> tuple[str, ...]:
        if not self._started:
            raise RuntimeError("coordinator service is not started")
        if stop is not None and stop.is_set():
            raise InterruptedError("coordinator cancellation interrupted")
        self._health_check()
        return self.coordinator.cancel(plan_id, reason, self._clock_ms())

    def reconcile(
        self,
        plan_id: str,
        *,
        participant_timeout_s: float = 30.0,
        stop: Event | None = None,
    ) -> tuple[str, ...]:
        goal = self.coordinator.store.goal(plan_id)
        self.wait_for_participants(
            goal.participant_ids, participant_timeout_s, stop
        )
        self._health_check()
        now_ms = self._clock_ms()
        assignment_ids = self.coordinator.reconcile(plan_id, now_ms)
        self.coordinator.resume_active(now_ms)
        return assignment_ids

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.bus.stop()
        finally:
            self.coordinator.close()


def parse_authority_leases(values: tuple[str, ...] | list[str]) -> Mapping[str, str]:
    """Parse repeatable ``ROBOT_ID=LEASE_ID`` owner arguments."""
    leases: dict[str, str] = {}
    for value in values:
        robot_id, separator, lease_id = value.partition("=")
        if not separator or not robot_id or not lease_id:
            raise ValueError("authority lease must use ROBOT_ID=LEASE_ID syntax")
        if robot_id in leases:
            raise ValueError(f"duplicate authority lease robot: {robot_id}")
        leases[robot_id] = lease_id
    return leases
