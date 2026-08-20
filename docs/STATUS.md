# Implementation Status

**Protocol version:** `ump/0.1`

**Maturity:** Reference foundation, not production or physical-control ready

## Implemented

- Fresh product requirements and architecture boundaries.
- Canonical JSON envelope encoding and decoding with a 64 KiB limit.
- Robot manifests, versioned capabilities, and semantic state descriptions.
- Frame-qualified SI-meter poses and bounded integrity-bound sensor metadata
  references without embedded sensor payloads.
- Recursively enforced robotics SI unit annotations and required coordinate-frame
  properties for numeric manufacturer capability schemas.
- Per-process session identity, monotonic sequences, replay rejection, and freshness.
- Source identity agreement for manifest and state payloads.
- Planner-neutral shared goals and dependency-based microtask plans.
- Public transport-independent planner protocol and trusted factory loader for
  deterministic, human-backed, or model-backed reasoning providers.
- Validation-atomic bounded goal batches and immutable plan revision lineage.
- Plan validation for participants, freshness, capabilities, dependencies, cycles,
  and deadlines.
- Capability input validation using JSON Schema Draft 2020-12.
- Duplicate assignment suppression with deterministic outcome replay.
- SQLite WAL assignment journal with durable acceptance and terminal outcomes.
- Payload-hash idempotency conflicts and restart-to-unknown behavior.
- Explicit assignment acknowledgements and terminal outcome statuses.
- SQLite WAL coordinator journal with immutable goals, plans, and stable assignments.
- Event-driven dependency progression for delayed acknowledgements and outcomes.
- Opt-in bounded concurrent native assignment execution with ordered envelope
  publication and exception-to-unknown safety behavior.
- Conservative coordinator restart recovery that marks in-flight work unknown.
- Authenticated assignment query/snapshot reconciliation bound to the original
  issuer, assigned robot, and canonical assignment fingerprint.
- Terminal participant evidence can resolve coordinator unknown state and safely
  resume successful dependencies; dual-unknown state remains blocked.
- Durable plan cancellation with native adapter authority, authenticated issuer
  binding, terminal cancelled outcomes, and restart-to-unknown recovery.
- Coordinator validation that outcome sources match assigned robots.
- TLS 1.3 network binding with mandatory client certificates and UMP ALPN.
- Certificate URI robot identity bound to every network envelope source.
- Optional SHA-256 certificate pinning from configured or discovered hints.
- Bounded framing, malformed-frame rejection, and in-memory session replay checks.
- Durable SQLite network replay protection for configured deployments, including
  authenticated replay rejection across receiver restart.
- Durable bounded per-peer outbox with FIFO restart recovery, capped exponential
  retry, backpressure, delivery metrics, and concrete error history.
- Durable ordered receiver inbox with receipt acknowledgements, idempotent exact
  retries, crash-window staging, concurrent peer processing, and dead letters.
- Protocol-safe safety-state priority stream with independent sequence/replay
  floors, queue heads, sender locks, inbox ordering, and reserved outbox capacity.
- Expiring UDP local-discovery hint encoding and transport.
- File-based network and peer configuration.
- Deny-by-default peer metadata disclosure with message-type allowlists and
  capability-filtered manifests.
- Deny-by-default participant and coordinator assignment authorization.
- Durable robot-local capability leases with issuer, time, and clock bounds.
- Revision-checked local grant, renewal, revocation, persistence, and audit history.
- Durable externally issued credential enrollment, staged rotation, activation,
  local fingerprint revocation, and handshake-time revocation enforcement.
- Robot-local evidence-based resolution of unknown assignments with immutable
  outcome, resolver identity, evidence audit, and safe resource release.
- Bounded structured outcome payloads validated against advertised capability
  output schemas before durable terminal acceptance.
- Versioned `ump.standard/v1` inspect-route, carry, and place contracts with a
  language-neutral catalog and validation CLI.
- Receiver-local lease evaluation resistant to sender timestamp backdating.
- Required-peer communication watchdog with manufacturer-defined loss and
  restoration callbacks derived from semantic-state freshness.
- Opaque robot-local resource declarations with atomic durable reservations,
  terminal release, contention rejection, and crash retention.
- Local `ump-authority` owner CLI.
- Deterministic three-robot simulation and correlated message trace.
- Optional ROS 2 action-backed manufacturer adapter with lazy `rclpy` loading,
  capability-specific codecs, cancellation, and uncertainty handling.
- ROS 2 `ExecuteCapability` interface package and Gazebo Harmonic conformance
  world with three cancellable proxy capability servers.
- Pinned Ubuntu Noble / ROS 2 Jazzy package build-and-test workflow for the
  reference interfaces and Gazebo fixture.
- Container-verified Jazzy interface generation and package build, plus a native
  Noble headless smoke gate for world startup and ROS action lifecycle behavior.
- Versioned, byte-exact valid and invalid wire vectors with SHA-256 integrity
  checks and a machine-readable `ump-conformance` report.
- Adapter conformance harness with read-only manifest/state inspection and an
  explicit opt-in gate before any native capability execution.
- Documented manufacturer-facing adapter protocol, supported package-root imports,
  and a runnable zero-capability read-only hardware integration starting point.
- Owner-facing `ump-node` service with trusted adapter factories, durable assignment
  and authority stores, bounded 1–10 Hz state publication, and signal shutdown.
- Owner-facing `ump-coordinator` workflow with trusted planner loading, bounded
  participant readiness, durable goal submission, completion waiting, and status.
- Participant-node enforcement of the active managed credential generation,
  handshake-time peer revocation, and runtime local-credential revocation checks.
- Participant-node detection of semantic safety transitions with single-snapshot,
  safety-stream-first publication and independent sequence ordering.
- Change-only participant manifest refresh so capability and availability updates
  reach peers before the corresponding periodic state.
- Owner-node required-peer configuration wired to the manufacturer communication
  loss/restoration contract with freshness-derived, edge-triggered callbacks.
- Append-only SQLite protocol recorder and loopback-only, read-only inspector UI
  for robot state, capabilities, and correlated protocol events.
- Reproducible reference-runtime benchmark for canonical in-memory state
  propagation, throughput, message size, and incremental idle Python heap.
- End-to-end mutual-TLS loopback benchmark covering fresh connection setup,
  identity binding, replay checks, framing, handling, and acknowledgement.
- Two-host mutual-TLS network benchmark harness with deployment credentials,
  bounded server lifetime, peer identity pinning, and machine-readable reports.
- Locally validated wheel and complete source-distribution build, with isolated
  wheel smoke tests and a checksum/provenance-aware release workflow.
- Executable traceability matrix covering all 41 named PRD functional
  requirements with evidence-backed implemented, partial, or missing status.

## Not yet implemented

- Automated integration with deployment-specific CA enrollment protocols,
  hardware security modules, and online OCSP/CRL services.
- Runtime-verified ROS 2/Gazebo dynamics, Isaac Sim, Webots, or physical robot
  adapters. The checked-in Gazebo proxies validate lifecycle, not physical work.
- Healthy-LAN TLS benchmarks, independent adapter conformance results, or release
  artifacts.
- Independent security, safety, or interoperability review.

The missing items are product work, not configuration switches. UMP must not be
represented as production-ready until the relevant PRD gates have evidence.
