# Universal Machine Protocol (UMP)

## Product Requirements Document

**Document status:** Draft v0.1  
**Project stage:** Definition  
**License intent:** Open source; final license selection is a Phase 0 decision  
**Primary artifact:** Protocol specification plus conforming runtime and SDKs

## 1. Executive summary

Universal Machine Protocol is a common communication layer for autonomous machines. It allows heterogeneous machines from different manufacturers and technology stacks to participate in a shared task without requiring a shared robot operating system, cloud platform, or autonomy product.

UMP standardizes the information and behaviors needed for machine collaboration:

- identity and secure authentication;
- discovery, presence, and liveness;
- capability advertisement and matching;
- state, health, telemetry, and spatial context;
- tasks, commands, responses, and progress;
- resource reservation and coordination;
- machine-to-machine handoffs;
- safety state and emergency signals;
- command authority and time-bounded leases;
- cancellation, retries, failures, and recovery; and
- protocol-version and feature negotiation.

UMP will be distributed as an open specification, machine-readable schemas, a lightweight runtime called `umpd`, SDKs, adapters, conformance tests, and simulation examples. It must function locally without a required cloud service. Cortex or any other orchestration product may use UMP, but none is required.

## 2. Problem

Robots can often communicate within one manufacturer's fleet or one middleware environment, but cross-vendor collaboration requires custom integrations. The same concepts are represented differently: identity, coordinate frames, capabilities, task state, safety state, authority, and errors. Even when two machines can exchange bytes, they may not agree on meaning, lifecycle, or responsibility.

This creates four recurring problems:

1. Every machine pairing becomes a bespoke integration.
2. Integrators cannot reliably discover what a machine can do at runtime.
3. Handoffs and shared-resource use lack portable semantics.
4. Failure, authority, and safety behavior are ambiguous at system boundaries.

UMP solves the interoperability boundary. It does not attempt to standardize every internal robot subsystem.

## 3. Product vision

An integrator should be able to add UMP to each compatible machine using one available installation path, place the machines on an authorized network, and observe them discover one another and safely perform a shared task through a common protocol.

The long-term test of success is simple: a new conforming robot can join an existing UMP deployment without changes to every other robot.

## 4. Goals

### 4.1 Product goals

- Provide vendor-neutral semantics for heterogeneous machine collaboration.
- Require no extra hardware when a machine supports direct software, ROS 2, or a vendor integration interface.
- Run robot-to-robot on a local network without cloud dependency.
- Support centralized, decentralized, and hybrid coordination models.
- Keep the core runtime small enough for common Linux `amd64` and `arm64` onboard computers.
- Make protocol behavior inspectable, testable, and implementable by third parties.
- Preserve robot-native control and safety boundaries.
- Offer a gradual adoption path: observe first, then coordinate, then perform handoffs.
- Make interoperability claims verifiable through public conformance tests.

### 4.2 Engineering goals

- Define stable, language-neutral schemas with explicit compatibility rules.
- Separate transport-independent semantics from transport bindings.
- Provide secure-by-default identity, encryption, authorization, and replay protection.
- Tolerate packet loss, reconnects, duplication, reordering, and partial machine failure.
- Support bounded command deadlines, idempotency, cancellation, and recovery.
- Record enough structured events to reconstruct task and authority history.

## 5. Non-goals

UMP v1 will not:

- replace a robot operating system, PLC, flight controller, or autonomy stack;
- define motor currents, servo loops, or hard real-time actuator control;
- claim to replace certified functional-safety systems or physical emergency stops;
- provide universal path planning, perception, SLAM, manipulation, or fleet optimization;
- translate arbitrary vendor capabilities automatically without an adapter;
- require a cloud account, global registry, blockchain, or proprietary coordinator;
- guarantee that two advertised capabilities are physically compatible without declared constraints;
- stream unbounded high-bandwidth sensor media through the control plane by default; or
- permit a remote peer to bypass local safety policy.

## 6. Users and stakeholders

### Robot and machine manufacturers

Manufacturers implement UMP directly or publish a supported adapter. They need stable schemas, certification tooling, low integration cost, and a clear extension mechanism.

### Robotics integrators

Integrators connect mixed fleets and production systems. They need discovery, capability matching, diagnostics, predictable task lifecycles, and vendor-independent error handling.

### Robotics developers and researchers

Developers need SDKs, local simulation, examples, readable logs, and fast installation on personal machines and development robots.

### Operators and safety owners

Operators need visible machine identity, authority, health, current work, emergency state, and an auditable history of decisions and handoffs.

### Standards and open-source community

Contributors need an open governance process, specification versioning, reference vectors, and an objective route to conformance.

## 7. Product principles

1. **One path is enough.** A robot is UMP-capable if any approved integration path works.
2. **Local first.** Core collaboration continues without internet or a vendor cloud.
3. **Meaning before transport.** Task and safety semantics must remain stable across network bindings.
4. **Least authority.** A peer receives only the capabilities and duration it needs.
5. **Local safety wins.** A machine may always reject or stop work based on local policy.
6. **Explicit state.** Important transitions are acknowledged, timed, and observable.
7. **Graceful heterogeneity.** Optional features are negotiated, not assumed.
8. **Simulation is a release gate.** Every meaningful protocol increment is demonstrated under nominal and failure conditions.

## 8. Installation and deployment model

### 8.1 Path A: native runtime

`umpd` runs as a native service or container on the robot's onboard computer and talks to the robot through a local adapter. Initial targets are Linux `amd64` and `arm64`. Packaging should include a standalone binary, Debian package, container image, and documented source build.

Best for machines that permit third-party processes and expose local APIs, IPC, CAN, serial, or network interfaces.

### 8.2 Path B: ROS 2 package

A ROS 2 adapter maps topics, services, actions, lifecycle state, transforms, and diagnostics to UMP. The UMP runtime may run in-process with the adapter or as a nearby daemon.

Best for research robots, many mobile robots, humanoid development platforms, and custom robotic systems already using ROS 2.

### 8.3 Path C: vendor SDK or controller bridge

An approved adapter uses the manufacturer's supported SDK, controller API, fieldbus, or application environment. The adapter exposes only behaviors allowed by the vendor and machine owner.

Best for commercial and industrial machines with controlled extension points.

### 8.4 Fallback: external gateway

When a machine is locked down, a gateway hosts `umpd` and communicates with the machine over an approved external interface. The gateway is logically part of that machine's trust boundary and must clearly advertise that it is a proxy.

The gateway is not assumed to make an inaccessible robot controllable. If no supported interface exists, the robot cannot safely participate beyond externally observed state.

## 9. System model

### 9.1 Logical components

- **UMP Runtime (`umpd`):** identity, sessions, discovery, protocol state machines, authorization, leases, routing, persistence, and telemetry.
- **Machine Adapter:** translates between UMP concepts and the native robot/controller interface.
- **SDK:** lets applications and adapters publish capabilities, receive tasks, and report state.
- **Transport Binding:** carries UMP envelopes over a supported network transport.
- **Registry (optional):** assists discovery and policy across routed or large deployments.
- **Coordinator (optional):** decomposes work and assigns tasks. Peer-to-peer operation remains possible.
- **Inspector:** developer tool for observing peers, schemas, tasks, leases, events, and faults.
- **Conformance Harness:** validates protocol implementations against required behavior.

### 9.2 Machine abstraction

Every machine exposes:

- a stable identity and current session identity;
- one or more endpoints;
- a machine class and descriptive metadata;
- supported protocol versions and features;
- capabilities with inputs, outputs, constraints, and safety requirements;
- current operational, health, safety, and connectivity state;
- zero or more resources it owns or can reserve;
- coordinate frames and relevant spatial state;
- task and command endpoints authorized for the current peer; and
- structured errors and recovery suggestions.

### 9.3 Communication planes

- **Discovery plane:** low-volume presence and endpoint negotiation.
- **Control plane:** commands, tasks, acknowledgements, leases, handoffs, and safety events.
- **Telemetry plane:** rate-controlled state and health streams.
- **Data plane:** references or negotiated streams for larger artifacts such as maps, images, and point clouds.

Control messages must never be delayed behind bulk data. Large payloads are transferred out of band and referenced by content metadata, URI, checksum, authorization, and expiry.

## 10. Functional requirements

Requirement levels use **MUST**, **SHOULD**, and **MAY** in their standards sense.

### 10.1 Identity and trust

- `ID-01` Each runtime MUST have a globally unique machine identifier that does not depend on its IP address.
- `ID-02` Each runtime session MUST have a unique session identifier and boot timestamp.
- `ID-03` Peers MUST mutually authenticate before accepting privileged messages.
- `ID-04` Production bindings MUST encrypt traffic in transit.
- `ID-05` Credentials MUST support rotation and revocation without changing the machine identifier.
- `ID-06` A gateway MUST declare the machine it represents and its proxy status.
- `ID-07` Authorization decisions MUST identify subject, action, resource, policy result, and time.
- `ID-08` Deployments MUST be able to operate with a local trust authority and no internet access.

### 10.2 Discovery and presence

- `DIS-01` A runtime MUST support local-network discovery or explicit peer configuration.
- `DIS-02` Presence MUST include identity, reachable endpoints, supported versions, and an expiry.
- `DIS-03` Discovery MUST not expose sensitive capabilities before policy permits it.
- `DIS-04` A runtime MUST detect expired peers and emit a deterministic presence transition.
- `DIS-05` Routed deployments SHOULD support an optional registry without changing peer semantics.

### 10.3 Version and feature negotiation

- `NEG-01` Peers MUST negotiate a mutually supported protocol major version before exchanging operational messages.
- `NEG-02` Unknown optional fields MUST not cause message rejection.
- `NEG-03` Required feature mismatch MUST produce a machine-readable incompatibility response.
- `NEG-04` Extensions MUST use namespaced identifiers.
- `NEG-05` A major-version mismatch MUST fail closed for commands while permitting minimal diagnostics where policy allows.

### 10.4 Capability advertisement

- `CAP-01` A capability MUST have a namespaced type, version, human label, input schema, output schema, and lifecycle.
- `CAP-02` A capability SHOULD declare physical constraints such as payload, reach, precision, speed, dimensions, environment, and required tooling where relevant.
- `CAP-03` A capability MUST declare whether it is observable, invocable, reservable, interruptible, and handoff-capable.
- `CAP-04` Dynamic capability availability MUST be distinguishable from static support.
- `CAP-05` Capability changes MUST carry a monotonic revision.
- `CAP-06` Implementations MUST validate task parameters against the declared schema before acceptance.

### 10.5 State, health, and telemetry

- `STA-01` Machines MUST publish operational state: offline, starting, idle, busy, paused, degraded, stopping, or faulted.
- `STA-02` Machines MUST publish safety state separately from operational state.
- `STA-03` Health MUST support component-level conditions with severity, code, message, first-seen time, and remediation metadata.
- `STA-04` Telemetry subscriptions MUST support rate limits and backpressure.
- `STA-05` State messages MUST include source time and sequence information.
- `STA-06` Receivers MUST be able to identify stale state.
- `STA-07` High-rate raw sensor streams SHOULD use a negotiated data-plane transport rather than core control messages.

### 10.6 Spatial context

- `SPC-01` Spatial values MUST identify a coordinate frame, units, timestamp, and uncertainty where applicable.
- `SPC-02` Each machine MUST expose a stable root frame or explicitly state that spatial capabilities are unavailable.
- `SPC-03` Frame relationships MUST be versioned or timestamped.
- `SPC-04` A receiver MUST reject a spatial command whose frame cannot be resolved.
- `SPC-05` Shared zones and resources SHOULD support geometry, occupancy, and validity intervals.

### 10.7 Tasks and commands

- `TSK-01` Every task MUST have a globally unique identifier, issuer, target, capability, parameters, priority, creation time, and deadline policy.
- `TSK-02` The lifecycle MUST include proposed, accepted, rejected, queued, running, paused, succeeded, failed, cancelled, and expired where applicable.
- `TSK-03` Terminal states MUST be immutable.
- `TSK-04` Duplicate submissions with the same idempotency key MUST not execute twice.
- `TSK-05` Acceptance MUST not imply completion and MUST identify the accepting machine.
- `TSK-06` Progress MUST be structured, monotonic where measurable, and allowed to include stage, percent, ETA, and evidence.
- `TSK-07` Cancellation MUST be acknowledged and may report immediate, deferred, or impossible-to-cancel behavior.
- `TSK-08` Deadlines MUST specify whether they govern start time, completion time, or message validity.
- `TSK-09` A machine MUST be allowed to reject work due to policy, safety, unsupported parameters, resources, or health.
- `TSK-10` Commands that can cause physical motion MUST require explicit authorization and an active authority scope.

### 10.8 Command authority and leases

- `AUT-01` Authority MUST be explicit, scoped, revocable, and time bounded.
- `AUT-02` A lease MUST identify holder, grantor, allowed actions, resources, issue time, expiry, and renewal policy.
- `AUT-03` Conflicting exclusive leases MUST not be active simultaneously.
- `AUT-04` Lease expiry or revocation MUST trigger a declared local safe behavior.
- `AUT-05` Clock uncertainty MUST be considered when evaluating leases.
- `AUT-06` A machine MUST preserve local operator and safety-controller precedence.
- `AUT-07` All authority transitions MUST be auditable.

### 10.9 Resource coordination

- `RES-01` Resources MUST have stable identifiers and concurrency semantics: exclusive, shared, or capacity based.
- `RES-02` Reservations MUST have lifecycle, owner, validity interval, and expiry behavior.
- `RES-03` Multi-resource acquisition MUST avoid indefinite deadlock through ordering, timeout, or transactional coordination.
- `RES-04` A reservation loss MUST notify affected tasks.
- `RES-05` Physical zones, tools, charging stations, payload slots, and communication channels MUST be representable.

### 10.10 Handoffs

- `HND-01` A handoff MUST identify source, destination, subject, preconditions, transfer point or frame, and completion evidence.
- `HND-02` Handoffs MUST use a two-party state machine: proposed, prepared, ready, transferring, committed, aborted, or failed.
- `HND-03` Exactly one party MUST be the authoritative owner of the subject before and after commit.
- `HND-04` Uncertain outcomes MUST enter reconciliation rather than silently retrying physical transfer.
- `HND-05` The source MUST retain responsibility until commit unless a domain profile explicitly defines another rule.
- `HND-06` Recovery instructions MUST distinguish retry-safe from inspection-required conditions.

### 10.11 Safety and emergency signals

- `SAF-01` Safety state MUST include normal, protective stop, emergency stop, recovery required, and unknown at minimum.
- `SAF-02` Emergency signals MUST use a priority path and MUST not depend on task queues.
- `SAF-03` Network emergency messages MUST be treated as advisory unless the deployment's certified safety design says otherwise.
- `SAF-04` Loss of communication MUST invoke a locally configured safe behavior.
- `SAF-05` Safety state MUST never be inferred solely from absence of telemetry.
- `SAF-06` Remote reset of an emergency condition MUST be prohibited by default.
- `SAF-07` UMP MUST document that it is not itself a functional-safety certification boundary.

### 10.12 Failure, retry, and recovery

- `ERR-01` Errors MUST include a stable code, category, retryability, origin, related task, time, and optional remediation.
- `ERR-02` Automatic retries MUST require idempotent semantics or a capability-declared retry strategy.
- `ERR-03` Retry behavior MUST use bounded attempts, deadlines, and backoff.
- `ERR-04` Reconnect MUST reconcile active tasks, leases, reservations, and handoffs.
- `ERR-05` Peers MUST distinguish rejection, execution failure, timeout, unknown outcome, and communication loss.
- `ERR-06` State recovery after restart MUST use durable records for accepted physical work.

### 10.13 Observability and audit

- `OBS-01` Every message MUST carry correlation and causation identifiers.
- `OBS-02` Protocol events MUST support structured logs and distributed traces.
- `OBS-03` Sensitive fields MUST be redactable by schema annotation.
- `OBS-04` Audit events MUST be tamper evident or exportable to an append-only system in production profiles.
- `OBS-05` Operators MUST be able to determine who commanded what, which machine accepted it, and the terminal result.

## 11. Protocol design

### 11.1 Envelope

Every UMP message has a common envelope containing:

- protocol major and minor version;
- message type and schema version;
- message, correlation, and causation identifiers;
- source and optional destination machine identifiers;
- source session identifier;
- sequence number;
- creation time, optional expiry, and clock quality;
- content type and payload;
- optional trace context;
- security context or transport-bound identity reference; and
- extension fields.

### 11.2 Schema format

The reference schema will use Protocol Buffers for compact, language-neutral encoding and code generation. A canonical JSON mapping will exist for debugging and web tooling. Schema evolution rules will prohibit field-number reuse and incompatible meaning changes within a major version.

The specification remains transport independent. Conforming implementations may use another encoding only when a standardized binding defines identical semantics and conformance vectors.

### 11.3 Initial transport profiles

- **Core secure session:** QUIC with TLS 1.3, reliable streams for control and optional datagrams for suitable telemetry.
- **Local discovery:** mDNS/DNS-SD for approachable development plus an authenticated UMP discovery exchange before trust.
- **ROS 2 bridge:** mapping to ROS 2 topics, services, actions, diagnostics, and TF where appropriate.
- **MQTT bridge:** optional fleet, constrained-network, and cloud integration profile; MQTT is not the sole core transport.
- **Developer profile:** loopback/in-memory transport for deterministic tests.

Transport choices are validated by benchmarks before v1 freeze. Semantics must not depend on QUIC-specific behavior.

### 11.4 Capability model

A capability descriptor includes:

```text
type: org.ump.material.pick
version: 1.0
inputs: object reference, pickup pose, constraints
outputs: grasp result, final object state
availability: available | reserved | unavailable | degraded
properties: payload, reach, precision, supported frames
execution: interruptible, idempotency policy, estimated duration
safety: required zone, human-presence policy, risk classification metadata
handoff: supported roles and compatible subject profiles
```

Common capabilities will be defined in domain profiles. Vendors can extend the namespace without changing the core protocol.

## 12. Security model

### 12.1 Threats in scope

- machine impersonation;
- unauthorized commands or capability access;
- replayed or duplicated commands;
- message tampering and downgrade attacks;
- compromised peer attempting lateral movement;
- credential theft and stale credentials;
- denial of service and telemetry flooding;
- malicious or malformed schema payloads; and
- audit deletion or ambiguity.

### 12.2 Required controls

- Mutual authentication using deployment-issued credentials.
- TLS 1.3 for network sessions in the production profile.
- Policy-based authorization at capability and action level.
- Nonces/session identifiers, sequence checks, and expiry for replay resistance.
- Input size, nesting, rate, and resource limits.
- Signed releases and verifiable build artifacts.
- Credential rotation, revocation, and short-lived operational credentials.
- Secure defaults with an explicitly marked insecure development mode.
- No unauthenticated motion command path.

### 12.3 Trust deployment modes

- **Single-owner site:** local certificate authority and local policy bundle.
- **Multi-vendor site:** federated trust roots and explicit cross-domain policy.
- **Development:** generated local credentials with visible non-production status.
- **Disconnected deployment:** offline enrollment, rotation package, and revocation list.

## 13. Safety model

UMP separates communication safety from physical functional safety. It transports declared safety state and coordinates safe behavior, but each machine retains responsibility for enforcing motion limits, collision avoidance, certified stops, and hardware interlocks.

Every adapter must define:

- which UMP actions can cause physical effects;
- native controller checks applied before execution;
- behavior on lease expiry, disconnect, stale spatial data, or invalid parameters;
- which safety states it can read and which it can request;
- what requires local human confirmation; and
- how recovery is performed.

No conformance badge may imply functional-safety certification.

## 14. Performance and reliability requirements

Initial targets are engineering goals to be validated and revised with evidence:

- Runtime idle memory: no more than 64 MB on the reference Linux build.
- Runtime compressed binary/package: target below 25 MB excluding adapters and certificates.
- Local discovery: 95% of healthy peers visible within 2 seconds on a stable LAN.
- Command acknowledgement: p95 below 50 ms on a stable local Ethernet/Wi-Fi test network, excluding robot execution.
- Emergency-state propagation: p99 below 100 ms on the reference LAN profile; still non-certified and advisory.
- Recovery: active-state reconciliation within 3 seconds after a short reconnect on the reference network.
- Availability: runtime survives malformed peer input without process termination.
- Scale for v1: 100 simultaneously visible machines and 1,000 telemetry updates per second per runtime under the published benchmark profile.
- Offline operation: all core functions work with internet disconnected.

Performance reports must identify hardware, OS, transport, topology, payload size, security mode, and clock method.

## 15. Compatibility and extension policy

- Semantic versioning applies to the specification and SDKs.
- Major protocol versions may change wire or behavioral compatibility.
- Minor versions may add optional fields, messages, and features.
- Patch versions clarify text or fix compatible implementation defects.
- A capability version is independent of the core protocol version.
- Unknown optional fields and features are ignored or forwarded according to profile.
- Extensions use reverse-domain namespaces.
- Experimental features are explicitly marked and cannot be required for core conformance.
- Deprecation requires a replacement path and at least one stable release cycle.

## 16. Open-source product requirements

The public repository will contain:

- normative protocol specification;
- `.proto` schemas and generated API documentation;
- runtime source;
- reference SDKs;
- adapters and examples;
- simulator and reproducible scenarios;
- conformance suite and test vectors;
- security policy and threat model;
- contribution and governance documents;
- release artifacts for supported targets; and
- compatibility matrix.

The project should use a permissive license to encourage manufacturer adoption while preserving protocol naming and conformance integrity through a separate trademark/conformance policy. Apache-2.0 and dual Apache-2.0/MIT are candidates to decide in Phase 0.

Public design proposals will be tracked as UMP Enhancement Proposals (UEPs). Normative behavior changes require review, compatibility analysis, tests, and a reference implementation.

## 17. Reference implementation scope

### 17.1 `umpd`

The reference runtime is planned in Rust for memory safety, predictable deployment, cross-compilation, and single-binary distribution. It includes:

- configuration and identity enrollment;
- peer discovery and secure sessions;
- capability registry;
- task, lease, reservation, and handoff state machines;
- local adapter API;
- durable event journal;
- policy enforcement hooks;
- health and metrics endpoint; and
- command-line inspection tools.

### 17.2 SDKs

- Rust core API for runtime and adapter authors.
- Python SDK for research, simulation, and rapid adapter development.
- C++ SDK for ROS 2 and industrial integrations.
- TypeScript SDK for inspector and management applications; not a required robot runtime.

### 17.3 Initial adapters

- Mock adapter for deterministic simulation.
- ROS 2 adapter.
- MQTT bridge.
- Generic HTTP/gRPC vendor adapter example.
- One real vendor or open robot adapter selected with a launch partner.

## 18. Simulation and validation strategy

Simulation is used to validate protocol semantics before involving expensive or hazardous hardware. It has four levels.

### Level 1: deterministic protocol simulation

Multiple virtual machines run in one process with a simulated clock and network. Scenarios verify state machines exactly and run in continuous integration.

Required scenarios:

- discovery and disappearance;
- version negotiation success and failure;
- capability matching and parameter rejection;
- task success, failure, cancellation, deadline expiry, and duplicate delivery;
- lease grant, renewal, conflict, revocation, and expiry;
- reservation contention and deadlock prevention;
- clean and interrupted handoffs;
- restart and state reconciliation; and
- malformed or unauthorized messages.

### Level 2: distributed network simulation

Separate runtime processes communicate through a controlled virtual network. Tests inject latency, jitter, loss, duplication, reordering, partitions, bandwidth limits, clock skew, and process crashes.

Required evidence includes message traces, terminal state agreement, invariant checks, and latency distributions.

### Level 3: physics-based robot simulation

ROS 2 and Gazebo are used for heterogeneous embodied scenarios. UMP coordinates high-level actions while native controllers execute movement.

Reference scenario:

1. A mobile robot discovers a robot arm and advertises payload transport.
2. The arm advertises pick, place, and handoff capabilities.
3. A coordinator or peer assigns pickup and delivery work.
4. The machines reserve a shared handoff zone.
5. They resolve coordinate frames and negotiate the handoff.
6. The mobile robot arrives and reports ready.
7. The arm transfers a simulated package.
8. Ownership commits only after both sides provide completion evidence.
9. Fault variants test disconnect, stale pose, blocked zone, dropped object, lease expiry, cancellation, and emergency state.

Additional profiles cover drone inspection and two-arm shared-resource coordination after the core scenario is stable.

### Level 4: hardware-in-the-loop and real robots

One real machine is paired with simulated peers, followed by two heterogeneous physical machines in a controlled environment. Physical tests begin with read-only discovery and telemetry, then non-motion commands, then supervised motion and handoff.

Every physical test requires a written hazard analysis, local emergency stop, bounded test area, operator authority, and adapter-specific safe-state behavior.

## 19. Simulation invariants

The test harness must continuously check:

- no terminal task changes state;
- no task executes twice for one idempotency key;
- no conflicting exclusive leases coexist;
- no handoff has two authoritative owners or zero owners outside a declared transfer interval;
- no command executes without current authority;
- no expired message initiates action;
- no unresolved coordinate frame reaches physical execution;
- every accepted task reaches a terminal or explicitly recoverable state;
- peer restart does not invent completed work; and
- local safety policy can veto every remote command.

## 20. Conformance model

Conformance is profile based:

- **UMP Core Observer:** identity, secure session, discovery, capabilities, and state.
- **UMP Core Participant:** observer plus tasks, commands, errors, and progress.
- **UMP Coordinator:** task issuance, authority, reservations, and recovery.
- **UMP Handoff:** participant plus the handoff state machine and ownership evidence.
- **UMP ROS 2 Adapter:** defined ROS mappings and lifecycle behavior.
- **UMP Gateway:** proxy identity, isolation, and disconnect behavior.

An implementation passes a profile only when it passes schema vectors, behavioral simulations, negative security tests, and required performance checks. Self-tested results are allowed during development; verified certification governance is a post-v1 decision.

## 21. Developer and operator experience

### Developer flow

1. Install a binary/package or add an SDK dependency.
2. Run `ump init` to create development identity and configuration.
3. Implement or configure a machine adapter.
4. Run `ump doctor` for platform, network, time, and credential checks.
5. Run a virtual peer and reference task locally.
6. Execute conformance scenarios for the selected profile.
7. Switch to production credentials and policy.

### Operator capabilities

The inspector should show peers, trust status, health, safety state, capabilities, active tasks, authority leases, resources, handoffs, protocol versions, and event history. Dangerous actions require explicit authentication and confirmation; read-only access is separately grantable.

## 22. Success metrics

### Adoption metrics

- Time for an experienced robotics developer to run the two-peer demo: under 15 minutes.
- Time to implement a basic adapter using an SDK: under one working day.
- At least three different machine classes demonstrated before v1.
- At least two independent, non-reference implementations pass core conformance before v1.0.

### Quality metrics

- 100% of normative state transitions covered by deterministic tests.
- Zero unresolved critical security findings at stable release.
- All release scenarios pass under the published fault matrix.
- Backward compatibility demonstrated across the supported minor-version window.
- Reproducible benchmark and simulation reports published for each release candidate.

### Product metrics

- A heterogeneous transport-to-arm handoff completes without vendor-specific logic in the task coordinator.
- Replacing one conforming simulated machine with another does not require peer changes.
- A locked-down machine can participate through a documented gateway while retaining explicit proxy identity.

## 23. MVP definition

The MVP is complete when two virtual machines and one ROS 2/Gazebo scenario can:

- securely discover and authenticate;
- negotiate version and features;
- advertise and query capabilities;
- exchange state and health;
- issue, accept, track, cancel, and complete a task;
- grant and expire command authority;
- reserve one shared resource;
- complete and abort a two-party handoff;
- recover deterministically from a disconnect; and
- pass the core conformance suite under the initial fault matrix.

The MVP does not require a production vendor robot, certified safety, cloud service, graphical fleet manager, or every planned transport.

## 24. Release criteria for v1.0

- Normative specification reviewed and frozen for v1.
- Threat model and independent security review completed.
- Runtime supports Linux `amd64` and `arm64` with signed artifacts.
- Python, Rust, and C++ integration paths documented.
- Native, ROS 2, and vendor-bridge examples each demonstrated.
- Core Participant and Handoff conformance suites are public and passing.
- Physics simulation passes nominal and fault scenarios reproducibly.
- At least two heterogeneous physical machines complete a supervised shared task.
- Upgrade, rollback, credential rotation, and recovery procedures are tested.
- Governance, contribution, compatibility, and vulnerability-disclosure policies are published.

## 25. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Scope becomes a universal robotics ontology | Delivery stalls | Keep core primitives small; move domain semantics into versioned profiles |
| Vendors expose incompatible or limited APIs | Some robots cannot fully participate | Capability honesty, adapter profiles, gateway fallback, launch partners |
| Network protocol is mistaken for safety certification | Physical harm or false assurance | Explicit safety boundary, adapter safety contracts, independent review |
| Coordinate-frame ambiguity causes unsafe commands | Incorrect movement | Mandatory frame, time, units, uncertainty, and pre-execution validation |
| Distributed handoff reaches uncertain state | Payload loss or duplicate ownership | Two-party commit, evidence, reconciliation, no blind retry |
| Excessive transport choices fragment behavior | Interoperability fails | One mandatory v1 transport profile; optional standardized bindings |
| Security setup is too difficult | Users disable it | Automated local enrollment, good diagnostics, secure development defaults |
| Runtime is too heavy for onboard computers | Low adoption | Measured budgets, modular features, ARM benchmarks, gateway option |
| Specification follows only one implementation | Vendor distrust | Public UEP process, test vectors, independent implementations |
| Simulation differs from real machines | Late integration failures | Hardware-in-the-loop begins before protocol freeze |

## 26. Open decisions

These decisions are intentionally scheduled early in the roadmap:

- project license and trademark/conformance policy;
- mandatory v1 transport after prototype benchmarks;
- identity credential format and enrollment protocol;
- local adapter API boundary and process isolation;
- durable event journal and recovery guarantees;
- canonical time synchronization and clock-quality representation;
- first standard capability profiles;
- first vendor/open-hardware launch partner; and
- supported ROS 2/Gazebo release matrix.

## 27. Definition of done for any protocol feature

A protocol feature is not done when only code exists. It is done when:

- normative behavior and state transitions are documented;
- schemas and compatibility rules are defined;
- reference runtime and at least one SDK expose it;
- positive, negative, restart, and fault tests exist;
- observability fields make the behavior diagnosable;
- security and safety implications are reviewed;
- deterministic simulation demonstrates it;
- conformance expectations are published; and
- an example shows how an adapter uses it.

