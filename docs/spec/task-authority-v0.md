# UMP Task and Authority v0.1

**Status:** Phase 2 implementation specification  
**Normative language:** MUST, MUST NOT, SHOULD, and MAY are interpreted as requirements.

## Scope

This profile defines high-level task requests, bounded authority leases, progress, cancellation, terminal outcomes, durable recovery, and reconciliation. It never grants direct actuator access. Native controllers and local safety policy retain precedence.

## Identity and correlation

Every task message MUST arrive on an authenticated UMP session. `source_machine_id` MUST match the transport-bound identity. Task messages MUST carry a non-empty `correlation_id`; responses MUST preserve it and set `causation_id` to the message that caused the transition.

`task_id` identifies one logical task. `idempotency_key` identifies one requested effect. An executor MUST return the recorded outcome when either identifier is redelivered with identical immutable fields. Reuse with different issuer, capability, input, lease, or policy MUST be rejected as a conflict.

## Authority leases

A task MUST NOT be accepted unless an ACTIVE lease:

- names the authenticated issuer as holder and the executor as grantor;
- contains the requested capability;
- is valid after subtracting declared clock uncertainty from expiry;
- has not been revoked; and
- does not conflict with another active exclusive lease over the same capability or resource.

Lease revisions MUST increase by one on renewal or revocation. Renewal after expiry and renewal of a non-renewable lease MUST fail. Revocation and expiry MUST prevent new execution immediately and invoke the adapter's declared local safe behavior for affected active work.

## Task lifecycle

The allowed lifecycle is:

```text
REQUESTED -> ACCEPTED -> RUNNING -> SUCCEEDED | FAILED | CANCELLED | UNKNOWN
RUNNING   -> RETRY_PENDING -> RUNNING
REQUESTED -> REJECTED | CANCELLED
ACCEPTED  -> CANCELLED | UNKNOWN
RUNNING   -> CANCEL_PENDING -> CANCELLED | SUCCEEDED | FAILED | UNKNOWN
UNKNOWN   -> SUCCEEDED | FAILED | CANCELLED
```

Terminal states are `SUCCEEDED`, `FAILED`, `CANCELLED`, and `REJECTED`. `UNKNOWN` is non-terminal and requires inspection or reconciliation. No transition may leave a terminal state.

Cancellation before execution MUST prevent handler invocation. During execution, interruptible work enters `CANCEL_PENDING`; non-interruptible work continues and reports its real terminal outcome. Cancellation MUST NOT fabricate success or cancellation after a physical effect may already have occurred.

## Deadlines, retries, and outcomes

Expired requests MUST be rejected before acceptance. Retry attempts MUST be bounded by `maximum_attempts` and the task deadline.

- `SAFE_TO_RETRY` permits another handler attempt after a recorded pre-effect failure.
- `AT_MOST_ONCE` MUST never invoke the handler more than once.
- `RECONCILE_REQUIRED` MUST enter `UNKNOWN` after loss of durable certainty.

After a crash, an accepted or running non-idempotent task with no durable terminal event MUST become `UNKNOWN` with `inspection_required=true`; it MUST NOT execute again automatically.

A retryable failure MUST durably enter `RETRY_PENDING` with a future attempt time. The runtime MUST enforce the requested attempt bound, deadline, and non-zero backoff before issuing another execution permit. Exhausted retries become `FAILED`; uncertain outcomes become `UNKNOWN` regardless of retry policy.

## Durability and reconciliation

Acceptance MUST be durably appended before handler invocation. Every state transition MUST append an event containing sequence, task, issuer, executor, correlation, causation, time, prior state, next state, and reason. Production profiles MUST place the journal on durable storage and SHOULD export it to append-only audit storage.

On reconnect, peers exchange task snapshots. The highest valid revision wins only when it extends an allowed local transition. Conflicting terminal outcomes or ambiguous physical effects produce `UNKNOWN`; they are never resolved by blind retry.

## Limits

An implementation MUST bound input/output bytes, task count, identifier lengths, reconciliation batch size, and journal record size. Malformed enum values, missing required semantic fields, revision regressions, and oversized values MUST be rejected without terminating the runtime.
