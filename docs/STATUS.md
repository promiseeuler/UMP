# UMP Implementation Status

**Updated:** 2026-08-14  
**Current roadmap interval:** Phase 5 installation paths and gateway in progress

## Completed

- Repository initialized as a Rust workspace.
- Linux `amd64` and `arm64` CI jobs defined.
- Apache-2.0 selected for the prototype; governance and trademark policy remain open.
- Contribution, conduct, security, glossary, ADR, and UEP foundations added.
- Protocol Buffers build works without a system `protoc` installation.
- UMP v1 envelope, hello, welcome, rejection, and heartbeat schemas added.
- Runtime validates identity fields, expiry, version ranges, and presence TTL.
- Deterministic simulation clock and in-memory delivery implemented.
- S0 mutual negotiation and deterministic presence-expiry simulation passes.
- Malformed hello, incompatible major, expired envelope, and heartbeat behavior are tested.
- Normative Core v0.1 behavior and compatibility rules are documented.
- Initial threat model and physical-adapter safety contract are published.
- Bounded mutual TLS/TCP and QUIC transport candidates are implemented.
- Both candidates enforce mutual authentication and reject identity mismatches.
- QUIC with ALPN `ump/1` is the provisional mandatory v1 transport.
- Reproducible release-mode benchmark commands and ARM64 baselines are published.
- Current S0 passes on macOS ARM64, native Linux ARM64, and emulated Linux AMD64.
- Machine descriptors cover endpoints, features, capabilities, constraints, and availability.
- Structured operational, health, safety, telemetry, and error messages are implemented.
- Authenticated peer identity is bound to every received envelope before runtime mutation.
- Metadata publication and disclosure require separately enrolled permissions and default to denied.
- Heartbeat replay, duplicate messages, stale state revisions, and oversized telemetry batches are rejected.
- Minimal `ump` and `umpd` processes support initialization, diagnostics, trust enrollment, runtime, peer listing, and inspection.
- Golden Protocol Buffers vectors decode consistently in Rust and Python.
- S1 deterministically validates 20 heterogeneous machines, 190 compatible negotiations, convergence by 500 ms, and its complete fault matrix.

## Phase 2 completed

- Task, acknowledgement, progress, terminal, cancellation, reconciliation, and authority-lease wire schemas are defined.
- Normative lifecycle, authorization, idempotency, journal ordering, and uncertain-outcome rules are documented.
- The runtime has scoped exclusive leases, renewal/revocation/expiry, task policy checks, bounded inputs, idempotency conflict detection, cancellation, progress, terminal outcomes, and reconciliation.
- Executor capability registration is required independently of lease authority.
- Retry-safe work uses a durable retry-pending state with enforced backoff, deadline, and attempt bounds.
- Acceptance and execution permission are appended to memory or fsynced JSONL journals before the caller can perform an action.
- Restarted running work becomes `unknown` with inspection required and cannot receive a second execution permit.
- `ump grant-lease`, `ump tasks`, and `ump timeline` expose authority and durable audit state.
- Authenticated QUIC dispatch persists task acceptance before acknowledgement and supports cancellation and reconciliation.
- Rust synchronous and Python synchronous/asynchronous handler registries provide adapter integration boundaries.
- Randomized malformed protobuf and task inputs are rejected without runtime termination.
- S2 passes 13 delivery-task variants with zero duplicate physical-action surrogates and zero unauthorized executions.
- Rust and Python both decode and round-trip the Phase 2 task-request golden vector.

Phase 2 passed locally and under Linux ARM64 and AMD64. Production PKI, independent fuzzing/security review, native onboard hardware measurements, and physical-robot validation remain later release requirements.

## Evidence

Run the complete local gate:

```sh
make check
make sim-s0
make sim-s1
make sim-s2
```

Validated locally on Apple ARM64 with the stable Rust toolchain:

- formatting: passed;
- Clippy with warnings denied: passed;
- Rust workspace tests: 39 passed, 0 failed;
- Python vector and handler tests: 7 passed, 0 failed;
- S0: passed at a deterministic simulated duration of 3,000 ms; and
- S1: passed with 20 machines, 190 negotiations, and convergence by 500 ms; and
- S2: passed 13 variants with no duplicate or unauthorized action surrogate.

Phase 0 transport measurements on Apple ARM64:

| Candidate | RTT p50 | RTT p95 | Reconnect + RTT p50 | RSS | Binary |
|---|---:|---:|---:|---:|---:|
| TLS 1.3/TCP | 29 us | 72 us | 373 us | 4,448 KiB | 2,980,880 B |
| QUIC/TLS 1.3 | 49 us | 99 us | 525 us | 13,952 KiB | 3,913,888 B |

## Phase 1 exit audit

- Required S1 scenarios: pass deterministically locally and on emulated Linux ARM64.
- Protected metadata: denied by default; publish and read permissions are independently enforced.
- Runtime footprint: measured at 3,264 KiB idle RSS for `umpd`, below the provisional 64 MiB target.
- Golden vectors: pass in Rust and Python using checked-in identical bytes.
- Authentication failures, replay, stale revisions, telemetry flood, and disappearance: covered by automated tests and S1.
- Current local gate: formatting, Clippy with warnings denied, all Rust tests, Python vectors, S0, and S1 pass.

Phase 1 is complete. Production credential enrollment, native Linux hardware benchmarks, external security review, and physical-robot validation remain required by later roadmap gates. Current credentials and simulations do not imply production or physical-control readiness.

## Phase 2 exit audit

- Duplicate action prevention: S2 reports zero across duplicate delivery, retry, crash, and uncertain-outcome variants.
- Convergence: partitioned and restarted views apply durable reconciliation; conflicting terminal outcomes become explicit `unknown` with inspection required.
- Authority: authenticated task permission, active lease scope, issuer identity, deadline, and local capability registration are enforced before acceptance or execution.
- Traceability: every task transition is journaled with sequence, actor, correlation, causation, prior state, next state, and reason.
- Input robustness: 2,048 randomized protobuf buffers and 512 randomized task cases execute without panic, alongside frame and semantic limits.
- Platform: the current tree passes workspace tests and S2 on Linux ARM64 and AMD64 containers using Rust 1.85.1.
- Footprint: release `umpd` measured 3,360 KiB idle RSS and 0.0% CPU after five seconds; binaries are 3,950,432 bytes (`umpd`) and 4,918,048 bytes (`ump`).

Phase 2 is complete. This establishes protocol/runtime behavior only; it does not certify physical motion, functional safety, or production security.

## Phase 3 exit audit

- Resource model: exclusive, shared, and capacity resources plus atomic lexically ordered multi-resource reservations are implemented.
- Reservation lifecycle: release, revocation, expiry, monotonic revisions, capacity recovery, and affected-task/handoff notifications are implemented.
- Spatial contract: SI pose, frame identity, freshness, quaternion validity, and position/orientation uncertainty are enforced before handoff progress.
- Handoff lifecycle: proposal, preparation, readiness, transfer, bilateral evidence, commit, abort, failure, unknown outcome, restart, and reconciliation are durable.
- Ownership invariant: bounded exhaustive restart/abort points and S3 report zero violations; ownership changes only in the bilateral commit event.
- Fault behavior: competing reservations, destination-not-ready, stale/unresolved transforms, timeout, source/destination crashes, transfer abort, and contradictory evidence are covered.
- Integration: authenticated QUIC accepts reservations and handoff proposals only with separate coordination permission.
- SDK/profile: Rust runtime, Python wire binding, C++17 adapter preview, and vendor-neutral material-movement profile are present.
- Cross-language vectors: Rust and Python decode and round-trip the Phase 3 handoff proposal.
- Platform: 48 Rust tests, eight Python tests, C++ syntax validation, and S3 pass locally and on Linux ARM64/AMD64.
- Footprint: release `umpd` with task/resource/coordination journals measured 3,664 KiB idle RSS and 0.0% CPU; binaries are 4,323,824 bytes (`umpd`) and 5,430,784 bytes (`ump`).

Phase 3 is complete. S3 is logical and deterministic; no claim is made yet about ROS 2, physics, real transforms, physical grasping, or functional-safety certification.

## Phase 4 progress

- The pinned Ubuntu 24.04 / ROS 2 Jazzy / Gazebo Harmonic container builds three ROS packages.
- Lifecycle, diagnostics, TF, capability-action, and local Unix adapter mappings are implemented.
- `umpd` durably permits adapter work through `next_task`, `progress`, `complete`, and `state_update`.
- The deterministic S4 world contains a controllable mobile base, articulated arm, package, and transfer zone.
- Seventeen ROS adapter/controller/profile tests and the Rust local-adapter contract tests pass.
- The daemon local API now carries controller progress, task-status polling, cancellation,
  and actual ROS-derived operational/safety state into its persisted snapshot.
- `s4_stack.launch.py` composes the world, two controllers, two lifecycle adapters,
  and automatic configure/activate transitions.
- Authenticated CLI surfaces now cover task issue/query/cancel, resource
  reserve/release, and handoff propose/update/query operations.
- `make ros2-s4` packages identity setup, trust enrollment, leases, independent
  runtimes, the six-step nominal mission, terminal polling, and JSONL tracing.
- `make sim-s4-protocol` passes the same six-task mission through real QUIC
  daemons and deterministic controller fixtures on any supported architecture.
- The S4 mission now includes a fourth zone-authority runtime, exclusive
  reservation and release, fresh spatial context, bilateral evidence, and a
  revision-7 ownership commit from arm to mobile base.
- Every nominal S4 run emits a machine-checked invariant report proving six
  unique task completions, ordered reservation and handoff revisions, bilateral
  authenticated evidence, commit-only ownership change, and failure-free delivery.
- Mobile navigation now detects sustained lack of physical progress and returns
  retryable `ros.navigation_blocked`; the Gazebo payload plugin exposes one-shot
  grasp failure and forced-drop instrumentation with explicit physical states.
- S4 now uses simulation time and publishes concrete world-frame TF for the
  mobile base and arm tool. The adapter rejects stale/future timestamps,
  non-finite or non-normalized transforms, and bounded translation/rotation
  discontinuities with stable diagnostic reason codes.
- The native-only `make ros2-s4-faults` gate injects stale and discontinuous
  mobile TF, requires invalid `tf.stale` and `tf.translation_discontinuity`
  spatial samples plus recovery, spawns a checked-in collision wall, and
  requires the resulting UMP task timeline to contain `ros.navigation_blocked`.
- Spatial samples now carry explicit position and orientation uncertainty. The
  native fault gate injects excessive localization uncertainty, requires an
  invalid `localization.position_uncertainty` sample, and proves recovery after
  the estimator returns within the configured capability limits.
- A Gazebo zone-monitor plugin now observes the physical transfer bounds at the
  declared `world` pose, publishes unapproved model intrusion, and a dedicated
  ROS node reports the resource event to the local zone authority. The daemon
  resolves and revokes active claims; sensor code never receives ownership authority.
- A ROS payload monitor reports physical drops to the coordination authority
  through a bounded subject-fault method. A transfer-stage drop becomes
  inspection-required unknown without changing the last authoritative owner;
  failed grasp is driven through the actual UMP task/action/timeline path.
- Protective and emergency events cancel active ROS actions and are latched in
  the adapter. A later normal or lower-severity ROS event cannot remotely reset
  or downgrade the stop, and accepted work remains unclaimed.
- The mobile base is now a replaceable Gazebo resource. A compact alternate
  model differs in mass, geometry, wheel radius, and track width while retaining
  the adapter contract. Native CI runs the unchanged six-task mission against
  both models and stores a separate invariant report for the swap run.
- The read-only `ump inspector` surface aggregates local identity/version,
  runtime state and health, peers, trust permissions, capabilities, tasks,
  leases, resources, reservations, handoffs, event histories, and fault IDs.
  Every S4 campaign exports four runtime snapshots plus a machine-checked
  nominal or fault report; secret credential fields are prohibited by the verifier.
- S4 integration fault checks now cover cancellation during execution,
  executor restart in accepted/running states, lease expiry, 5% and 10% packet
  loss, burst loss, 50-500 ms delay with deterministic jitter, emergency-state
  claim gating, transfer-zone expiry, and safe unknown recovery.
- A zone sensor can revoke its active reservation through the bounded local API;
  the S4 intrusion scenario proves transfer becomes inspection-required unknown
  without changing arm ownership. Protocol 1.2 also completes an authenticated
  task with an arm capped at the previous compatible minor 1.1.
- Native Linux CI now owns the live Gazebo smoke and complete embodied S4 gates,
  uploading the mission trace and logs even on failure. Gazebo Transport cannot
  start under the current AMD64-on-Apple-Silicon QEMU environment; SDF validation passes locally.
- Nominal reference and model-swap runs now sample Linux process RSS and CPU
  time for all three robot runtimes throughout the mission and emit a
  provenance-rich `performance.json`. The report enforces a 64 MiB active-peak
  RSS ceiling per runtime, a conservative 25 MiB combined `ump` plus `umpd`
  binary-size ceiling, complete sampling, and a passing mission invariant
  report. The sampler and report gates pass fixture and Linux `/proc` tests;
  native Gazebo values remain pending CI.

## Phase 4 next

- Validate the complete task/reservation/handoff sequence for both reference
  and alternate mobile models against native Gazebo physics.
- Observe the configured payload, physical-intrusion, safety, stale-TF,
  localization-error, and blocked-obstacle gates on native Ubuntu.
- Confirm the Gazebo live smoke on native Ubuntu CI; Gazebo Transport cannot initialize
  multicast under the local AMD64-on-Apple-Silicon QEMU environment.
- Publish the reference and alternate-model native performance reports and add
  command acknowledgement, emergency propagation, discovery, and recovery
  latency measurements under declared load conditions.

## Phase 5 progress

- A dependency-free generic HTTP adapter host maps durable UMP local tasks to
  allowlisted vendor controller operations without accepting task-selected URLs,
  paths, credentials, or timeouts.
- The vendor bridge requires HTTPS outside explicit loopback development,
  supports bearer secrets supplied by environment and mTLS client identity,
  validates server trust, rejects redirects, and bounds input, response, polling,
  controller timeout, and retry metadata.
- Cancellation, deadlines, read-only commissioning, stable failure mapping, and
  newline-JSON Unix local API behavior are covered by ten executable tests using
  a real loopback HTTP server. A runnable representative controller and
  industrial PLC/field-interface integration guidance are included.
- Machine descriptors now distinguish direct deployment from an external
  gateway proxy and carry the gateway ID, represented machine ID, controller
  interface profile, and read-only status. Receivers reject incomplete proxy
  metadata and associations that differ from the authenticated source identity.
- The external gateway supervisor verifies local runtime association before
  operating, probes controller health before claiming commands, atomically emits
  unambiguous operator status, advances state revisions across restart, and maps
  observation-only controllers without calling `next_task`.
- `make sim-s5` runs the unchanged six-task QUIC S4 mission first through an
  onboard SDK/HTTP bridge and then through an external gateway. Eleven checked
  invariants prove equivalent task count and committed ownership, direct/proxy
  distinction, controller disconnect and recovery, and gateway restart continuity.

- A reproducible Debian builder packages `ump`, `umpd`, the Apache-2.0 notice,
  maintainer scripts, and a hardened systemd unit for Linux `amd64` and `arm64`.
- The package creates a locked `ump` service account and private persistent
  state directory. Removal stops the service and preserves identity and audit
  data for explicit decommissioning.
- An actual Linux package install/remove test verifies account ownership,
  `0700` state permissions, installed CLI execution, binary removal, and state
  preservation. Archive and policy checks run in CI on both architectures.
- A clean Ubuntu lifecycle gate installs one package version, initializes and
  runs a machine, upgrades, deliberately rolls back, and uninstalls. `doctor`
  succeeds after both transitions, credentials and configuration remain byte-
  identical, and runtime state remains available after removal. The gate passed
  locally with the native ARM64 artifact and is configured for both CI runners.
- `ump rotate-development-credentials` requires exact machine confirmation and
  an offline daemon, validates a new credential set, preserves policy and
  journals, archives the old set under private permissions, and reports both
  fingerprints with mandatory peer re-enrollment. Unit tests cover rotation,
  mismatched confirmation, changed identity, state preservation, and refusal
  while the daemon is active; both package and rootless-container lifecycle
  gates exercise the command.
- A deterministic standalone Linux bundle now contains `ump`, `umpd`, the
  hardened service unit, license, source/version manifest, and internal
  checksums. The ARM64 bundle validates and reproduces byte-for-byte for fixed
  inputs.
- Tag-driven release automation is configured to test both native architectures,
  publish Debian packages and standalone bundles, attach keyless Sigstore
  signatures and GitHub provenance, and push a signed `amd64`/`arm64` OCI
  manifest with maximal provenance and an SBOM. This is configured evidence,
  not a claim that an official tag release has already run.
- The ROS 2 Jazzy packages now rebuild at the final relocatable
  `/opt/ump/ros/jazzy` prefix and are packaged as `ump-ros2-jazzy`. A clean
  AMD64 install test proves the development workspace is absent, then verifies
  all three package prefixes, generated action introspection, Python imports,
  launch metadata, and the declared native-runtime dependency.
- CI runs the ROS package gate with the existing Jazzy/Gazebo suite. Tag release
  automation builds, signs, attests, and publishes the package for Linux
  `amd64` and `arm64`. The migration guide preserves existing autonomy and
  certified safety ownership while introducing the UMP adapter boundary.
- A minimal OCI image runs `umpd` as numeric UID/GID 65532 with a dedicated
  persistent volume and includes `ump` for initialization and inspection. The
  ARM64 image passes identity initialization, persistence, `doctor`, one-shot
  runtime startup, stop/start recovery, and state-ownership checks across
  separate containers. Two rootless identities also exchange explicit trust and
  complete authenticated, capability-scoped task submission over QUIC. Native
  Linux CI uses an isolated bridge; local Docker Desktop uses a shared network
  namespace because its VM drops this cross-container UDP path.

## Phase 5 next

- Run the first tag release and independently verify every uploaded signature,
  provenance statement, checksum, package lifecycle, and container digest.
- Add production credential enrollment and external revocation integration.
- Run the ROS packaging gate on native ARM64 and verify its published signature
  and provenance in the first tag release.
- Run the complete S5 fleet with the installed ROS 2 package on native Jazzy,
  retaining the same native participant, controller bridge, coordinator, and
  external-gateway relocation used by the architecture-neutral checkpoint.
