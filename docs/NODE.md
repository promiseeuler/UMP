# Participant Node

`ump-node` runs one trusted manufacturer adapter as a long-lived UMP participant.
It provides the operational path for owners who do not want to embed the Python
runtime in another service.

## Configuration

Prepare the mutual-TLS configuration described in `NETWORK_PROFILE.md`, then run:

```sh
ump-node \
  --network /etc/ump/network.json \
  --adapter manufacturer_package.adapter:create_adapter \
  --adapter-config /etc/manufacturer/robot.json \
  --assignment-database /var/lib/ump/assignments.sqlite3 \
  --authority-database /var/lib/ump/authority.sqlite3 \
  --credential-database /var/lib/ump/credentials.sqlite3 \
  --credential-directory /var/lib/ump/credentials \
  --state-hz 2
```

The adapter value is a trusted Python `module:factory`. The factory receives the
adapter configuration as a `pathlib.Path`, or `None` when `--adapter-config` is
omitted, and must return a `RobotAdapter`. Installing an adapter grants native
code execution to that package; use only owner- or manufacturer-approved builds.

The adapter manifest robot ID must exactly match the network configuration and
certificate URI identity. The node performs read-only adapter conformance before
opening its listener. `state-hz` is bounded to the PRD's 1–10 Hz range. The node
starts its mutual-TLS listener, publishes its manifest and initial state,
then publishes semantic state at the selected rate until `SIGINT` or `SIGTERM`.
It emits one JSON `ready` event after successful startup.

The network certificate, private key, and CA paths must exactly match the active
generation in the robot-local credential store. Peer revocations are checked on
every inbound and outbound handshake. The node also rechecks its active local
generation before every state publication and exits if that generation is
retired or revoked; restart it after an approved credential rotation.

## Authority and durability

The assignment and authority databases are mandatory. A new authority database
contains no leases, so remote assignments are denied by default. Owners grant
specific issuer/capability/time scopes with `ump-authority`; see `AUTHORITY.md`.

The network configuration also requires durable replay, inbox, and outbox paths.
Place all databases on persistent local storage owned by the UMP service account.
Do not share one database file between robots or copy a live database between
identities.

`--execution-workers` defaults to zero, preserving synchronous adapter execution.
A manufacturer may select 1–32 workers only when its native API safely permits
independent high-level requests. UMP resource declarations and the native robot
controller remain authoritative over concurrency.

## Read-only rollout

From a source checkout, the included example can exercise the node path:

```sh
ump-node --network config/network.json \
  --adapter examples.read_only_adapter:create_adapter \
  --assignment-database state/assignments.sqlite3 \
  --authority-database state/authority.sqlite3 \
  --credential-database state/credentials.sqlite3 \
  --credential-directory state/credentials
```

Replace the example paths and credentials before running it. The example
advertises no capabilities and rejects all assignments; it is intended only as
the first supervised awareness-integration stage.
