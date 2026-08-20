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

## Status

Status is read without applying restart recovery or modifying the durable journal
and does not require network credentials:

```sh
ump-coordinator status \
  --database /var/lib/ump/coordinator.sqlite3 \
  --plan-id PLAN_ID
```

Planner proposals remain untrusted and pass the checks in `PLANNER.md`. Each
participant independently enforces its robot-local authority lease and native
safety policy before accepting an assignment.
