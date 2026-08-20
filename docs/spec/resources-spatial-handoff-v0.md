# UMP Resources, Spatial Context, and Handoffs v0.1

**Status:** Phase 3 implementation specification  
**Normative language:** MUST, MUST NOT, SHOULD, and MAY are interpreted as requirements.

## Resource reservations

Resources use stable namespaced identifiers and declare exclusive, shared, or capacity concurrency. Exclusive resources have effective capacity one. Capacity claims MUST request a positive quantity no greater than declared capacity.

A multi-resource request MUST be evaluated atomically in ascending resource-ID order. Either every claim becomes active or none does. Implementations MUST NOT hold a partial set while waiting for another resource. This ordering and all-or-none rule prevent indefinite acquisition deadlock inside one manager.

Reservations identify owner, related task, claims, issue time, expiry, status, and monotonic revision. Release, revocation, and expiry free capacity exactly once and notify related active tasks. A stale revision MUST NOT mutate a reservation.

## Spatial context

Core coordinates use SI metres and radians. A pose names both reference and subject frames, source time, maximum age, and position/orientation uncertainty. Quaternions MUST be finite, non-zero, and normalized within declared tolerance.

An adapter MUST resolve the reference frame before physical execution. Missing transforms, data at or beyond maximum age, clock regression, or uncertainty above the capability's declared threshold MUST block execution. UMP MUST NOT silently assume that identically named frames from different authorities are equivalent.

## Handoff lifecycle

The lifecycle is:

```text
PROPOSED -> PREPARED -> READY -> TRANSFERRING -> COMMITTED
     |          |         |            |
     +----------+---------+------------+-> ABORTED | FAILED | UNKNOWN
UNKNOWN -> COMMITTED | ABORTED | FAILED
```

The proposal identifies source, destination, subject, transfer context, reservation, preconditions, related task, and deadline. The source prepares; the destination declares readiness; the source begins transfer. Commit requires positive completion evidence from both authenticated parties.

## Ownership invariant

Exactly one authoritative owner MUST be recorded at every durable revision. The source remains owner in `PROPOSED`, `PREPARED`, `READY`, `TRANSFERRING`, `ABORTED`, `FAILED`, and `UNKNOWN`. Ownership changes to the destination only in the same durable event that records `COMMITTED` after bilateral evidence.

No restart, timeout, disconnect, or contradictory evidence may infer a transfer. Ambiguity enters `UNKNOWN` with `inspection_required=true` and `retry_safe=false`. A physical transfer in `UNKNOWN` MUST NOT be retried blindly.

## Safety and recovery

A handoff MUST have an active reservation for its transfer resource and fresh, resolvable spatial context before `PREPARED`, `READY`, or `TRANSFERRING`. Reservation loss, stale transform, deadline expiry, or participant disappearance blocks further forward progress and triggers abort when no physical effect may have happened, otherwise `UNKNOWN`.

Every reservation and handoff mutation MUST be durably journaled with sequence, actor, correlation, causation, prior state, next state, time, and reason. Reconciliation accepts only authenticated, monotonic records that preserve identity and the ownership invariant. Conflicting committed ownership requires inspection.
