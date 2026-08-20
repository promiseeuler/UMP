# UMP Mutual-TLS Network Profile v0.1

## Status

This profile is an implemented network alpha for controlled local testing. It is
not yet approved for unattended physical robot operation. Remaining production
gates are listed in `docs/STATUS.md`.

## Trust model

Operational UMP messages use mutual TLS. Both peers must present certificates
issued by a locally trusted certification authority. The certificate must contain
exactly one URI Subject Alternative Name in this form:

```text
urn:ump:robot:<robot-id>
```

Common Names are not used for UMP identity. The authenticated certificate robot
ID must equal the envelope `source_id`. Clients also require the server's robot
ID and may pin the SHA-256 fingerprint advertised by discovery or configuration.

TLS authenticates identity and encrypts transport. It does not grant permission
to inspect metadata or issue assignments. Peer disclosure policy and scoped
assignment leases are separate authorization decisions.

## Metadata disclosure

Each configured peer has an outbound allowlist of message types. The default is
empty, so adding a peer or accepting a discovery hint discloses no operational
metadata. `manifest` permission may additionally name exact versioned
capabilities; all other capabilities are removed from that peer's copy before
transport. Omitting `state` hides current activity, intent, progress, blockers,
resources, and assignment correlation from that peer.

Disclosure filtering occurs before TLS transmission. It does not modify the
full local event delivered to local subscribers. Assignment permission is still
evaluated independently by the target robot's local authority lease.

## TLS requirements

- TLS 1.3 minimum.
- Client certificates required by every server.
- Certificate chains validated against the configured local CA bundle.
- ALPN must negotiate `ump/0.1`.
- Exactly one length-prefixed UMP envelope and one delivery acknowledgement per
  TLS connection in v0.1.
- Four-byte unsigned big-endian frame length.
- Encoded frame length from 1 through 65,536 bytes.
- Connection and handshake timeouts configured from 0.1 through 60 seconds.

The receiver returns `ump-delivery-ack/0.1` with the matching message ID and a
status of `received`, `processed`, or `duplicate`. Configured receivers return
`received` only after the envelope is durable in their inbox. `duplicate` means
the exact message is already durable and will not be processed twice. Direct
test servers without an inbox return `processed` after synchronous handling.

The one-message connection profile prioritizes simple failure boundaries and
testability. A measured persistent-session profile may replace it in a compatible
future minor version while preserving these acknowledgement semantics.

## Durable delivery

Configured buses write every peer-specific disclosed envelope to a SQLite WAL
outbox before local publication. A bounded queue applies backpressure by raising
an error without partially enqueueing a multi-peer batch. Delivery remains FIFO
for each peer even across source-session changes. Failed attempts use capped
exponential backoff, survive sender restart, and are retried by a background
worker after the peer returns.

The receiver stages an envelope, commits its replay sequence floor, then activates
the inbox row for application workers. This ordering closes the crash windows on
both sides of replay persistence. Workers preserve order per source while
allowing different sources to execute concurrently. Handler failures become
durable dead letters exposed through `inbox_counts` and `inbox_failures`; they are
not blindly re-executed.

### Safety priority

Safety-state envelopes use the `safety` stream. The durable outbox maintains one
FIFO head per peer and stream, selects safety heads first, and reserves configured
capacity that operational backlog cannot consume. Safety and operational TLS
sends use independent serialization locks, so one blocked operational connection
does not block opening a safety connection. A currently in-flight kernel or TLS
operation is not cancelled.

The durable inbox orders each source, session, and stream independently and
selects ready safety rows first. Replay floors are also stream-qualified. Existing
v0.1 databases migrate operational rows and replay floors to the `operational`
stream without discarding data. The default operational envelope encoding omits
the stream field, preserving prior canonical bytes.

A delivery acknowledgement proves durable protocol receipt only. It does not
prove that a robot accepted an assignment or completed physical work. Assignment
acknowledgements, outcomes, reconciliation, and local safety policy remain the
authoritative application lifecycle.

## Replay behavior

The receiver binds sequence state to authenticated robot ID and session ID. A
sequence less than or equal to the last accepted sequence is rejected before
application delivery. Buses created with `TlsNetworkBus.from_config` persist the
highest accepted sequence for every peer session in the configured SQLite WAL
database before application delivery. Exact and lower-sequence replays remain
rejected after receiver restart.

The direct `TlsMessageServer` constructor defaults to a bounded in-memory window
of 4,096 sessions for isolated tests. Production code must use the file-based
configuration or explicitly supply `SqliteReplayProtector`. Durable session rows
are not automatically evicted because UMP v0.1 has not defined a cryptographic
session-expiry rule; deleting them would reopen acceptance of old traffic.

## Discovery

Discovery uses a compact expiring UDP JSON announcement containing only:

- discovery protocol version;
- claimed robot ID;
- host and TLS port;
- claimed certificate SHA-256 fingerprint; and
- expiry time.

A discovery packet is an untrusted routing hint. It must never create authority,
enroll a certificate, disclose protected metadata, or prove identity. The sender
address may be preferred over the claimed host to reduce redirection. The next
TLS connection must validate CA chain, ALPN, certificate robot ID, and optional
fingerprint before any operational message is accepted.

## Configuration

See `config/network.example.json`. `replay_database_path`,
`inbox_database_path`, and `outbox_database_path` must point to durable local
storage. `maximum_pending_deliveries` sets the bounded backpressure limit and
`reserved_safety_deliveries` protects a subset from operational use. Each
peer explicitly configures
`allowed_message_types` and `allowed_capabilities`. Credential paths may be
absolute or relative to the configuration file. Private keys should be readable
only by the UMP service account; participant and coordinator startup reject group-
or world-accessible key files. Use a hardware-backed key store where the platform
supports one.

Validate configuration before participant or coordinator preflight:

```sh
ump-network-config validate /etc/ump/network.json
ump-network-config schema
```

The loader and public `schemas/ump-network-config-v1.schema.json` reject unknown
top-level and peer fields, missing fields, booleans used as numbers, duplicate
policy entries, uppercase or malformed certificate pins, unversioned capability
names, unsupported message types, oversized peer/policy lists, invalid bounds,
and missing credential files. Unknown fields fail closed because silently
ignoring a misspelled pin or disclosure key could change security behavior.

Successful validation prints robot identity, bind endpoint, peer count, disclosure
policy names, queue bounds, and timeout. It deliberately omits certificate, key,
CA, and database paths. This command parses files but does not verify managed
credential activation, key permissions, database-role isolation, or TLS context
construction; use `ump-node --preflight` or `ump-coordinator preflight` for those
deployment checks.

```python
from ump.network import TlsNetworkBus, load_network_config

config = load_network_config("config/network.json")
bus = TlsNetworkBus.from_config(config)
host, port = bus.start()

# Register Participant or Registry with bus, then publish normally.

bus.stop()
```

`delivery_metrics` reports pending, delivered, and failed-attempt totals.
`delivery_errors` retains concrete outbound connection, TLS, identity, framing,
and acknowledgement errors. Applications must monitor both, plus inbox dead
letters, and surface degraded connectivity to operators.

## Certificate enrollment

UMP deliberately does not invent a certificate authority. Robot owners or
manufacturers provision certificates through their chosen PKI. The implemented
robot-local lifecycle validates and stages issued bundles, activates rotations
transactionally, records an audit trail, and rejects locally revoked peer
fingerprints on every inbound and outbound handshake. See `CREDENTIALS.md`.
Test certificates generated by the suite are ephemeral and must never be reused.
