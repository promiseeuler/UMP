# UMP Core Protocol v0.1

**Status:** Prototype normative draft  
**Wire package:** `ump.v1`  
**Protocol version:** `1.2` (compatible negotiation floor: `1.0`)

The key words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED, MAY, and OPTIONAL are to be interpreted as requirement levels. This draft is normative for the current reference implementation but may change before UMP 1.0.

## 1. Scope

This document defines the common envelope, stable machine and session identity, version negotiation, presence, and heartbeat behavior implemented by the first UMP protocol kernel. Task, authority, resource, handoff, capability, and safety-state messages will be added in subsequent normative documents.

UMP messages coordinate high-level machine behavior. They MUST NOT bypass the represented machine's local controller policy or certified safety system.

## 2. Data model

The source of truth for wire fields and field numbers is `schemas/ump/v1/core.proto`. This document defines the meaning and required behavior of those fields.

All timestamps ending in `_ms` are unsigned milliseconds. Protocol time is not assumed to be UTC unless a field explicitly says so. Expiry comparisons in the current local profile use a monotonic clock within the receiving runtime.

## 3. Common envelope

Every message MUST use `Envelope`.

| Field | Requirement |
|---|---|
| `protocol_major` | MUST identify the sender's active major protocol version. |
| `protocol_minor` | MUST identify the sender's active minor protocol version. |
| `message_id` | MUST be non-empty and unique within the source session. |
| `source_machine_id` | MUST be the stable identity of the represented machine. |
| `source_session_id` | MUST identify the current runtime boot/session and change after loss of durable session state. |
| `sent_at_ms` | MUST contain the sender's protocol-clock value when the message was created. |
| `expires_at_ms` | When present, a receiver MUST reject the message at or after this value. |
| `body` | MUST contain exactly one known message body for operational processing. |

`source_machine_id` MUST NOT be derived solely from a mutable IP address. Gateways MUST eventually use the proxy identity fields defined by the Gateway profile; until those fields exist they MUST NOT claim core conformance for a represented physical machine.

A receiver MUST reject an envelope with an empty message, machine, or session identifier. A receiver MUST reject an expired envelope before changing peer or machine state.

## 4. Version negotiation

### 4.1 Hello

A peer begins negotiation with `Hello`.

- `supported_major_versions` MUST contain at least one value.
- `minimum_minor_version` MUST be less than or equal to `maximum_minor_version`.
- `presence_ttl_ms` MUST be greater than zero.
- A sender MUST include only versions for which it can obey normative behavior.

The receiver selects the highest mutually supported major version and the highest minor version supported by both peers in that major. The current implementation supports `1.0` through `1.2`. A participant configured with a lower maximum negotiates that common minor; peers MUST restrict behavior to the selected minor and mutually advertised features. A gateway proxy sets a `1.2` minimum because explicit proxy association is mandatory for that deployment form.

If no common major or minor version exists, the receiver MUST send `NegotiationRejected` and MUST NOT add the source as a negotiated peer. Rejection codes are stable machine-readable identifiers; human detail is diagnostic and MUST NOT be parsed for behavior.

### 4.2 Welcome

`Welcome` confirms the selected major and minor version and communicates the sender's presence TTL. Receiving a valid welcome establishes or refreshes the source peer for the selected session.

A future implementation supporting more than one version MUST reject a welcome that was not offered or is outside its supported range. This validation is required before multi-version support is enabled.

### 4.3 Rejection

The initial stable rejection codes are:

- `ump.negotiation.no_common_major`
- `ump.negotiation.no_common_minor`

Receiving a rejection does not establish presence and MUST NOT authorize any operation.

## 5. Presence

Presence is a receiver-local, expiring observation. It is not proof of physical health, safety, or task readiness.

A peer record contains stable machine identity, source session identity, negotiated version, last-seen time, expiry time, and status. The initial statuses are `Present` and `Expired`.

On a valid hello or welcome, the receiver MUST set:

```text
last_seen_ms = receive_time_ms
expires_at_ms = receive_time_ms + advertised_presence_ttl_ms
status = Present
```

At `now_ms >= expires_at_ms`, the receiver MUST transition a present peer to `Expired` exactly once for that presence interval. Expiry MUST NOT be interpreted as emergency stop, task failure, or physical disappearance; those behaviors belong to explicit policy and later protocol profiles.

A new source session for the same stable machine identity supersedes the old session only after successful authentication and negotiation. Session reconciliation behavior will be normative before durable tasks are implemented.

## 6. Heartbeat

A heartbeat contains a source-session sequence and a positive presence TTL.

- The sequence MUST increase within a source session.
- A receiver MUST ignore a heartbeat from a peer that has not successfully negotiated.
- A valid heartbeat from a negotiated peer refreshes only the receiver's view of the sender.
- Heartbeats are directional. A heartbeat in one direction MUST NOT refresh reciprocal presence.
- A zero TTL MUST be rejected and MUST NOT refresh presence.

Heartbeat sequence MUST strictly increase in the negotiated source session. Duplicate, stale, zero-TTL, and wrong-session heartbeats MUST NOT refresh presence. S0 validates directional refresh and expiry; Phase 1 runtime tests validate replay rejection.

## 7. State-machine invariants

Implementations MUST preserve these invariants:

1. An invalid or expired message cannot create or refresh a peer.
2. A rejected negotiation cannot create a peer.
3. Presence is scoped to stable machine identity plus source session.
4. Time never moves backward within one runtime's monotonic clock domain.
5. Expiry occurs at the advertised boundary, not after an arbitrary grace interval.
6. One-way traffic refreshes only the receiving side's view.

## 8. Encoding and compatibility

Protocol Buffers field numbers MUST never be reused. A field's meaning MUST NOT change incompatibly within a major protocol version. Unknown additive fields MUST be preserved where a binding supports forwarding and otherwise ignored. Unknown message bodies MUST NOT cause physical or privileged action.

Canonical test vectors will be added before Phase 1 exit. Generated debug output is not a canonical wire encoding.

## 9. Security profile

In-memory simulation is a development profile and provides no network authentication. Any network transport carrying operational UMP messages MUST provide confidentiality, integrity, peer authentication, downgrade protection, and bounded message framing.

No unauthenticated network message may create authority, invoke a capability, or represent safety state. Development credentials MUST be visibly identified and MUST NOT be accepted by a production trust policy.

## 10. Current conformance boundary

The current prototype demonstrates:

- valid mutual `1.2` negotiation and compatible lower-minor negotiation;
- deterministic peer establishment and expiry;
- directional heartbeat refresh;
- malformed hello rejection;
- incompatible-major rejection; and
- expired-envelope rejection.

It does not yet claim UMP Core Observer conformance because authenticated network sessions, capability advertisement, structured state, golden vectors, and negative security tests remain incomplete.
