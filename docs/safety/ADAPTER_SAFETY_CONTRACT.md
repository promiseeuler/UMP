# UMP Adapter Safety Contract

Every adapter that exposes a physical machine MUST publish and test this contract. A blank or incomplete contract means the adapter is development-only and cannot claim a physical-control conformance profile.

## 1. Adapter identity

- Adapter name and version:
- Machine/controller models:
- Native API or field interface:
- Deployment path: native, ROS 2, vendor bridge, or gateway:
- Maintainer and support status:

## 2. Physical-effect capability inventory

For every capability, document:

| Field | Required content |
|---|---|
| Capability identifier and version | Stable namespaced value |
| Physical effect | Motion, force, energy, payload, tool, or environmental effect |
| Native command mapping | Exact controller API/action used |
| Input bounds | Units, coordinate frame, valid range, precision, and maximum age |
| Required authority | Scope, issuer classes, lease, and local mode |
| Native safety checks | Limits and interlocks applied after UMP validation |
| Interruptibility | Immediate, deferred, or impossible, with maximum stop latency |
| Completion evidence | Native state proving success |
| Retry behavior | Idempotent, reconciliation required, or never automatic |

## 3. Mandatory fail-safe behavior

The adapter MUST define observable behavior for:

- invalid, missing, stale, or unresolved coordinate frames;
- command or lease expiry before and during execution;
- runtime-to-adapter disconnect;
- adapter-to-controller disconnect;
- UMP peer or coordinator disappearance;
- local protective stop and emergency stop;
- health degradation during execution;
- cancellation in each capability stage;
- adapter restart with uncertain controller state; and
- contradictory completion evidence.

The adapter MUST reject out-of-range inputs before invoking the native API. The native controller MUST remain able to reject any request. A local safety veto MUST take precedence over every remote command.

## 4. Authority precedence

Adapters MUST document their complete precedence order. The default recommendation is:

1. Hardware emergency stop and certified safety controller.
2. Local operator and controller mode.
3. Robot-native safety and autonomy policy.
4. Active UMP authority lease and site policy.
5. Remote task or command priority.

UMP MUST NOT remotely reset an emergency state by default.

## 5. Timing and resource bounds

- Maximum accepted command age:
- Maximum spatial-state age:
- Lease-expiry reaction time:
- Runtime disconnect detection time:
- Maximum command queue depth:
- Maximum concurrent actions:
- CPU and memory limits:
- Safe behavior if limits are exceeded:

## 6. Verification evidence

An adapter release MUST include automated or supervised evidence for:

- valid and invalid input boundaries;
- unauthorized command rejection;
- stale frame and stale command rejection;
- cancellation at every declared interruptible stage;
- lease expiry and revocation;
- runtime, adapter, and controller restarts;
- network loss and recovery;
- protective-stop propagation;
- safe behavior under resource exhaustion; and
- no blind retry after an unknown physical outcome.

Physical testing additionally requires a bounded area, local emergency stop, trained operator, written hazard analysis, and rollback procedure.

## 7. Vendor bridge and gateway additions

A vendor bridge or external gateway MUST additionally document and test:

- the immutable capability-to-controller operation allowlist;
- controller authentication, server verification, credential storage, and rotation;
- redirect behavior and protection against task-supplied destinations;
- request, response, queue, timeout, and retry bounds;
- native deduplication and reconciliation using the UMP task identifier;
- controller disconnect behavior before, during, and after physical effect;
- how read-only commissioning prevents task claims and controller writes; and
- the approved PLC function block, vendor SDK method, or controller job behind each capability.

The bridge MUST NOT bypass certified safety logic or directly implement a cyclic fieldbus motion loop. A gateway losing contact with UMP MUST leave the robot under local controller authority; losing contact with the controller during a physical effect MUST produce an uncertain outcome requiring reconciliation unless independent evidence proves a terminal result.
