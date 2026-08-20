from __future__ import annotations

from dataclasses import asdict, dataclass
import gc
import math
import platform
import sys
import time
import tracemalloc
from typing import Any

from .authority import AllowAllAuthorizer
from .models import RobotManifest
from .runtime import Participant, Registry
from .simulation import SimulatedRobot, capability
from .transport import MAX_MESSAGE_BYTES, InMemoryBus, encode_envelope


@dataclass(frozen=True)
class BenchmarkThresholds:
    propagation_p95_ms: float = 100.0
    idle_heap_per_participant_bytes: int = 32 * 1024 * 1024


@dataclass(frozen=True)
class BenchmarkResult:
    profile: str
    samples: int
    participants: int
    propagation_p50_ms: float
    propagation_p95_ms: float
    propagation_p99_ms: float
    propagation_messages_per_second: float
    idle_heap_per_participant_bytes: int
    largest_observed_message_bytes: int
    maximum_message_bytes: int
    thresholds: BenchmarkThresholds
    checks: dict[str, bool]
    environment: dict[str, str]

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["passed"] = self.passed
        return value


def percentile(values: list[int], percentile_value: float) -> int:
    if not values:
        raise ValueError("percentile requires at least one sample")
    if not 0.0 <= percentile_value <= 1.0:
        raise ValueError("percentile must be between zero and one")
    ordered = sorted(values)
    index = max(0, math.ceil(percentile_value * len(ordered)) - 1)
    return ordered[index]


def _propagation(samples: int) -> tuple[list[int], int]:
    bus = InMemoryBus()
    registry = Registry(bus)
    manifest = RobotManifest(
        "benchmark-robot",
        "UMP Reference",
        "B1",
        "benchmark",
        (capability("ump.benchmark.observe/v1", "Benchmark state propagation"),),
    )
    participant = Participant(
        SimulatedRobot(manifest),
        bus,
        authorizer=AllowAllAuthorizer(),
    )
    participant.announce(1_000)
    largest_message = max(len(encode_envelope(item)) for item in bus.trace)
    try:
        for sequence in range(64):
            participant.publish_state(1_001 + sequence)
        bus.trace.clear()
        timings = []
        for sequence in range(samples):
            started = time.perf_counter_ns()
            participant.publish_state(2_000 + sequence)
            timings.append(time.perf_counter_ns() - started)
            largest_message = max(
                largest_message, len(encode_envelope(bus.trace[-1]))
            )
            bus.trace.clear()
        assert registry.peers[manifest.robot_id].state is not None
        return timings, largest_message
    finally:
        participant.close()


def _idle_heap_per_participant(participants: int) -> int:
    gc.collect()
    tracemalloc.start()
    baseline, _ = tracemalloc.get_traced_memory()
    bus = InMemoryBus()
    registry = Registry(bus)
    instances = []
    try:
        for index in range(participants):
            manifest = RobotManifest(
                f"benchmark-robot-{index}",
                "UMP Reference",
                "B1",
                "benchmark",
                (
                    capability(
                        "ump.benchmark.observe/v1",
                        "Benchmark idle participant memory",
                    ),
                ),
            )
            participant = Participant(
                SimulatedRobot(manifest),
                bus,
                authorizer=AllowAllAuthorizer(),
            )
            participant.announce(1_000)
            instances.append(participant)
        current, _ = tracemalloc.get_traced_memory()
        assert len(registry.peers) == participants
        return max(0, current - baseline) // participants
    finally:
        for participant in instances:
            participant.close()
        tracemalloc.stop()


def run_reference_benchmark(
    *,
    samples: int = 5_000,
    participants: int = 100,
    thresholds: BenchmarkThresholds | None = None,
) -> BenchmarkResult:
    if samples < 10:
        raise ValueError("samples must be at least 10")
    if participants < 1:
        raise ValueError("participants must be positive")
    active_thresholds = thresholds or BenchmarkThresholds()
    timings, largest_message = _propagation(samples)
    total_ns = sum(timings)
    p50_ms = percentile(timings, 0.50) / 1_000_000
    p95_ms = percentile(timings, 0.95) / 1_000_000
    p99_ms = percentile(timings, 0.99) / 1_000_000
    messages_per_second = samples / (total_ns / 1_000_000_000)
    heap_per_participant = _idle_heap_per_participant(participants)
    checks = {
        "propagation_p95_within_reference_target": (
            p95_ms <= active_thresholds.propagation_p95_ms
        ),
        "idle_heap_within_reference_target": (
            heap_per_participant
            <= active_thresholds.idle_heap_per_participant_bytes
        ),
        "messages_within_core_limit": largest_message <= MAX_MESSAGE_BYTES,
    }
    return BenchmarkResult(
        profile="ump.reference.in-memory/v1",
        samples=samples,
        participants=participants,
        propagation_p50_ms=p50_ms,
        propagation_p95_ms=p95_ms,
        propagation_p99_ms=p99_ms,
        propagation_messages_per_second=messages_per_second,
        idle_heap_per_participant_bytes=heap_per_participant,
        largest_observed_message_bytes=largest_message,
        maximum_message_bytes=MAX_MESSAGE_BYTES,
        thresholds=active_thresholds,
        checks=checks,
        environment={
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "executable": sys.executable,
        },
    )
