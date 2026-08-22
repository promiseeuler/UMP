# Robot Deployment Path

This path is for robot owners and manufacturers integrating UMP with an existing
robot controller. UMP exchanges semantic awareness and authorized high-level
work requests. It must not issue joint commands, trajectories, motor values, or
emergency-stop actions.

## 1. Establish ownership boundaries

Assign every robot and coordinator a stable UMP identity. Document who owns the
native controller, safety system, adapter, certificates, authority database, and
planner. Confirm that the robot can remain safe and locally controlled when UMP
is stopped or disconnected.

Create a bounded test area, physical emergency-stop procedure, operator roles,
and rollback plan before any assignment-capable integration. Do not use
`AllowAllAuthorizer` outside deterministic simulation.

## Local awareness lab

Before using manufacturer hardware, generate and verify a complete localhost
deployment bundle:

```sh
ump-deployment quickstart --output ump-local-lab
ump-deployment verify-local ump-local-lab
```

The verifier starts every generated mutual-TLS endpoint and proves that each
robot receives every peer manifest and semantic state. Generated adapters are
read-only and advertise no capabilities. Run each `preflight.sh`, then launch
each `run.sh` in a separate terminal to keep the network active.

Use `ump-deployment wizard --output my-ump-lab` for interactive identities, or
use `ump-deployment generate --topology <file> --output <directory>
--development-pki` for repeatable topology-as-code. Development credentials are
short-lived, include a local CA private key, and are never eligible for physical
or production deployment.

## 2. Run the reference simulation

Install the reviewed UMP build in an isolated environment and run:

```sh
ump-simulate run --project-root . --output simulated-qualification.json
ump-simulate validate simulated-qualification.json
ump-conformance conformance/v0.1
```

Stop if any check fails. A passing report verifies the software workflow, not
the physical robot.

## 3. Implement a read-only adapter

Start from `examples/read_only_adapter.py`. Replace only `manifest()` and
`state()` data sources with bounded manufacturer-native semantic APIs. Keep the
capability tuple empty and keep `accept()` rejecting assignments.

```sh
ump-adapter-conformance inspect \
  --adapter manufacturer_package.adapter:create_adapter \
  --adapter-config /etc/manufacturer/robot.json \
  --output adapter-conformance.json

ump-adapter-conformance verify adapter-conformance.json \
  --implementation manufacturer_package/adapter.py
```

Stop if identity, schemas, state freshness, or implementation binding fails.
Review the detailed contract in `MANUFACTURER_ADAPTER.md`.

## 4. Issue and enroll identities

Use the owner's existing PKI to issue one certificate per participant. Each leaf
certificate must contain exactly one URI subject alternative name of the form
`urn:ump:robot:<robot-id>`. Never share private keys between robots.

```sh
ump-credentials --robot-id robot-humanoid-1 \
  --database /var/lib/ump/credentials.sqlite3 \
  --directory /var/lib/ump/credentials \
  enroll --certificate issued/robot-humanoid-1.pem \
  --private-key issued/robot-humanoid-1.key \
  --ca issued/site-ca.pem

ump-credentials --robot-id robot-humanoid-1 \
  --database /var/lib/ump/credentials.sqlite3 \
  --directory /var/lib/ump/credentials \
  activate --generation 1
```

Use owner-only file permissions, encrypted storage, and a deployment-specific
hardware-backed key provider where required. See `CREDENTIALS.md` for rotation
and revocation.

## 5. Configure peer disclosure

Create one strict network configuration per participant from
`config/network.example.json`. Pin robot IDs and certificate fingerprints. In
the awareness stage, allow only `manifest` and `state`; do not allow assignment
messages or capabilities. Give replay, inbox, and outbox stores distinct durable
paths.

```sh
ump-network-config validate /etc/ump/robot-humanoid-network.json

ump-node --preflight \
  --network /etc/ump/robot-humanoid-network.json \
  --adapter manufacturer_package.adapter:create_adapter \
  --adapter-config /etc/manufacturer/robot.json \
  --assignment-database /var/lib/ump/assignments.sqlite3 \
  --authority-database /var/lib/ump/authority.sqlite3 \
  --credential-database /var/lib/ump/credentials.sqlite3 \
  --credential-directory /var/lib/ump/credentials \
  --inspector-database /var/lib/ump/inspector.sqlite3 \
  --state-hz 2
```

Preflight must pass before the node is started. Repeat independently for every
robot and for the owner coordinator.

## 6. Operate in awareness-only mode

Start each participant with the same arguments, omitting `--preflight`. Observe
manifests, activity, intent, safety state, health, optional battery telemetry,
freshness, and communication-loss behavior through the read-only inspector.
Never substitute guessed values when a native API does not expose a field; use
`unknown` or omit optional battery telemetry. Exercise disconnect, restart,
certificate rotation, replay, stale-state, and storage recovery procedures.

```sh
ump-inspector --database /var/lib/ump/inspector.sqlite3 \
  --host 127.0.0.1 --port 8765

ump-network-diagnostics --network /etc/ump/robot-humanoid-network.json
```

The inspector database path must match the path used by the running node. An
empty UI means no compatible messages have been recorded there; it does not prove
that hardware, ROS, or a manufacturer SDK is disconnected elsewhere.

Keep this stage running for an owner-defined soak period. Do not progress while
there are dead letters, stale retry backlogs, unexplained identity changes, or
unsafe communication-loss behavior.

## 7. Add one high-level capability

Select one bounded capability whose semantics match the native API exactly.
Prefer a contract from `ump.standard/v1`; otherwise use a manufacturer-owned,
versioned schema. Implement native operating-mode, workspace, resource, and
safety checks inside `accept()`. Native software retains final acceptance and
cancellation authority.

Exercise the adapter first in deterministic simulation, then ROS 2/Gazebo or
the manufacturer's professional simulator. Run execution
conformance only with its
explicit native-execution gate and retain the adapter version, firmware,
configuration, fixtures, and result.

## 8. Grant bounded assignment authority

The target robot owner grants authority locally after adapter and safety review:

```sh
ump-authority --robot-id robot-humanoid-1 \
  --database /var/lib/ump/authority.sqlite3 \
  grant --lease-id owner-shift-a \
  --issuer-id warehouse-coordinator-1 \
  --capability ump.material.carry/v1 \
  --expires-at-ms 1800000000000
```

Use short validity windows and exact capability scopes. Update network policy to
permit only the reviewed coordinator messages and capability. Revoke the lease
immediately when the supervised session ends.

## 9. Submit a supervised goal

Validate the goal and preflight the coordinator before opening the bounded work
area:

```sh
ump-goal validate /etc/ump/goals/move-package.json

ump-coordinator preflight \
  --network /etc/ump/coordinator-network.json \
  --credential-database /var/lib/ump/coordinator-credentials.sqlite3 \
  --credential-directory /var/lib/ump/coordinator-credentials \
  --database /var/lib/ump/coordinator.sqlite3

ump-coordinator submit \
  --network /etc/ump/coordinator-network.json \
  --credential-database /var/lib/ump/coordinator-credentials.sqlite3 \
  --credential-directory /var/lib/ump/coordinator-credentials \
  --database /var/lib/ump/coordinator.sqlite3 \
  --planner owner_planner:create_planner \
  --goal /etc/ump/goals/move-package.json \
  --authority-lease robot-humanoid-1=owner-shift-a
```

An operator must supervise the first physical assignments with functioning
physical emergency stops. UMP cancellation is only a high-level request and
must never replace the robot's safety system.

## 10. Expand and qualify

Add capabilities and robots one at a time. Repeat conformance, simulation,
preflight, authority, and supervised pilot evidence for every adapter build.
Before production claims, complete the two-host LAN measurement, native
professional-simulator run, hardware pilot, independent security/safety/
interoperability reviews, and tagged release evidence listed in
`compliance/qualification.json`.
