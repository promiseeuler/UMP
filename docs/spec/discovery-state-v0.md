# UMP Discovery, Capability, and State v0.1

**Status:** Prototype normative draft  
**Wire package:** `ump.v1`  
**Depends on:** UMP Core Protocol v0.1

This document defines the endpoint, feature, capability, machine descriptor, operational state, health, safety, telemetry, and structured error semantics used by the Phase 1 protocol kernel.

## 1. Authentication and disclosure

An envelope's `source_machine_id` is a claim. A network receiver MUST bind that claim to the identity authenticated by its transport before processing the message. The reference QUIC profile binds a peer certificate fingerprint to an enrolled stable machine ID. A mismatch MUST fail before presence or metadata changes.

Discovery does not imply unrestricted disclosure. A peer MUST pass site policy before receiving protected capability, state, health, spatial, or telemetry information. The runtime MUST support a peer that may negotiate presence but cannot read protected metadata.

Development credentials and in-memory simulation identities are explicitly non-production. They MUST NOT be accepted by a production trust policy.

## 2. Endpoints and features

An `Endpoint` identifies one reachable protocol binding.

- `uri` MUST be non-empty and include sufficient scheme and authority information for its transport.
- `transport` MUST identify a negotiated transport kind.
- Lower `priority` values are preferred. Equal priorities have no implied order.
- Advertising an endpoint does not grant authorization to connect or invoke capabilities.
- A receiver MUST ignore an unsupported optional endpoint while retaining supported alternatives.

The mandatory prototype endpoint is QUIC with TLS 1.3 and ALPN `ump/1`. TLS/TCP is an optional development and bridge profile. In-memory endpoints are simulation-only.

A `Feature` has a namespaced name, independent version, and required flag. A peer that does not support a required feature MUST treat the affected descriptor as incompatible. Unknown optional features MAY be ignored. Feature negotiation is distinct from protocol version negotiation.

## 3. Capabilities

A `Capability` describes something a machine can observe or do. It does not grant permission to invoke it.

- `type`, `version`, and `revision` MUST be present; revision MUST be greater than zero.
- `type` MUST use a namespaced identifier.
- Input and output schema URIs identify parameter and result contracts where applicable.
- `availability` describes current usability and MUST NOT change the capability's static meaning.
- Observable, invocable, reservable, interruptible, and handoff-capable behavior is explicit.
- Every numeric constraint MUST identify its unit. Receivers MUST NOT infer units from names.
- Capability revision MUST increase whenever advertised capability content changes.

A receiver MUST validate invocation parameters against the exact advertised capability version before task acceptance. Task invocation is outside this Phase 1 document.

## 4. Machine descriptor and advertisement

`MachineDescriptor` is the revisioned, protected description of one machine session's integration surface.

- `machine_class` and a revision greater than zero are REQUIRED.
- Endpoint URIs MUST be non-empty and every capability MUST satisfy Section 3.
- Descriptor revision MUST increase monotonically within one source session.
- An equal or lower revision is stale and MUST NOT replace stored metadata.
- A descriptor is accepted only for a successfully negotiated, currently known peer.

`Advertisement` wraps one descriptor. Absence of its descriptor is malformed. An advertisement cannot establish presence or authority.

## 5. Operational and safety state

Operational and safety state are independent dimensions. Initial operational values are starting, idle, busy, paused, degraded, stopping, and faulted. Initial safety values are normal, protective stop, emergency stop, recovery required, and unknown.

- `UNSPECIFIED` means omitted and MUST NOT be interpreted as normal.
- Safety `UNKNOWN` is explicit and MUST fail closed according to adapter policy.
- Operational idle does not imply safety normal.
- A network safety message remains advisory unless separately incorporated into a certified safety design.
- Remote emergency reset is prohibited by default.

## 6. Health and telemetry

A health condition includes component, severity, stable code, human message, first-seen time, and optional remediation. Behavior MUST depend on stable code and severity, not parsed human text. First-seen time remains stable while one condition is continuously active.

Telemetry is bounded diagnostic state, not an unlimited sensor stream.

- Every sample identifies metric, value, unit, source time, and source sequence where available.
- A receiver MUST be able to identify stale or out-of-order samples.
- The reference runtime accepts at most 256 samples in one state update.
- A larger update is rejected before storing the batch.
- High-rate media and raw sensor streams MUST use a negotiated data plane.
- Telemetry load MUST NOT delay control-plane processing.

## 7. State update

`StateUpdate` is a revisioned protected snapshot containing operational state, safety state, health, telemetry, source time, and revision.

- Revision MUST be greater than zero and increase monotonically within one source session.
- Equal or lower revisions are stale and MUST NOT replace stored state.
- Source time describes observation time, not merely network send time.
- A state update cannot establish presence or authority.
- An unauthorized peer receives neither state nor a policy-revealing partial response.

## 8. Structured errors

`ErrorReport` includes stable code, category, diagnostic detail, retryability, related message ID, occurrence time, and remediation. Initial categories are protocol, authentication, authorization, validation, resource, and internal.

- Behavior MUST use code, category, and retryability; detail and remediation are human diagnostics.
- Authentication errors SHOULD avoid revealing whether a claimed machine exists.
- Authorization errors MUST NOT disclose protected capability details.
- Retryable does not authorize blind physical retry.
- An error received from a peer cannot establish presence.

## 9. Replay and ordering

- Message IDs MUST be unique within a source session.
- A duplicate message ID MUST be rejected and MUST NOT refresh presence.
- Heartbeat sequence MUST strictly increase within the negotiated source session.
- Heartbeats from a different or unknown session MUST NOT refresh presence.
- Descriptor and state revisions MUST strictly increase.
- The reference runtime remembers at least 4,096 recent `(session_id, message_id)` pairs.

Transport replay resistance does not replace application duplicate and revision checks.

## 10. Phase 1 conformance evidence

The Phase 1 reference implementation demonstrates 20 virtual machines across four classes; protected descriptors; different feature and capability revisions; delayed startup; duplicate, incompatible-version, expired-credential, stale-state, telemetry-flood, and disappearing-peer faults; zero false presence transitions; bounded runtime memory; and identical golden vector decoding in Rust and Python.

## 11. Gateway deployment association

A directly hosted machine MUST advertise `DEPLOYMENT_MODE_DIRECT` and omit
`proxy`. A machine represented through an external gateway MUST advertise
`DEPLOYMENT_MODE_GATEWAY_PROXY` and include exactly one `ProxyAssociation`.
The association MUST contain a stable `ump:gateway:` gateway identifier, the
represented `ump:machine:` identifier, a non-empty controller-interface profile,
and whether the interface is read-only. The represented machine identifier MUST
equal the authenticated sender machine identity.

On a session negotiated at protocol 1.2 or newer, peers MUST treat an unspecified
deployment mode, a proxy on a direct deployment, or an incomplete proxy association
as malformed metadata. Legacy 1.0/1.1 sessions MAY omit both deployment fields and
are interpreted as direct for compatibility. A proxy MUST require protocol 1.2 and
MUST NOT downgrade to a legacy session. Gateway status does not
transfer safety authority away from the represented machine's local controller.
