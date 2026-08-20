from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import gc
import math
from pathlib import Path
import platform
import re
import socket
import sys
import tempfile
from threading import Event, Lock
import time
import tracemalloc
from typing import Any, Callable

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .authority import AllowAllAuthorizer
from .models import RobotManifest
from .network import (
    TlsMessageClient,
    TlsMessageServer,
    create_client_context,
    create_server_context,
)
from .runtime import Participant, Registry
from .simulation import SimulatedRobot, capability
from .transport import MAX_MESSAGE_BYTES, InMemoryBus, encode_envelope, make_envelope


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


@dataclass(frozen=True)
class TlsBenchmarkThresholds:
    round_trip_p95_ms: float = 100.0


@dataclass(frozen=True)
class TlsBenchmarkResult:
    profile: str
    samples: int
    round_trip_p50_ms: float
    round_trip_p95_ms: float
    round_trip_p99_ms: float
    round_trips_per_second: float
    message_bytes: int
    maximum_message_bytes: int
    thresholds: TlsBenchmarkThresholds
    checks: dict[str, bool]
    environment: dict[str, str]

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["passed"] = self.passed
        return value


@dataclass(frozen=True)
class TlsNetworkBenchmarkResult:
    profile: str
    repository_revision: str
    samples: int
    warmup_samples: int
    local_robot_id: str
    remote_robot_id: str
    remote_host: str
    remote_port: int
    round_trip_p50_ms: float
    round_trip_p95_ms: float
    round_trip_p99_ms: float
    round_trips_per_second: float
    message_bytes: int
    maximum_message_bytes: int
    thresholds: TlsBenchmarkThresholds
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


def run_tls_loopback_benchmark(
    *,
    samples: int = 100,
    thresholds: TlsBenchmarkThresholds | None = None,
) -> TlsBenchmarkResult:
    if samples < 10:
        raise ValueError("samples must be at least 10")
    active_thresholds = thresholds or TlsBenchmarkThresholds()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        ca_path, server_certificate, server_key, client_certificate, client_key = (
            _benchmark_credentials(root)
        )
        received = 0

        def receive(_envelope) -> None:
            nonlocal received
            received += 1

        server = TlsMessageServer(
            "benchmark-server",
            "127.0.0.1",
            0,
            create_server_context(server_certificate, server_key, ca_path),
            receive,
        )
        host, port = server.start()
        client = TlsMessageClient(
            "benchmark-client",
            create_client_context(client_certificate, client_key, ca_path),
            timeout=5.0,
        )
        try:
            for sequence in range(1, 11):
                client.send(
                    host,
                    port,
                    "benchmark-server",
                    _benchmark_envelope(sequence),
                )
            timings: list[int] = []
            message_bytes = 0
            for sequence in range(11, samples + 11):
                envelope = _benchmark_envelope(sequence)
                message_bytes = max(message_bytes, len(encode_envelope(envelope)))
                started = time.perf_counter_ns()
                client.send(host, port, "benchmark-server", envelope)
                timings.append(time.perf_counter_ns() - started)
            if received != samples + 10:
                raise RuntimeError("TLS benchmark receiver count is inconsistent")
        finally:
            server.stop()
    total_ns = sum(timings)
    p50_ms = percentile(timings, 0.50) / 1_000_000
    p95_ms = percentile(timings, 0.95) / 1_000_000
    p99_ms = percentile(timings, 0.99) / 1_000_000
    return TlsBenchmarkResult(
        profile="ump.reference.tls-loopback/v1",
        samples=samples,
        round_trip_p50_ms=p50_ms,
        round_trip_p95_ms=p95_ms,
        round_trip_p99_ms=p99_ms,
        round_trips_per_second=samples / (total_ns / 1_000_000_000),
        message_bytes=message_bytes,
        maximum_message_bytes=MAX_MESSAGE_BYTES,
        thresholds=active_thresholds,
        checks={
            "round_trip_p95_within_reference_target": (
                p95_ms <= active_thresholds.round_trip_p95_ms
            ),
            "message_within_core_limit": message_bytes <= MAX_MESSAGE_BYTES,
        },
        environment={
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "executable": sys.executable,
            "transport": "TCP loopback; fresh mutual-TLS connection per message",
        },
    )


def run_tls_network_benchmark(
    *,
    host: str,
    port: int,
    local_robot_id: str,
    remote_robot_id: str,
    repository_revision: str,
    certificate_path: str | Path,
    private_key_path: str | Path,
    ca_path: str | Path,
    samples: int = 100,
    warmup_samples: int = 10,
    timeout: float = 5.0,
    expected_certificate_sha256: str | None = None,
    thresholds: TlsBenchmarkThresholds | None = None,
) -> TlsNetworkBenchmarkResult:
    if re.fullmatch(r"[0-9a-f]{40}", repository_revision) is None:
        raise ValueError("repository revision must be a full lowercase commit SHA")
    if samples < 10:
        raise ValueError("samples must be at least 10")
    if not 0 <= warmup_samples <= 10_000:
        raise ValueError("warmup samples must be between 0 and 10000")
    if not 1 <= port <= 65_535:
        raise ValueError("port must be between 1 and 65535")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    active_thresholds = thresholds or TlsBenchmarkThresholds()
    client = TlsMessageClient(
        local_robot_id,
        create_client_context(certificate_path, private_key_path, ca_path),
        timeout=timeout,
    )
    session_id = f"lan-benchmark-{time.time_ns()}"
    sequence = 1
    for _ in range(warmup_samples):
        client.send(
            host,
            port,
            remote_robot_id,
            _benchmark_envelope(sequence, local_robot_id, session_id),
            expected_certificate_sha256,
        )
        sequence += 1
    timings: list[int] = []
    message_bytes = 0
    for _ in range(samples):
        envelope = _benchmark_envelope(sequence, local_robot_id, session_id)
        message_bytes = max(message_bytes, len(encode_envelope(envelope)))
        started = time.perf_counter_ns()
        client.send(
            host,
            port,
            remote_robot_id,
            envelope,
            expected_certificate_sha256,
        )
        timings.append(time.perf_counter_ns() - started)
        sequence += 1
    total_ns = sum(timings)
    p50_ms = percentile(timings, 0.50) / 1_000_000
    p95_ms = percentile(timings, 0.95) / 1_000_000
    p99_ms = percentile(timings, 0.99) / 1_000_000
    return TlsNetworkBenchmarkResult(
        profile="ump.reference.tls-network/v1",
        repository_revision=repository_revision,
        samples=samples,
        warmup_samples=warmup_samples,
        local_robot_id=local_robot_id,
        remote_robot_id=remote_robot_id,
        remote_host=host,
        remote_port=port,
        round_trip_p50_ms=p50_ms,
        round_trip_p95_ms=p95_ms,
        round_trip_p99_ms=p99_ms,
        round_trips_per_second=samples / (total_ns / 1_000_000_000),
        message_bytes=message_bytes,
        maximum_message_bytes=MAX_MESSAGE_BYTES,
        thresholds=active_thresholds,
        checks={
            "round_trip_p95_within_reference_target": (
                p95_ms <= active_thresholds.round_trip_p95_ms
            ),
            "message_within_core_limit": message_bytes <= MAX_MESSAGE_BYTES,
        },
        environment={
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "executable": sys.executable,
            "local_hostname": socket.gethostname(),
            "transport": "TCP network; fresh mutual-TLS connection per message",
        },
    )


def serve_tls_network_benchmark(
    *,
    host: str,
    port: int,
    robot_id: str,
    repository_revision: str,
    certificate_path: str | Path,
    private_key_path: str | Path,
    ca_path: str | Path,
    expected_messages: int,
    timeout: float,
    ready: Callable[[str, int], None] | None = None,
) -> dict[str, Any]:
    if re.fullmatch(r"[0-9a-f]{40}", repository_revision) is None:
        raise ValueError("repository revision must be a full lowercase commit SHA")
    if expected_messages < 1:
        raise ValueError("expected messages must be positive")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    completed = Event()
    received_lock = Lock()
    received = 0

    def receive(_envelope) -> None:
        nonlocal received
        with received_lock:
            received += 1
            if received >= expected_messages:
                completed.set()

    server = TlsMessageServer(
        robot_id,
        host,
        port,
        create_server_context(certificate_path, private_key_path, ca_path),
        receive,
        connection_timeout=min(timeout, 10.0),
    )
    bound_host, bound_port = server.start()
    if ready is not None:
        ready(bound_host, bound_port)
    started = time.monotonic()
    complete = completed.wait(timeout)
    elapsed = time.monotonic() - started
    server.stop()
    with received_lock:
        received_messages = received
    return {
        "profile": "ump.reference.tls-network-server/v1",
        "repository_revision": repository_revision,
        "robot_id": robot_id,
        "bind_host": bound_host,
        "bind_port": bound_port,
        "expected_messages": expected_messages,
        "received_messages": received_messages,
        "elapsed_seconds": elapsed,
        "errors": [str(error) for error in server.errors],
        "passed": (
            complete
            and received_messages == expected_messages
            and not server.errors
        ),
        "environment": {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
    }


def _benchmark_envelope(
    sequence: int,
    source_id: str = "benchmark-client",
    session_id: str = "tls-benchmark-session",
):
    return make_envelope(
        "state",
        source_id,
        session_id,
        sequence,
        1_000 + sequence,
        {
            "robot_id": source_id,
            "mode": "idle",
            "safety": "normal",
            "activity": "Benchmarking authenticated transport",
            "intent": "Measure end-to-end UMP TLS delivery",
            "progress": 0.0,
            "summary": "TLS benchmark state sample",
            "fresh_for_ms": 2_000,
            "blockers": [],
            "assignment_id": None,
        },
    )


def _benchmark_credentials(root: Path) -> tuple[Path, Path, Path, Path, Path]:
    now = datetime.now(timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "UMP Benchmark CA")])
    ca_certificate = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    ca_path = root / "ca.pem"
    ca_path.write_bytes(ca_certificate.public_bytes(serialization.Encoding.PEM))

    def leaf(robot_id: str) -> tuple[Path, Path]:
        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, robot_id)])
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(ca_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(hours=1))
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.UniformResourceIdentifier(f"urn:ump:robot:{robot_id}")]
                ),
                critical=False,
            )
            .add_extension(
                x509.ExtendedKeyUsage(
                    [ExtendedKeyUsageOID.CLIENT_AUTH, ExtendedKeyUsageOID.SERVER_AUTH]
                ),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        certificate_path = root / f"{robot_id}.pem"
        key_path = root / f"{robot_id}.key"
        certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        return certificate_path, key_path

    server_certificate, server_key = leaf("benchmark-server")
    client_certificate, client_key = leaf("benchmark-client")
    return ca_path, server_certificate, server_key, client_certificate, client_key
