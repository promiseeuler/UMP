# UMP Hardware Design Partner Program

This process is for robot owners, manufacturers, integrators, and research teams
helping validate UMP against real robot platforms. UMP is currently an alpha
reference implementation. Participation begins with architecture review and
read-only awareness; it does not begin with remote physical work.

UMP exchanges semantic state and authorized high-level work requests. It does
not issue joint commands, trajectories, motor values, controller gains, or
emergency-stop actions. Native robot software and physical safety systems remain
authoritative throughout the program.

## 1. Partner eligibility

A suitable partner has:

- authorized access to a robot SDK, ROS 2 interface, or manufacturer simulator;
- a Linux onboard or companion computer capable of running Python 3.11 or newer;
- access to bounded semantic state such as activity, intent, progress,
  availability, and safety status;
- a professional simulator or controlled physical workspace;
- an operator responsible for the native robot and emergency-stop procedure;
- permission to install a read-only integration service; and
- an engineer able to review and maintain the manufacturer adapter.

Physical testing additionally requires an approved test plan, restricted work
area, functioning physical emergency stops, rollback procedure, and named safety
owner. A partner who cannot meet those conditions can still participate in
protocol review and simulation testing.

## 2. Intake information

Collect the following before granting repository or support access:

| Area | Required information |
| --- | --- |
| Organization | Organization, technical contact, and test location |
| Robot | Manufacturer, model, firmware, robot class, and quantity |
| Compute | Operating system, architecture, Python version, and ROS distribution |
| Native API | SDK/action/service used for semantic state and high-level work |
| Simulator | Gazebo, Isaac Sim, manufacturer simulator, or none |
| State | Available activity, intent, progress, safety, pose, and availability fields |
| Capability | Candidate high-level capability and native acceptance/cancellation API |
| Network | Addressing, firewall, certificate, offline, and latency constraints |
| Safety | Safety owner, emergency stop, work-area limits, and communication-loss policy |
| Evidence | Information that may be retained, anonymized, or published |

Do not request or accept private keys, customer telemetry, raw sensor data,
proprietary controller source, or confidential deployment credentials in an
issue or test report.

## 3. Program stages

Partners progress one stage at a time. Completion of one stage does not
automatically authorize the next.

### Stage A: protocol and architecture review

Review:

- `PRD.md`, `PROTOCOL.md`, and `ARCHITECTURE.md`;
- `MANUFACTURER_ADAPTER.md` and the `RobotAdapter` contract;
- `INTEROPERABILITY.md` and the standard capability vocabulary;
- assignment authority, communication-loss behavior, and the safety boundary;
  and
- the partner's proposed mapping from native APIs to UMP semantic fields.

Record ambiguities, missing vocabulary, privacy constraints, and any place where
UMP would require actuator-level access. Actuator-level access is a blocking
design error and must not be implemented.

**Exit criteria:** the partner can describe the adapter boundary, disclosed
state, trust model, and one possible read-only integration without changing
native safety behavior.

### Stage B: reference simulation

Install the reviewed UMP checkout:

```sh
git clone https://github.com/promiseeuler/UMP.git
cd UMP
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install .
```

Run the retained simulation workflow:

```sh
ump-simulate run --project-root . --output partner-simulation.json
ump-simulate validate partner-simulation.json
ump-visual-sim --port 8766
```

**Exit criteria:** every simulation check passes and the partner understands why
the result is not physical-robot or production qualification.

### Stage C: local mutual-TLS awareness lab

Generate and verify a three-node read-only network:

```sh
ump-deployment quickstart --output ump-local-lab
ump-deployment verify-local ump-local-lab
```

For partner-selected identities, use:

```sh
ump-deployment wizard --output partner-local-lab
```

Run every generated `preflight.sh`, then start each `run.sh` in a separate
terminal. Inspect one retained protocol view:

```sh
ump-inspector \
  --database ump-local-lab/nodes/robot-humanoid-1/state/inspector.sqlite3 \
  --port 8765
```

Generated certificates are development-only and marked
`production_eligible: false`.

**Exit criteria:** every node observes every peer manifest and state, no delivery
errors remain, peer fingerprints are pinned, and no adapter advertises an
assignment-capable capability.

### Stage D: partner simulator read-only adapter

Begin with `examples/read_only_adapter.py`. Replace only the manifest and state
sources with bounded native simulator APIs. Keep the capability tuple empty and
keep assignment acceptance disabled.

```sh
ump-adapter-conformance inspect \
  --adapter partner_package.adapter:create_adapter \
  --adapter-config partner-adapter.json \
  --output adapter-conformance.json

ump-adapter-conformance verify adapter-conformance.json \
  --implementation partner_package/adapter.py
```

Test state freshness, restart, network loss, certificate rotation, malformed
native values, safety-state transitions, and native API failure. Do not send raw
sensor content through semantic state.

**Exit criteria:** read-only conformance passes; identity, units, frames, and
freshness are correct; loss/restoration behavior is recorded; and the adapter
cannot invoke native work.

### Stage E: physical robot read-only observation

Replace development credentials with owner-issued certificates. Run full node
preflight before binding a listener. Connect only to approved semantic state
APIs. Operate for an owner-defined soak period while monitoring the protocol
inspector and network diagnostics.

```sh
ump-network-diagnostics --network /etc/ump/network.json
```

Stop immediately for identity mismatch, stale or misleading state, unexplained
safety transitions, dead letters, retry backlog, native instability, or any
effect on the robot controller.

**Exit criteria:** the robot remains safe and locally operable with UMP running,
stopped, disconnected, and restarted; retained state accurately describes the
native robot; and no physical action can be requested through UMP.

### Stage F: one capability in professional simulation

Select one bounded high-level capability whose native semantics match a
versioned UMP contract. Add native operating-mode, workspace, resource, safety,
acceptance, terminal-outcome, uncertainty, and cancellation checks. Exercise it
only in a professional simulator or approved bounded test fixture.

Execution conformance requires the explicit native-execution gate described in
`CONFORMANCE.md`. Never interpret adapter success as evidence of physical
success unless the native controller provides the corresponding terminal result.

**Exit criteria:** input and output schemas pass, unauthorized and malformed work
is rejected before native execution, uncertainty does not trigger duplicate
physical work, cancellation retains native authority, and simulator evidence is
retained against the exact adapter build.

### Stage G: supervised physical pilot

Follow `HARDWARE_PILOT.md`. Begin with one capability, one target robot, one
owner-approved coordinator, short authority leases, a bounded workspace, and a
named operator. UMP cancellation remains a high-level request and never replaces
the physical emergency stop.

The production pilot target is at least two physical robots and one simulated
participant. Add robots and capabilities individually rather than enabling a
fleet at once.

**Exit criteria:** the pilot evidence bundle validates, all blocking findings are
resolved or explicitly accepted by the responsible owner, leases are revoked at
the end of the session, and the partner approves the retained result.

## 4. Evidence package

Retain one directory per partner test revision containing:

- UMP full commit revision and package version;
- robot manufacturer, model, firmware, and non-secret configuration identifiers;
- adapter source revision and conformance report;
- simulator/runtime versions and topology;
- network and credential validation summaries without private material;
- protocol inspector export or bounded event summary;
- test cases, expected results, actual results, and timestamps;
- communication-loss, restart, cancellation, and recovery results;
- operator and reviewer roles;
- findings and dispositions; and
- an explicit `simulation`, `read_only_physical`, or `supervised_physical` label.

Never mark simulated evidence as hardware evidence or partner-authored code as an
independent manufacturer result unless the relevant party confirms ownership and
scope.

## 5. Stop conditions

Stop testing and return to the previous stage when:

- UMP affects actuator-level control or a native safety mechanism;
- robot identity or certificate ownership is uncertain;
- semantic state is stale, false, ambiguous, or exposes unauthorized data;
- an adapter reports success without reliable native terminal evidence;
- communication loss causes an undefined native response;
- a planner requests work outside advertised capability or authority scope;
- physical emergency stops or supervision are unavailable;
- protocol delivery or storage health is degraded; or
- the partner cannot determine who owns a safety or security decision.

Security vulnerabilities must follow `SECURITY.md`, not a public issue. Physical
safety concerns remain blocking until reviewed by the robot owner and responsible
safety engineer.

## 6. Completion and publication

A partner test is complete only when its stage exit criteria and evidence package
are reviewed. Publishing a result requires partner approval and removal of
credentials, confidential SDK details, customer information, network addresses,
and raw telemetry.

Design-partner testing informs the protocol and adapter contracts. It does not by
itself make UMP production-ready. Production claims still require every gate in
`compliance/qualification.json`, including independent security, safety, and
interoperability review and a retained tagged release artifact.
