# Unknown Assignment Reconciliation

An assignment becomes `unknown` when UMP cannot prove whether native work
completed, such as after a process crash, action-result timeout, or interrupted
cancellation. Unknown work is never retried automatically and continues holding
its declared robot-local resources.

## Automatic evidence

The coordinator sends an `assignment_query` to the assigned robot. If that
participant has a durable terminal outcome, it returns an authenticated
`assignment_snapshot`. The coordinator requires the original robot identity and
assignment fingerprint before reconciling the run.

## Operator or manufacturer evidence

When no native terminal outcome exists, an authorized operator or manufacturer
service can inspect domain evidence such as a controller mission log, fixture
sensor, barcode scan, or physical inspection. Record that determination on the
assigned robot host:

```sh
ump-reconcile \
  --database var/assignments.sqlite3 \
  --assignment-id assignment-42 \
  --status succeeded \
  --description "Destination sensor and mission log confirm placement" \
  --resolver-id operator/alice \
  --evidence "inspection-record:site-a/2026-08-20/42"
```

Allowed determinations are `succeeded`, `failed`, `rejected`, and `cancelled`.
The command does not control the robot or infer a status. It only persists the
explicit determination, resolver identity, evidence reference, and occurrence
time in one transaction. The resulting terminal outcome is immutable and held
resources are released.

Afterward, run coordinator reconciliation with the same authenticated identity
and journal that submitted the plan:

```sh
ump-coordinator reconcile \
  --network /etc/ump/coordinator-network.json \
  --credential-database /var/lib/ump/coordinator-credentials.sqlite3 \
  --credential-directory /var/lib/ump/coordinator-credentials \
  --database /var/lib/ump/coordinator.sqlite3 \
  --plan-id PLAN_ID
```

The command first requires fresh manifest and semantic state from every goal
participant. It conservatively marks interrupted dispatched/accepted work
unknown, queries each unknown assignment, validates authenticated robot identity
and assignment fingerprint, and resumes dependent work only after durable
successful evidence. It waits for a known terminal run by default. Timeout or
remaining uncertainty exits nonzero and never triggers blind execution.

`ump-reconcile` is the robot-local operator evidence command;
`ump-coordinator reconcile` is the network query and coordinator recovery
command. Neither command infers physical state.

Deployments are responsible for restricting command access to authorized local
operators and for defining acceptable evidence per capability. Evidence should
be a bounded audit reference, not secret material or raw sensor payloads. UMP
does not claim that an evidence source is truthful; that trust remains with the
robot owner and manufacturer safety process.
