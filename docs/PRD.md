# Universal Machine Protocol Product Requirements Document

**Status:** Draft v0.1

**Product stage:** Foundation

**Primary deliverable:** Open protocol specification, schemas, reference runtime, and adapters

## 1. Product definition

Universal Machine Protocol (UMP) is a lightweight, manufacturer-neutral
communication and coordination layer for robots and autonomous machines on a
shared network.

UMP allows each connected machine to publish a precise semantic description of:

- its identity and type;
- the capabilities it exposes;
- its current operational and safety state;
- what it is doing now;
- what it intends to do next;
- its progress, blockers, and relevant location; and
- the tasks and resources involved in its work.

Other machines consume this information to maintain shared awareness. An
optional collaboration layer accepts a shared goal and uses a replaceable
reasoning provider to propose microtasks for capable participants.

UMP is not a robot controller. It does not generate joint trajectories, drive
motors, replace autonomy software, or bypass local safety policy. Native robot
controllers remain the only components that execute physical behavior.

## 2. Problem

Robots from different manufacturers commonly use incompatible message formats,
capability names, fleet systems, and control stacks. An owner who combines a
humanoid, quadruped, and mobile arm cannot easily let each machine understand
what the others are doing. Collaboration then requires custom pairwise
integrations and a coordinator tightly coupled to every vendor.

UMP creates one open semantic boundary. A vendor or individual writes one UMP
adapter for a robot. Every conforming peer can then understand that robot's
public capabilities and state without learning its private control API.

## 3. Vision scenario

An owner connects three robots from different manufacturers to an authorized
local network:

1. A humanoid advertises carrying and human-space manipulation capabilities.
2. A quadruped advertises terrain traversal and route-inspection capabilities.
3. A mobile arm advertises grasping, placing, and mobile-manipulation capabilities.
4. Each robot continuously publishes a compact semantic state description.
5. The owner submits: "Move the sealed package from intake to storage."
6. A chosen reasoning provider receives only authorized UMP descriptions and
   proposes a dependency graph of microtasks.
7. UMP validates that every assignment uses an advertised capability, has valid
   dependencies, and respects current availability.
8. Approved assignments are delivered to robot adapters as high-level requests.
9. Native controllers decide how to execute or reject each request.
10. All peers observe progress, completion, failure, or replanning through UMP.

## 4. Goals

- Enable shared awareness across robots from unrelated manufacturers.
- Provide compact, detailed, human-readable and machine-readable descriptions.
- Keep protocol semantics independent of transport, robot middleware, and vendor.
- Work on local networks without a required cloud service.
- Support individual owners, research teams, integrators, and manufacturers.
- Permit any reasoning model, deterministic planner, or human-authored plan.
- Use the same adapter boundary in simulation and on physical robots.
- Integrate cleanly with ROS 2 and professional simulators.
- Make state, plans, assignments, and outcomes observable and auditable.
- Preserve the authority of each robot's native controller and safety system.

## 5. Non-goals

UMP v0 will not:

- control motors, joints, navigation stacks, grippers, or low-level hardware;
- provide universal perception, SLAM, motion planning, or task reasoning;
- guarantee that a reasoning model's plan is correct or safe;
- replace ROS 2, a vendor SDK, a PLC, or a fleet manager;
- stream raw camera, lidar, or point-cloud data through semantic state messages;
- require one network transport, cloud provider, or AI model;
- infer capabilities that a robot has not explicitly advertised; or
- claim functional-safety certification.

## 6. Users

- **Robot owners** connect mixed personal or commercial robots.
- **Manufacturers** expose interoperable descriptions through native adapters.
- **Integrators** coordinate heterogeneous machines without pairwise bridges.
- **Researchers** test multi-robot behavior in simulation and hardware.
- **Planner developers** consume normalized robot context and propose plans.
- **Operators** inspect current activity, intent, progress, and failures.

## 7. Product principles

1. **Describe, do not drive.** UMP communicates meaning and assignments; native
   robot software controls physical behavior.
2. **Capability honesty.** Robots expose only capabilities they can currently
   support and may reject every request.
3. **Shared awareness first.** Read-only state exchange is useful without
   collaboration or remote task assignment.
4. **Structured plus readable.** Protocol fields carry deterministic meaning;
   bounded summaries make the same state understandable to people and models.
5. **Planner neutrality.** Planner output is untrusted and replaceable.
6. **Local safety wins.** Local policy and emergency systems always take precedence.
7. **Transport neutrality.** Semantics remain stable across in-memory, DDS, QUIC,
   WebSocket, or other standardized bindings.
8. **Simulation and reality share contracts.** Only adapters change.

## 8. System components

- **Robot adapter:** maps native capability and state APIs to UMP and maps approved
  high-level assignments back to the native controller.
- **UMP participant:** publishes a robot manifest and state, consumes peer state,
  and exchanges collaboration messages.
- **Registry:** maintains the current view of participants, capabilities, state,
  sequence numbers, and freshness.
- **Transport binding:** carries UMP envelopes. The v0 reference uses an in-memory
  bus; production bindings are separately specified.
- **Goal coordinator:** requests plans, validates proposals, issues approved
  assignments, and observes outcomes. It contains no vendor control logic.
- **Reasoning provider:** optional plug-in that converts a goal and authorized
  world snapshot into a proposed microtask graph.
- **Inspector:** displays manifests, semantic state, plans, assignments, and events.

## 9. Core data model

### 9.1 Envelope

Every message includes protocol version, message identifier, type, source,
timestamp, sequence, optional correlation identifier, and typed payload.

### 9.2 Robot manifest

A manifest includes stable robot identity, manufacturer, model, robot class,
adapter version, and capabilities. Each capability includes a namespaced name,
plain-language description, input schema, output schema, and availability.

### 9.3 Semantic robot state

State includes mode, safety state, current activity, next intent, progress,
blockers, active assignment, optional pose reference, and a bounded summary.
The structured fields are authoritative; the summary must faithfully describe
them and must not introduce actions or claims absent from those fields.

Example summary:

> Quadruped Q1 is inspecting aisle 4 for a traversable route. It is 60% complete,
> operating normally, and intends to publish the route before transport begins.

### 9.4 Shared goal and collaboration plan

A shared goal contains an outcome description, constraints, participants, and
optional deadline. A plan contains ordered or parallel microtasks. Each microtask
identifies an assigned robot, advertised capability, inputs, dependencies,
completion criteria, and execution window.

Plan acceptance does not execute work. Each microtask must still pass authority,
availability, resource, safety, and native-controller checks.

## 10. Functional requirements

### Awareness

- `AWR-01` A participant MUST publish a manifest before operational state.
- `AWR-02` State MUST use a monotonically increasing sequence per source session.
- `AWR-03` State MUST include source time and freshness duration.
- `AWR-04` Receivers MUST reject stale or replayed state.
- `AWR-05` State MUST separate operational mode from safety state.
- `AWR-06` State summaries MUST be bounded to 1,024 UTF-8 bytes in the core profile.
- `AWR-07` A robot MAY operate in read-only mode and publish no invocable capability.

### Capabilities

- `CAP-01` Capability names MUST be namespaced and versioned.
- `CAP-02` A capability MUST include description and JSON input/output schemas.
- `CAP-03` Availability MUST distinguish available, busy, degraded, and unavailable.
- `CAP-04` An adapter MUST validate inputs before passing an assignment to native code.
- `CAP-05` A robot MUST be free to reject a supported capability at runtime.

### Collaboration

- `COL-01` UMP MUST support one goal or a list of goals.
- `COL-02` Reasoning providers MUST implement a transport-independent planner interface.
- `COL-03` Planner requests MUST contain only authorized participant context.
- `COL-04` Planner proposals MUST be treated as untrusted input.
- `COL-05` The validator MUST reject unknown participants, unavailable capabilities,
  missing dependencies, dependency cycles, invalid execution windows, and oversized data.
- `COL-06` Independent microtasks MAY execute concurrently.
- `COL-07` Dependent microtasks MUST wait for successful prerequisite outcomes.
- `COL-08` Failure MUST become visible and MUST NOT trigger blind physical retries.
- `COL-09` Replanning MUST create a new immutable plan revision.
- `COL-10` A person MAY supply a plan without using an AI reasoning model.

### Assignment authority

- `AUT-01` Remote assignments MUST be denied unless a local authority policy permits them.
- `AUT-02` Authority MUST bind authenticated issuer, target robot, exact capability, and time window.
- `AUT-03` An assignment MUST reference the lease presented for its target robot.
- `AUT-04` Lease validity MUST use receiver-local time and fail closed for declared clock uncertainty.
- `AUT-05` Leases MUST support local grant, monotonic renewal, revocation, expiry, and durable audit.
- `AUT-06` A remote protocol peer MUST NOT be able to grant itself assignment authority.
- `AUT-07` Authorization MUST occur before durable acceptance or native adapter execution.
- `AUT-08` Local safety and native adapter rejection MUST remain authoritative after lease approval.

### Control and safety boundary

- `SAF-01` UMP MUST NOT expose actuator-level commands in the core protocol.
- `SAF-02` Assignment messages MUST describe desired outcomes, not trajectories.
- `SAF-03` Native adapters MUST retain final acceptance and cancellation authority.
- `SAF-04` Safety state MUST have a priority publication path in network bindings.
- `SAF-05` Loss of UMP communication MUST invoke adapter-defined local behavior.
- `SAF-06` UMP network messages MUST NOT be presented as a certified emergency stop.

### Interoperability

- `INT-01` The canonical v0 encoding MUST be JSON conforming to the published schema.
- `INT-02` Unknown optional fields MUST be ignored.
- `INT-03` Incompatible major versions MUST fail closed for assignments.
- `INT-04` Adapters MUST use stable SI units and explicit coordinate-frame identifiers.
- `INT-05` Large sensor data MUST be referenced by metadata rather than embedded.

## 11. Quality requirements

- Core message limit: 64 KiB in the default profile.
- State update target: 1-10 Hz, configurable by robot and network.
- Local state propagation target: p95 below 100 ms on a healthy LAN.
- Reference participant idle memory target: below 32 MiB.
- A disconnected peer must become stale within its declared freshness interval.
- All protocol state transitions must be deterministic and testable without hardware.
- Logs must correlate goal, plan, microtask, assignment, and robot state identifiers.

## 12. Security and privacy

Production bindings must mutually authenticate participants, encrypt traffic,
authorize metadata and task access separately, prevent replay, and support local
operation without internet access. A deployment must be able to hide sensitive
capabilities or state from unauthorized peers. Planner providers receive a
filtered snapshot and must not receive raw sensor data by default.

## 13. Simulation and real-robot strategy

### Level 1: deterministic reference simulation

In-process adapters test manifests, state convergence, planning, validation,
dependencies, outcomes, stale peers, and malformed messages.

### Level 2: network process simulation

Separate participants run over a secure network binding under latency, loss,
duplication, reordering, disconnection, and restart.

### Level 3: professional robot simulation

The primary visual profile targets ROS 2 Jazzy with Webots. Gazebo Harmonic is
retained as the compatibility and engineering-validation profile. The
architecture must also support NVIDIA Isaac Sim adapters without changing core
schemas. The reference scenario uses a humanoid, quadruped, and mobile arm to
inspect a route, transport an object, and place it.

### Level 4: hardware in the loop and physical robots

Replace one simulated adapter with one physical robot adapter, beginning in
read-only mode. Progress to supervised high-level assignments only after adapter
safety review, bounded work areas, physical emergency stops, and operator approval.

## 14. MVP acceptance criteria

- Three heterogeneous simulated robots exchange manifests and live semantic state.
- Every robot can inspect what the other two are doing and intend to do.
- A shared goal is submitted through the public coordinator API.
- A replaceable planner proposes capability-based microtasks.
- Invalid and cyclic plans are rejected before assignment.
- Valid dependencies execute in order while independent work can run concurrently.
- State and task outcomes remain visible in a correlated event trace.
- No UMP component calls an actuator-level API.
- The same adapter interface is documented for a ROS 2/Gazebo implementation.
- The complete reference test suite runs with one command and no robot hardware.

## 15. Delivery milestones

1. **Protocol seed:** schemas, semantic state, in-memory transport, registry, tests.
2. **Collaboration seed:** planner interface, validation, assignment lifecycle, demo.
3. **Network alpha:** secure local transport, discovery, identity, replay protection.
4. **ROS 2 simulator alpha:** Webots visual demonstration, Gazebo compatibility,
   adapters, three-robot scenario, and fault injection.
5. **Conformance alpha:** golden vectors, independent adapter test harness, inspector.
6. **Hardware pilot:** one simulated plus two physical participants in supervised work.

## 16. Decisions and evidence

Still deferred until deployment evidence:

- mandatory production transport binding; and
- first commercial robot adapters.

Resolved in the reference implementation:

- mutual-TLS network profile and robot-local credential lifecycle;
- Ubuntu Noble, ROS 2 Jazzy, Webots visual, and Gazebo Harmonic compatibility matrix;
- durable SQLite journals for assignments, coordination, authority, delivery,
  replay protection, credentials, and inspection; and
- `ump.standard/v1`, the first bounded domain capability vocabulary.
