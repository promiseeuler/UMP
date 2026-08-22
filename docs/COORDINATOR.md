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

Validate a goal before deployment, or print the versioned contract for tooling:

```sh
ump-goal validate /etc/ump/goals/move-package.json
ump-goal schema
```

Goal documents reject unknown fields, duplicate participants, non-object
constraints, invalid identifiers, and non-integer deadlines. Validation output
contains IDs and participant names but omits descriptions and constraints.

## Submit

The coordinator needs its own issued credential, network configuration, and
durable stores. Participant network policies must permit its identity to receive
`manifest` and `state` and must permit the required collaboration messages in
the reverse direction.

Validate deployment inputs before loading a planner, opening the coordinator
journal, or binding a listener:

```sh
ump-coordinator preflight \
  --network /etc/ump/coordinator-network.json \
  --credential-database /var/lib/ump/coordinator-credentials.sqlite3 \
  --credential-directory /var/lib/ump/coordinator-credentials \
  --database /var/lib/ump/coordinator.sqlite3 \
  --inspector-database /var/lib/ump/coordinator-inspector.sqlite3
```

Preflight validates the network configuration, current active credential,
certificate/key/CA compatibility, owner-only private-key permissions, writable
storage parents, and distinct coordinator, credential, replay, inbox, outbox,
and optional inspector database paths. It emits one JSON report and creates none
of those runtime stores. Port availability and peer reachability remain runtime
properties.

Add the same optional `--inspector-database` argument to `submit`, `cancel`, or
`reconcile` to retain the coordinator's local and authenticated remote protocol
view. Runtime health checks include the recorder during participant, completion,
and reconciliation waits. Serve that database separately with `ump-inspector`.

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

## Goal Batches

To submit a list of tasks, place 1–256 goal objects in a JSON array and replace
`--goal` with `--goals`:

```sh
ump-coordinator submit \
  --network /etc/ump/coordinator-network.json \
  --credential-database /var/lib/ump/coordinator-credentials.sqlite3 \
  --credential-directory /var/lib/ump/coordinator-credentials \
  --database /var/lib/ump/coordinator.sqlite3 \
  --planner owner_planner:create_planner \
  --goals /etc/ump/goals/shift-tasks.json \
  --authority-lease robot-humanoid-1=lease-humanoid \
  --authority-lease robot-quadruped-1=lease-quadruped
```

Goal IDs must be unique. The coordinator waits for the union of declared
participants and validates every proposed plan before journaling or publishing
any run. This prevents a partly accepted batch when one proposal is invalid.
Runs then progress independently under one bounded completion deadline. JSON
events use `submitted_batch` and `completed_batch`, with one durable run snapshot
per goal. Exit status is successful only when every run succeeds.

Use `ump-goal validate-batch /etc/ump/goals/shift-tasks.json` to validate a
batch independently. `ump-goal batch-schema` prints its JSON Schema.

Exit status is `0` for successful completion, `1` for a terminal non-successful
run, `2` for configuration/validation failure, and `3` for timeout or
interruption. A timeout or signal never fabricates a
cancellation; the printed run remains available for operator reconciliation.

All networked coordinator commands enforce the same private-key, writable-parent,
and database-role isolation checks as preflight. Read-only `status` and `runs`
remain usable without network credentials and do not perform restart recovery.

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

List recent runs newest first, optionally filtered by durable status:

```sh
ump-coordinator runs \
  --database /var/lib/ump/coordinator.sqlite3 \
  --status active \
  --limit 100
```

Run history is also read-only. The limit must be between 1 and 1,000, and each
entry includes the plan ID, goal ID, status, creation time, and update time.

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
evidence only; it does not qualify a network or physical robot deployment.
