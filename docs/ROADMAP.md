# UMP Engineering Roadmap

## 1. Roadmap approach

This roadmap is organized around tangible interoperability increments. Each phase ends with working software, a repeatable simulation, measured results, and a decision gate. Calendar dates are assigned only after the initial team and capacity are known; effort is expressed as indicative engineering weeks for a small core team of three to five engineers.

The sequence deliberately proves semantics in deterministic simulation before adding physical complexity. Hardware testing starts before v1 protocol freeze so real-world findings can still change the design.

## 2. Workstreams

The project runs seven parallel workstreams:

- **Specification:** terminology, schemas, state machines, compatibility, profiles.
- **Runtime:** `umpd`, sessions, storage, policy, state machines, diagnostics.
- **SDKs and adapters:** Rust, Python, C++, mock, ROS 2, vendor bridges.
- **Security and safety:** identity, trust, authorization, threat model, adapter contracts.
- **Simulation and conformance:** virtual machines, network faults, Gazebo, test vectors.
- **Developer experience:** CLI, installer, examples, documentation, inspector.
- **Governance and ecosystem:** license, UEPs, releases, compatibility, partnerships.

## 3. Phase 0: foundation and decisions

**Indicative effort:** 2-3 weeks  
**Outcome:** The project can accept contributions without foundational ambiguity.

### Deliverables

- Ratified charter, scope, terminology, and product principles.
- Repository structure and continuous integration.
- License, contribution guide, code of conduct, governance draft, and security policy.
- Architecture decision records for runtime language, schema format, transport prototype, and adapter boundary.
- Initial threat model and safety-boundary statement.
- Versioning policy and UEP template.
- Benchmark hardware profiles for `amd64` and `arm64`.
- Simulation architecture and scenario format.

### Technical spike

Prototype one authenticated request/response using Protocol Buffers over two candidate local transports. Measure startup time, binary size, idle memory, p50/p95 latency, reconnect behavior, and implementation complexity.

### Simulation checkpoint S0: two virtual machines say hello

Run two in-process virtual machines with a simulated clock. They exchange identity, versions, and a heartbeat. Tests prove deterministic expiry and reject malformed version negotiation.

### Exit gate

- Core decisions recorded with owners and rationale.
- Clean build and tests on Linux `amd64` and `arm64` CI or representative emulation/hardware.
- S0 runs with one command and emits a machine-readable report.
- No unresolved disagreement on UMP's safety boundary.

## 4. Phase 1: protocol kernel

**Indicative effort:** 4-6 weeks  
**Outcome:** Secure peers can discover one another and agree on identity, compatibility, capabilities, and state.

### Specification

- Common envelope and identifier rules.
- Identity, session, endpoint, and presence schemas.
- Version and feature negotiation.
- Capability descriptor and revision rules.
- Operational, health, safety, and telemetry state.
- Error taxonomy and correlation rules.

### Runtime and SDK

- Minimal `umpd` process with configuration and lifecycle.
- Local development credential generation and mutual authentication.
- Peer table, presence expiry, and reconnect.
- Capability registry and state publisher.
- In-memory transport and first secure network transport.
- Rust core API and Python SDK preview.
- CLI commands: `ump init`, `ump run`, `ump peers`, `ump inspect`, `ump doctor`.

### Simulation checkpoint S1: heterogeneous discovery lab

Simulate at least 20 virtual machines across four classes: mobile base, arm, drone, and fixed sensor. Each advertises different features and capability revisions.

Faults include delayed startup, duplicate packets, unsupported major version, expired credentials, disappearing peers, stale state, and a telemetry flood.

### Measurements

- Discovery convergence time.
- False-live and false-offline transitions.
- Idle/runtime memory and CPU.
- Negotiation outcomes by version pair.
- Telemetry backpressure behavior.

### Exit gate

- Required S1 scenarios pass deterministically in CI.
- Unauthorized peers cannot access protected capabilities or operational state.
- The runtime meets provisional idle memory target.
- Schemas have golden encode/decode vectors in at least Rust and Python.

## 5. Phase 2: task lifecycle and authority

**Indicative effort:** 5-7 weeks  
**Outcome:** One machine can authorize another to request high-level work and both converge on its outcome.

### Specification

- Task and command schemas.
- Task lifecycle and terminal-state rules.
- Progress, result, cancellation, deadlines, and idempotency.
- Authority grants and leases.
- Structured failure, retry, and recovery.
- Durable reconciliation after restart.

### Runtime and SDK

- Task state machine and handler registration.
- Policy checks for capability invocation.
- Lease manager with renewal, revocation, and expiry.
- Idempotency store and durable event journal.
- Reconnect reconciliation protocol.
- Python handler API and Rust adapter API.
- Trace export and task timeline inspection.

### Simulation checkpoint S2: delivery task under faults

A virtual coordinator assigns a package-delivery task to a mobile robot. A fixed sensor provides zone state. The robot obtains an authority lease, accepts work, reports progress, and completes.

The suite repeats with:

- cancellation before start and during execution;
- duplicate task delivery;
- expired command;
- process crash after acceptance;
- network partition during execution;
- lease expiry and revocation;
- coordinator restart;
- conflicting issuers; and
- non-idempotent action with unknown outcome.

### Exit gate

- No duplicate physical-action surrogate occurs across every retry scenario.
- Issuer and executor converge on terminal state or explicit `unknown/reconcile` state.
- No command executes outside valid authority.
- Every transition is visible in a correlated event trace.
- Fuzzed task inputs do not crash the runtime.

## 6. Phase 3: resources, spatial context, and handoffs

**Indicative effort:** 6-8 weeks  
**Outcome:** Multiple machines coordinate shared space and transfer responsibility for a physical subject.

### Specification

- Resource and reservation model.
- Coordinate frame, transform, units, time, and uncertainty model.
- Handoff roles, states, evidence, commit, abort, and reconciliation.
- Ownership representation.
- Domain profile draft for material movement.

### Runtime and SDK

- Resource registry and reservation manager.
- Deadlock avoidance and bounded acquisition.
- Frame-resolution hooks for adapters.
- Handoff state machine and durable recovery.
- Scenario invariant checker.
- C++ SDK preview for ROS 2 adapter work.

### Simulation checkpoint S3: logical package handoff

A virtual mobile robot and arm reserve a transfer zone and hand off a package. The simulation tracks package ownership and position as authoritative state.

Fault variants include competing zone reservations, destination not ready, stale transform, transfer timeout, source crash before commit, destination crash after commit, abort during transfer, and contradictory evidence.

### Exit gate

- Ownership invariant holds in all model-checked or exhaustive bounded scenarios.
- No conflicting exclusive reservation survives reconciliation.
- Unresolvable frame or stale spatial state blocks execution.
- Handoff recovery never uses a blind physical retry.
- Material-movement profile is usable without vendor-specific fields.

## 7. Phase 4: ROS 2 and physics simulation MVP

**Indicative effort:** 6-8 weeks  
**Outcome:** UMP coordinates visibly different simulated robots performing a shared physical task.

### Platform work

- ROS 2 adapter for actions, services, topics, diagnostics, lifecycle, and TF.
- Gazebo simulation package and reproducible world.
- Mobile base navigation capability mapping.
- Robot arm pick/place capability mapping.
- Simulated payload and handoff-zone instrumentation.
- Reference coordinator that uses only UMP APIs.
- Inspector view for peers, tasks, leases, resources, and handoff timeline.

### Simulation checkpoint S4: embodied warehouse handoff

Nominal demonstration:

1. Start the simulated world and independent UMP runtimes.
2. Discover a mobile robot, arm, and zone sensor.
3. Match capabilities for moving a package from source to destination.
4. Grant scoped authority and reserve the handoff zone.
5. Navigate the mobile robot to the transfer pose.
6. Resolve coordinate frames and readiness conditions.
7. Execute arm pickup and place onto the mobile robot.
8. Commit package ownership.
9. Navigate to destination and complete the parent task.

Fault campaign:

- 5%, 10%, and burst packet loss;
- 50-500 ms latency and jitter;
- runtime restart at each handoff state;
- blocked navigation path;
- failed grasp or dropped payload;
- stale or discontinuous transform;
- handoff-zone intrusion;
- protective stop and emergency-state broadcast;
- lease revocation during approach; and
- one robot running the previous compatible protocol minor version.

### Artifacts

- One-command headless CI scenario.
- Visual demonstration recording generated from the same scenario.
- Event trace and invariant report.
- Benchmark report with declared hardware and network profile.
- Adapter mapping guide.

### MVP exit gate

- All PRD MVP requirements are demonstrated.
- Nominal scenario succeeds repeatedly without manual intervention.
- Every injected fault reaches the specified safe or recoverable state.
- Coordinator contains no ROS 2 or robot-vendor-specific logic.
- Swapping the mobile robot model for another conforming model requires only adapter/configuration changes.

## 8. Phase 5: installation paths and gateway

**Indicative effort:** 5-7 weeks  
**Outcome:** Users can adopt UMP through any one of the three primary paths, with a tested fallback.

### Native path

- Signed standalone binaries for Linux `amd64` and `arm64`.
- Debian package and service unit.
- Rootless container image.
- Cross-compilation and release automation.
- Resource-constrained benchmark and configuration profile.

### ROS 2 path

- Installable ROS 2 package for the selected supported distribution.
- Launch files, parameters, lifecycle behavior, and diagnostics.
- Example robot integration and migration guide.

### Vendor bridge path

- Generic adapter host API.
- HTTP/gRPC controller example and industrial field-interface design guidance.
- Adapter security and safety contract template.
- Launch-partner adapter or a representative open controller integration.

### Gateway fallback

- Explicit proxy identity and machine association.
- Isolation between gateway host and represented machine.
- Defined behavior for controller disconnect and gateway restart.
- Read-only mode for machines that expose observation but not commands.

### Simulation checkpoint S5: mixed installation fleet

Run the S4 shared task with one native runtime, one ROS 2 package, and one SDK/controller bridge. Repeat with the bridged participant moved to an external gateway without changing other peers or the coordinator.

### Exit gate

- Fresh-machine installation succeeds from documentation for all three paths.
- A user needs only one path for a conforming integration.
- Gateway proxy status is never ambiguous to peers or operators.
- Uninstall, upgrade, rollback, and credential rotation are tested.
- The core shared task behaves identically across deployment forms.

## 9. Phase 6: security, scale, and interoperability hardening

**Indicative effort:** 6-10 weeks  
**Outcome:** The implementation is credible for external pilots and independent implementations.

### Security

- Complete threat model and protocol abuse cases.
- Fuzz all network and schema boundaries.
- Penetration test and independent design review.
- Credential rotation, revocation, offline enrollment, and recovery exercises.
- Rate limits, quotas, malformed-message isolation, and audit export.
- Signed releases, software bill of materials, and reproducible-build progress.

### Reliability and scale

- 100-machine discovery and task benchmark.
- Long-duration soak tests.
- Network partition and split-brain campaigns.
- Clock drift and time-source degradation tests.
- Storage exhaustion, corrupt journal, and low-memory behavior.
- Rolling minor-version upgrade matrix.

### Interoperability

- Public conformance harness and test vectors.
- Wire capture fixtures and reference traces.
- Independent implementation guide.
- Interop event with at least one non-reference implementation.

### Simulation checkpoint S6: adversarial facility

Run 100 mixed virtual machines for 24 hours with scheduled faults, credential rotation, rolling upgrades, malicious inputs, saturated telemetry, and concurrent resource use. A smaller physics subset performs repeated handoffs throughout.

### Exit gate

- No critical unresolved security issue.
- Published performance targets pass under declared conditions.
- No invariant violation during soak and adversarial campaigns.
- At least one independent implementation passes Core Observer and Core Participant tests.
- Upgrade compatibility window is proven, not assumed.

## 10. Phase 7: hardware-in-the-loop and pilot

**Indicative effort:** 6-12 weeks, dependent on hardware access  
**Outcome:** UMP coordinates real heterogeneous machines in a controlled environment.

### Hardware progression

1. Real onboard computer running `umpd` with mock actuators.
2. Real robot in read-only discovery and telemetry mode.
3. Real robot accepts non-motion tasks.
4. Real robot executes bounded supervised motion with simulated peers.
5. One simulated and one physical machine perform a shared task.
6. Two physical machines from different stacks perform a supervised handoff.
7. Pilot deployment runs repeated tasks with operator oversight.

### Required controls

- Adapter hazard analysis and safety contract.
- Physical emergency stop and trained operator.
- Controlled zone and speed/force limits.
- Preflight identity, network, time, frame, lease, and health checks.
- Incident capture and rollback procedure.
- No remote emergency reset by default.

### Simulation checkpoint S7: digital twin rehearsal

Before each physical test, run the same task and fault plan against digital twins or representative simulated models. Store the expected event trace, safety transitions, and recovery decisions for comparison with hardware.

### Exit gate

- Two heterogeneous physical machines complete the reference shared task.
- Disconnect, cancellation, lease expiry, protective stop, and recovery are demonstrated safely.
- Observed hardware behavior matches adapter contracts and protocol traces.
- Pilot feedback produces no unresolved v1-blocking semantic issue.

## 11. Phase 8: v1 release candidate and launch

**Indicative effort:** 4-6 weeks  
**Outcome:** Stable specification, supported artifacts, and credible conformance claims.

### Deliverables

- UMP Specification 1.0 release candidate and final.
- Runtime and SDK 1.0 release candidates.
- Signed Linux `amd64` and `arm64` artifacts.
- Supported ROS 2 package.
- Core Observer, Participant, Coordinator, Handoff, and Gateway conformance profiles as completed.
- Security review report and resolved findings.
- Compatibility matrix and support policy.
- Tutorials for native, ROS 2, vendor bridge, and gateway paths.
- Reference simulations, traces, and benchmark reports.
- Governance body and release process.

### Final simulation checkpoint S8: release qualification

Freeze code and schemas, then rerun every deterministic, network, physics, mixed-installation, security, scale, compatibility, and hardware scenario from a clean environment. Release artifacts, not developer builds, must be tested.

### v1 exit gate

All v1 release criteria in the PRD pass, known limitations are documented, and no required behavior exists only in the reference implementation without normative specification and conformance coverage.

## 12. Post-v1 candidate roadmap

Post-v1 work is evidence driven and may include:

- additional domain profiles for inspection, charging, tool changing, and coordinated lifting;
- constrained embedded profile using CBOR or another standardized binding;
- CAN, OPC UA, MAVLink, and additional industrial adapters;
- cross-site/federated discovery and policy;
- privacy-preserving capability disclosure;
- richer spatial maps and negotiated bulk-data streams;
- deterministic real-time companion profile where justified;
- formal verification of leases and handoffs;
- certified conformance labs; and
- long-term standards-body submission.

## 13. Milestone dependency map

```text
Foundation
  -> Identity + discovery + capabilities
    -> Tasks + authority
      -> Resources + spatial context + handoffs
        -> ROS 2/Gazebo MVP
          -> All installation paths
            -> Security + scale + independent interop
              -> Hardware pilot
                -> v1.0
```

Security, safety, observability, documentation, and simulation run through every phase rather than arriving at the end.

## 14. Recommended first backlog

The first implementation cycle should produce these issues in order:

1. Adopt license and contribution/governance baseline.
2. Define glossary and normative requirement style.
3. Create repository layout, CI, formatting, and release skeleton.
4. Write the v0 envelope, identity, endpoint, and negotiation schemas.
5. Implement a simulated clock and in-memory transport.
6. Implement two virtual machine runtimes and presence expiry.
7. Add golden schema vectors and compatibility tests.
8. Prototype mutual authentication and one secure network transport.
9. Add capability and state schemas plus Python SDK bindings.
10. Build S1 heterogeneous discovery scenario and report generator.
11. Benchmark on representative `amd64` and `arm64` systems.
12. Review Phase 1 evidence before designing task execution in code.

## 15. Release evidence package

Every milestone release should publish:

- specification and schema version;
- source commit and build provenance;
- supported platform matrix;
- conformance results;
- simulation scenario results;
- performance report;
- security test summary;
- compatibility test matrix;
- known limitations; and
- upgrade and rollback notes.

This evidence makes progress tangible and gives manufacturers a stable basis for evaluating adoption.

