# UMP Assignment Authority v0.1

## Purpose

Mutual TLS proves which peer sent an assignment. A local authority lease decides
whether that authenticated peer may ask this robot to execute the named
high-level capability during a bounded time window.

Leases are stored and evaluated on the target robot. Remote peers cannot grant
themselves authority through a protocol message. The robot owner, manufacturer
integration, or an approved local policy service creates and revokes leases.

## Lease scope

Each lease contains:

- stable lease ID;
- target/grantor robot ID;
- authenticated issuer/coordinator ID;
- one or more exact versioned capability names;
- issue and expiry times;
- maximum clock uncertainty;
- monotonic revision; and
- active or revoked state.

The participant uses its receiver-local clock. Envelope timestamps do not affect
lease validity. To fail closed around clock error, a lease is rejected when the
local time plus declared uncertainty reaches expiry or local time minus
uncertainty is earlier than issue time.

## Secure defaults

`Participant` uses `DenyAllAuthorizer` unless an authorizer is supplied.
`Coordinator` requires an authority lease ID for every assigned robot unless a
test explicitly sets `require_authority=False`.

`AllowAllAuthorizer` is for deterministic simulation and conformance fixtures
only. It must not be used for a physical robot or production network.

## Owner CLI

The CLI operates on a local SQLite database. Times are Unix epoch milliseconds.

```sh
ump-authority \
  --robot-id robot-humanoid-1 \
  --database /var/lib/ump/authority.sqlite3 \
  grant \
  --lease-id owner-shift-a \
  --issuer-id warehouse-coordinator-1 \
  --capability ump.material.carry/v1 \
  --expires-at-ms 1800000000000
```

Renew a lease by repeating `grant` with the same lease and issuer identities and
exactly the next revision. Scope and time may be changed by that local renewal.

```sh
ump-authority \
  --robot-id robot-humanoid-1 \
  --database /var/lib/ump/authority.sqlite3 \
  revoke \
  --lease-id owner-shift-a \
  --expected-revision 1 \
  --reason "operator ended collaboration"
```

Inspect the append-only transition history:

```sh
ump-authority \
  --robot-id robot-humanoid-1 \
  --database /var/lib/ump/authority.sqlite3 \
  events \
  --lease-id owner-shift-a
```

## Runtime configuration

```python
from ump.authority import SqliteAuthorityStore
from ump.runtime import Participant

authority = SqliteAuthorityStore(
    "robot-humanoid-1", "/var/lib/ump/authority.sqlite3"
)
participant = Participant(adapter, bus, authorizer=authority)
```

The coordinator must reference the lease expected by each target:

```python
coordinator = Coordinator(
    "warehouse-coordinator-1",
    bus,
    registry,
    authority_lease_ids={"robot-humanoid-1": "owner-shift-a"},
)
```

Lease authorization is not functional-safety approval. Native adapters retain
final acceptance authority and local safety systems always take precedence.
