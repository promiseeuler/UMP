# Owner Coordinator

`ump-coordinator` gives an owner a supported path from a shared goal to validated,
durable high-level assignments. It is a distinct mutual-TLS UMP identity and does
not issue actuator, trajectory, or emergency-stop commands.

## Goal Document

Create a bounded JSON goal such as:

```json
{
  "goal_id": "goal-move-package-1",
  "description": "Move the sealed package from intake to storage shelf A",
  "participant_ids": [
    "robot-humanoid-1",
    "robot-quadruped-1",
    "robot-mobile-arm-1"
  ],
  "constraints": {"keep_upright": true},
  "deadline_ms": 1770000000000
}
```

`deadline_ms` is an absolute Unix epoch timestamp in milliseconds. Every
participant must be an explicitly configured network peer.

## Submit

The coordinator needs its own issued credential, network configuration, and
durable stores. Participant network policies must permit its identity to receive
`manifest` and `state` and must permit the required collaboration messages in
the reverse direction.

```sh
ump-coordinator submit \
  --network /etc/ump/coordinator-network.json \
  --credential-database /var/lib/ump/coordinator-credentials.sqlite3 \
  --credential-directory /var/lib/ump/coordinator-credentials \
  --database /var/lib/ump/coordinator.sqlite3 \
  --planner owner_planner:create_planner \
  --planner-config /etc/ump/planner.json \
  --goal /etc/ump/goals/move-package.json \
  --authority-lease robot-humanoid-1=lease-humanoid \
  --authority-lease robot-quadruped-1=lease-quadruped \
  --authority-lease robot-mobile-arm-1=lease-arm
```

The command waits up to 30 seconds for a manifest and fresh semantic state from
every participant before invoking the planner. It then waits up to five minutes
for a terminal durable run result. Both limits are configurable.

Exit status is `0` for successful completion, `1` for a terminal non-successful
run, `2` for configuration/validation failure, and `3` for timeout or
interruption. A timeout or signal never fabricates a
cancellation; the printed run remains available for operator reconciliation.

## Cancel

Cancellation is a high-level request to each robot's native adapter. It is not an
emergency stop, and UMP never claims cancellation until the robot accepts it and
publishes terminal evidence.

Use the same coordinator network identity and durable journal that submitted the
plan:

```sh
ump-coordinator cancel \
  --network /etc/ump/coordinator-network.json \
  --credential-database /var/lib/ump/coordinator-credentials.sqlite3 \
  --credential-directory /var/lib/ump/coordinator-credentials \
  --database /var/lib/ump/coordinator.sqlite3 \
  --plan-id PLAN_ID \
  --reason "Operator ended supervised work"
```

If `submit` is still waiting on that host, interrupt it first so `cancel` can bind
the coordinator's configured listener. The journal is permanently bound to the
first coordinator identity that opens it; a different identity fails closed.
Cancellation may be requested for dispatched, accepted, or restart-uncertain
assignments. Pending dependent work is cancelled locally without publication.

The command prints the affected assignment IDs and waits up to 30 seconds for a
terminal run result. Exit status `0` means the durable run is `cancelled`; `1`
means it reached another terminal state, `2` indicates validation/configuration
failure, and `3` indicates timeout or interruption. Continue using the physical
emergency-stop system whenever immediate risk reduction is required.

## Status

Status is read without applying restart recovery or modifying the durable journal
and does not require network credentials:

```sh
ump-coordinator status \
  --database /var/lib/ump/coordinator.sqlite3 \
  --plan-id PLAN_ID
```

For restart-uncertain work, use `ump-coordinator reconcile` as documented in
`RECONCILIATION.md`. Recovery requires fresh context from all declared
participants and fingerprint-bound terminal evidence; assignments are never
blindly resent.

Planner proposals remain untrusted and pass the checks in `PLANNER.md`. Each
participant independently enforces its robot-local authority lease and native
safety policy before accepting an assignment.

The automated integration suite runs one coordinator and three participant
identities through a complete dependency-ordered collaboration over mutual TLS
TCP sockets, with CA identity checks, explicit disclosure policies, durable
inboxes/outboxes, and zero failed deliveries. This is localhost transport
evidence only; it does not replace the required two-host LAN or hardware pilot.
