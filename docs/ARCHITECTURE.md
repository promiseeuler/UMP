# UMP v0 Architecture

## Boundary

UMP communicates observations, intent, goals, plans, high-level assignments, and
outcomes. A robot adapter is the hard boundary between UMP and native robot
software. Only the native side may invoke navigation, manipulation, or actuator
APIs.

```text
Reasoning provider -> proposed plan -> UMP validator -> assignments
                                                   |
Robot peers <- semantic state <- UMP participant <- adapter -> native controller
```

The validator never turns a plan directly into motion. An assignment remains a
request that the target adapter may reject.

## Portable interfaces

`RobotAdapter` exposes manifest and state reads plus high-level assignment and
cancellation methods. Cancellation returns a native accepted/rejected decision;
it is never treated as an actuator command or emergency stop. A simulation
adapter and a physical robot adapter implement the same interface. `Planner`
accepts a shared goal and immutable participant snapshot, then returns a
proposal. The reference planner is deterministic; an AI model can replace it
without changing adapters.

## Transport bindings

`InMemoryBus` is a deterministic development binding. It exercises envelope,
registry, state, and collaboration semantics without claiming to be a production
network protocol. A secure network binding will preserve the same messages and
participant behavior.

Every binding must pass bytes through the canonical codec before delivery. This
keeps tests honest about serialization, size limits, enum representation, and
unknown data even when sender and receiver live in the same process.

`TlsNetworkBus` implements the same publish/subscribe interface for one process
identity. Local publications are delivered locally and forwarded to configured
peers. Authenticated remote messages are injected locally without being
rebroadcast, preventing forwarding loops. Operational traffic uses TLS 1.3 with
mandatory client certificates and certificate-to-envelope identity binding.

UDP discovery supplies expiring endpoint and certificate-fingerprint hints only.
Trust is established by the subsequent TLS exchange, never by discovery.

## Coordinator execution model

The coordinator is event-driven. It journals a validated plan and all stable
assignment identifiers before publication, journals dispatch before delivery,
and progresses dependencies only after durable successful outcomes. SQLite WAL
storage provides restart-safe run and step state.

The in-memory bus delivers events synchronously, so the reference demo can still
finish inside one call. Tests also delay acknowledgements and outcomes to verify
that no call-stack assumption controls dependency progression.

The coordinator accepts one goal or a bounded batch. Every proposal in a batch
is validated before any run is journaled or published. Replanning is additive:
a terminal plan remains immutable while a new plan receives a new identifier,
an exactly incremented revision, and an explicit predecessor identifier.

Participants execute adapters synchronously by default for deterministic
simulation. Manufacturers may opt into a bounded `execution_workers` pool for
independent native high-level requests. Authorization, schema validation,
resource reservation, durable acceptance, and acknowledgement still happen
before scheduling. A reentrant publication lock preserves envelope sequence
order across workers. Adapter exceptions become terminal `unknown` evidence
because physical execution may have begun; they are never labelled as safe
failures or retried blindly.

On restart, dispatched or accepted work is conservatively marked unknown and is
not resent. The coordinator can query the original participant for a durable
terminal outcome. A response is accepted only when its authenticated robot
identity and assignment fingerprint match the coordinator journal; a recovered
success resumes newly ready dependencies. Unknown participant state remains
blocked. Timeout policy remains a subsequent protocol increment.

Assignment authority is enforced at the robot boundary with durable,
receiver-local capability leases. The coordinator requires lease identifiers by
default, and the participant independently validates holder, target, capability,
revision, revocation state, and receiver-local time before accepting work.

Peer metadata disclosure is enforced at the outbound network fan-out. Each peer
receives only allowed message classes, and manifest capabilities are filtered by
exact versioned name. Discovery creates routing information but no visibility.
Thus a planner using a network-populated registry sees only context already
authorized for its participant identity.

Robot-local resource reservations live in the participant assignment journal.
Reservation and durable acceptance share one transaction, and terminal outcome
recording releases reservations in the same transaction. Resource names remain
opaque to UMP so manufacturers retain their own safety and scheduling semantics.
