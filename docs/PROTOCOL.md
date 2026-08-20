# UMP Core Protocol v0.1

This document defines the behavior implemented by the UMP v0.1 reference core.
Normative terms MUST, SHOULD, and MAY are used in their standards sense.

## Encoding

The canonical encoding is UTF-8 JSON. Encoders MUST emit object keys in lexical
order with no insignificant whitespace. A core-profile message MUST NOT exceed
65,536 encoded bytes. Decoders MUST reject malformed UTF-8, malformed JSON,
non-object envelopes, unknown message types, and incompatible protocol versions.

Unknown optional object fields MAY be retained or ignored. Implementations MUST
not change the meaning of a known field based on an unknown field.

## Envelope

Every message contains:

- `protocol`: exactly `ump/0.1`;
- `message_id`: unique within the sender's operational history;
- `message_type`: one of the types declared in the schema;
- `source_id`: stable identity of the publishing participant;
- `session_id`: unique identity for one participant process lifetime;
- `stream`: optional sequence namespace, `operational` by default or `safety`;
- `sequence`: positive, monotonically increasing integer within the session;
- `timestamp_ms`: source time in Unix epoch milliseconds for network profiles;
- `correlation_id`: optional goal or operation correlation identifier; and
- `payload`: message-specific object.

Transport authentication is responsible for binding the authenticated peer to
`source_id`. The reference in-memory binding cannot authenticate; its registry
still enforces agreement between envelope identity and payload identity.

## Session and state rules

1. A robot MUST publish a manifest as the first message of a new session.
2. A receiver MUST reject state for a session whose manifest has not been accepted.
3. A new accepted session supersedes state from an older session of the same robot.
4. A receiver MUST ignore a sequence less than or equal to the last accepted
   sequence for that source session.
5. State becomes stale after `timestamp_ms + fresh_for_ms`.
6. Stale state MUST NOT be used to authorize a collaboration plan.

### Safety state stream

The optional `safety` stream carries state messages only. Operational and safety
streams have independent monotonically increasing sequence counters within the
same authenticated process session. A safety-stream state still requires that
the session's operational manifest was accepted first. Receivers keep separate
replay floors and prefer the newest source timestamp when streams converge.

Safety publication is an observability path, not an emergency stop. Assignments,
cancellations, goals, plans, and outcomes are forbidden on the safety stream.

## Spatial and sensor references

Optional shared-state poses use a named `frame_id`, Cartesian `position_m` in SI
meters, a normalized quaternion in `orientation_xyzw` order, and the source
observation time. Frame identifiers are semantic names; UMP does not publish or
infer frame transforms.

Large sensor content is never embedded in a core message. A state may publish
up to 32 metadata-only references. Each reference carries an absolute URI that
cannot use the `data:` scheme, media type, byte length, lowercase SHA-256 digest,
observation time, optional expiry, and optional frame. A reference grants no
access by itself; retrieval authentication and disclosure authorization remain
deployment responsibilities.

Numeric fields in advertised capability input/output schemas MUST follow
`docs/INTEROPERABILITY.md`: every number declares a supported `x-ump-unit`, and
spatial quantities name a required string frame property through
`x-ump-frame-field`. Dimensionless values explicitly use unit `1`.

## Communication loss

A deployment that depends on peer communication MUST configure a required-peer
watchdog and manufacturer-defined local loss behavior. Loss is derived from the
same declared state freshness used by collaboration, not from sender timestamps
alone. The callback receives the complete stale-peer set on an edge transition.
It may invoke reviewed native behavior but UMP does not prescribe motion.

Restoration is a separate edge and means only that required peer state is fresh
again. It does not resume assignments or physical work automatically. A callback
exception remains observable and is retried on the next watchdog evaluation.

## Plan revisions

An initial plan has `revision` 1 and no predecessor. Replanning MUST create a
new immutable `plan_id`, increment the previous revision by exactly one, and set
`supersedes_plan_id` to that exact previous plan. An active plan must be
cancelled before replanning. Prior plans, assignments, and outcomes remain
unchanged and auditable after a later revision is created.

## Assignment boundary

An assignment requests one advertised high-level capability. Before invoking a
native adapter, a participant MUST confirm the capability exists and validate
the assignment inputs against that capability's JSON Schema. Repeated delivery
of the same `assignment_id` MUST return the recorded outcome and MUST NOT invoke
the native adapter again.

An assignment ID is permanently bound to the canonical hash of its first
accepted payload. Reuse with different content MUST be rejected as an
idempotency conflict. A participant MUST durably record acceptance before
calling native code and durably record a terminal outcome before publishing it.

If a process restarts with an accepted assignment but no terminal outcome, the
assignment becomes `unknown`. Redelivery MUST NOT invoke native code. The
participant returns an `unknown` acknowledgement and outcome so an operator or
domain-specific reconciliation process can inspect physical reality.

Assignment acknowledgements use these statuses:

- `accepted`: durably recorded and entering adapter validation;
- `rejected`: invalid, unauthorized, unsupported, or conflicting;
- `unknown`: execution may have occurred and must not be blindly retried; and
- a terminal status when a stored terminal result is replayed.

An outcome acknowledges only the adapter-level result represented by its fields.
It is not evidence of physical success unless the adapter's capability contract
defines and supplies the necessary evidence.

### Robot-local resources

State and plan steps MAY name opaque robot-local resources such as tools,
fixtures, or locally managed zones. UMP does not assign universal physical
meaning to these identifiers. Before durable acceptance, the participant MUST
atomically reserve every requested local resource for the assignment. A conflict
is rejected without invoking native code. Reservations are released with a
durable terminal outcome.

An interrupted assignment that becomes `unknown` retains its reservations. They
MUST NOT be reused merely because the UMP process restarted; evidence-based or
operator/domain-specific resolution is required first.

## Cancellation boundary

A coordinator MAY request cancellation of an active plan. It MUST durably mark
pending assignments `cancelled` and in-flight assignments
`cancellation_requested` before publishing any `cancellation_request`. The
request includes the target robot, assignment ID, and a bounded human-readable
reason.

The participant MUST accept a cancellation request only from the authenticated
issuer that originally created the durable assignment. The native adapter keeps
final authority and returns an explicit accepted or rejected decision. UMP MUST
record and publish `cancelled` only after native acceptance. Native rejection is
not cancellation success. Already-terminal work remains immutable, unknown work
remains unknown, and an interrupted cancellation request recovers as `unknown`.

Cancellation is a task-coordination operation. It is not an emergency stop,
does not bypass local controllers, and MUST NOT be represented as a safety-rated
stop mechanism. Deployments continue to rely on robot-local safety systems and
physical emergency stops.

## Assignment authority

Every operational assignment includes `authority_lease_id`. The authenticated
envelope source is the issuer presenting that lease. Before durable assignment
acceptance, the target participant must verify against robot-local policy that:

- the lease exists and is active;
- its grantor is the local target robot;
- its holder equals the authenticated envelope source;
- the requested capability is in scope; and
- receiver-local time is safely within the lease window after clock uncertainty.

Missing, unknown, expired, revoked, not-yet-valid, wrong-issuer, and
out-of-capability leases are rejected. Sender timestamps must not extend lease
validity. Rejected authorization does not consume or bind the assignment ID.

## Coordinator lifecycle

A coordinator stores an immutable goal, plan, stable assignment identifiers, and
step dependencies before publishing the goal or plan. A step follows this state
machine:

```text
pending -> dispatched -> accepted -> succeeded | failed | rejected | unknown
                    \--------------------------^
                     -> cancellation_requested -> cancelled | unknown
```

An outcome may arrive before its acknowledgement and transition a dispatched
step directly to a terminal state. A delayed acknowledgement MUST NOT replace a
terminal result. Terminal results are immutable.

Only `succeeded` satisfies a dependency. A failed, rejected, or unknown step
blocks its dependent steps. Independent pending steps remain representable but
are not dispatched after a run has entered a terminal failed or unknown state.

The coordinator MUST bind acknowledgements and outcomes to the robot assigned in
the stored plan. Payload identity, envelope source identity, and assigned robot
identity must agree.

On coordinator restart, previously dispatched or accepted work becomes
`unknown`; it is not redispatched. Pending and completed records remain durable.
This conservative behavior prevents duplicate physical action.

The coordinator MAY publish an `assignment_query` for each unknown assignment.
Only the original authenticated issuer may query the participant journal. If a
durable terminal outcome exists, the participant returns an
`assignment_snapshot` containing the status, outcome, and canonical assignment
fingerprint. The coordinator MUST require agreement among authenticated source,
assigned robot, assignment ID, outcome identity and status, and the fingerprint
of its stored assignment. Only this evidence may transition `unknown` to
`succeeded`, `failed`, or `rejected`. An unknown snapshot leaves the run unknown
and MUST NOT cause redispatch. A recovered success may resume ready dependencies.

## Current conformance scope

The v0.1 reference tests cover envelope encoding, size bounds, identity matching,
session ordering, replay rejection, state freshness, plan capability matching,
dependency validation, input-schema validation, and duplicate assignments.
The suite also covers durable coordinator dispatch, delayed outcomes, dependency
progression, failure blocking, restart-to-unknown, authenticated reconciliation,
event-source binding, and out-of-order acknowledgements.

Physics-based execution remains required by the PRD but is not part of this
protocol increment yet. The implemented network alpha provides mutual TLS; its
remaining operational limitations are documented separately.
