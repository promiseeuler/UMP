# Universal Machine Protocol

Universal Machine Protocol (UMP) is a manufacturer-neutral semantic layer for
shared robot awareness and high-level collaboration. It lets heterogeneous
robots describe what they are, what they can do, what they are doing, and their
current operational state using one bounded protocol.

UMP may carry shared goals and validated assignments, but it does not control
joints, motors, navigation, manipulation, gait, or emergency stops. Every robot
keeps its native controller, autonomy, acceptance checks, and safety system.

> **Project status:** UMP is an alpha reference implementation and software
> qualification laboratory. It is suitable for integration development and
> supervised hardware trials after the readiness gate passes. It is not a
> universal robot certification or a replacement for manufacturer safety
> controls.

## What UMP provides

- A versioned UMP 0.1 wire protocol with canonical encoding and conformance vectors.
- Shared identity, capabilities, activity, intent, progress, availability,
  pose, safety, health, battery, and freshness.
- A manufacturer `RobotAdapter` boundary that starts read-only.
- Mutual-TLS transport with discovery, authentication, replay protection,
  disclosure policy, durable inboxes, and durable outboxes.
- Goals, planning, bounded assignments, outcomes, cancellation, authority
  leases, and restart reconciliation.
- Integrations for MassRobotics, VDA 5050, Open-RMF, ROS 2, and OPC UA Robotics.
- A read-only inspector that displays only recorded UMP traffic.
- A mixed-fleet laboratory with live middleware, faults, load profiles, signed
  evidence, and a Gazebo reference world.

```text
native controller <-> manufacturer adapter <-> UMP participant/network
                  <-> registry, inspector, and coordinator
                  <-> validated plan + owner-issued authority lease
                  <-> bounded assignment <-> native acceptance and execution
```

## Requirements

The core requires Python 3.11 or newer, Git, and macOS or Linux. The repeatable
standards and visual lab requires Docker Desktop on macOS, including Apple
Silicon, or Docker Engine with Compose on Linux. ROS 2, Gazebo, MQTT, and OPC UA
are optional and lazy-loaded.

## Install

```sh
git clone https://github.com/promiseeuler/UMP.git
cd UMP
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install .
```

Install pinned WebSocket, MQTT, and OPC UA lab dependencies when needed:

```sh
python3 -m pip install '.[lab]'
```

Confirm the tools are installed:

```sh
ump-demo --help
ump-lab --help
ump-readiness --help
ump-ops --help
```

## Five-minute verification

Run the mixed-fleet scenario: a mobile robot delivers a payload, an inspection
robot checks the destination, and a manipulator places the payload after both
dependencies succeed.

```sh
ump-lab run --output .ump-lab/scenario.json
ump-readiness report --output .ump-lab/readiness.json
ump-readiness verify .ump-lab/readiness.json
```

Success returns `"passed": true` for the scenario and `"valid": true` for the
report. Every assignment passes through plan validation, a scoped authority
lease, the adapter boundary, and native-style acceptance. This proves the
deterministic software path, not physical compatibility.

Run all Python and protocol checks:

```sh
python3 -m unittest discover -s tests -v
ump-conformance conformance/v0.1
```

## Docker readiness lab

Docker Compose is the supported repeatable macOS and Linux environment. The
first ROS 2 or Gazebo build is large and can take several minutes.

Run the core scenario:

```sh
docker compose -f docker-compose.lab.yml \
  --profile core run --build --rm core
```

Run real local MQTT, WebSocket, OPC UA, and ROS 2 probes:

```sh
docker compose -f docker-compose.lab.yml \
  --profile standards up -d --wait mqtt
docker compose -f docker-compose.lab.yml \
  --profile standards run --build --rm standards
docker compose -f docker-compose.lab.yml \
  --profile standards run --rm standards python -m ump.lab_services opc_ua
docker compose -f docker-compose.lab.yml \
  --profile standards run --build --rm ros2
```

Run the delayed-network and headless Gazebo scenarios:

```sh
docker compose -f docker-compose.lab.yml \
  --profile fault run --build --rm fault-test
docker compose -f docker-compose.lab.yml \
  --profile visual run --build --rm visual
```

The visual profile loads all three robots and a payload in Gazebo Harmonic.
UMP assignments move models through the controller's native execution boundary;
there is no simulator-only execution path inside UMP core.

Stop persistent services when finished:

```sh
docker compose -f docker-compose.lab.yml --profile standards down
docker compose -f docker-compose.lab.yml --profile fault down
```

## Fault, load, and soak tests

```sh
ump-lab load --participants 3 --cycles 10
ump-lab load --participants 25 --cycles 10
ump-lab load --participants 100 --cycles 10
ump-lab load --participants 250 --cycles 10
ump-lab fault config/fault-baseline.json --output .ump-lab/faults.json
```

The release soak is a real eight-hour run:

```sh
ump-lab soak --duration-s 28800 --participants 25 --state-hz 2
```

Scheduled CI requires a self-hosted Linux runner labeled `ump-soak`. A queued,
shortened, skipped, or interrupted soak is not passing evidence.

## Readiness evidence

Create a signed machine-readable report containing runtime versions,
configuration hashes, scenario outcomes, delivery metrics, fault outcomes,
mapping warnings, and artifact checksums:

```sh
ump-readiness report \
  --fault-profile config/fault-baseline.json \
  --output .ump-lab/readiness.json
ump-readiness verify .ump-lab/readiness.json
```

Without `--signing-key`, UMP uses an ephemeral Ed25519 key. That proves report
integrity, not operator identity. Releases and hardware trials must use an
owner-controlled key.

The software gate passes only when the same reviewed revision completes the
full tests, protocol conformance, container transports, fault and load profiles,
Gazebo scenario, eight-hour soak, and signed report verification.

## Local awareness network

Generate and verify a three-participant mutual-TLS development network:

```sh
ump-deployment quickstart --output ump-local-lab
ump-deployment verify-local ump-local-lab
```

The generated adapters are read-only, advertise no executable capabilities,
and use development credentials. Start each `run.sh` in a separate terminal:

```sh
ump-local-lab/nodes/robot-humanoid-1/run.sh
ump-local-lab/nodes/robot-quadruped-1/run.sh
ump-local-lab/nodes/robot-arm-1/run.sh
```

Inspect one node's recorded network view:

```sh
ump-inspector \
  --database ump-local-lab/nodes/robot-humanoid-1/state/inspector.sqlite3 \
  --host 127.0.0.1 --port 8765
```

Visit `http://127.0.0.1:8765/`. The inspector is empty until compatible messages
reach that exact database. It never invents robots or telemetry.

## Production software deployment

UMP includes a non-root production image, a hardened Compose template, systemd
units, health and readiness probes, Prometheus metrics, authenticated inspector
access, and verified SQLite backup and restore tooling. These make the software
deployable as an owner-operated service; they do not certify a robot or replace
site-specific security and availability engineering.

```sh
docker build -f docker/Dockerfile.production -t ump:local .
docker compose -f deploy/compose.production.yml config
```

Long-running nodes expose `/healthz`, `/readyz`, and `/metrics` only when
`--operations-port` is configured. Keep that listener on loopback or a private
management network. Remote inspector access requires an explicit opt-in, a
32-byte-or-longer token, and TLS 1.3 credentials.

Follow [`docs/OPERATIONS.md`](docs/OPERATIONS.md) for preflight, deployment,
monitoring, backup, restore, upgrades, rollback, and the software release gate.

## Standards integrations

| Integration | Awareness | External work | Native responsibility |
| --- | --- | --- | --- |
| MassRobotics 1.0 | Bidirectional status mapping | Read-only by default | AMR and fleet behavior |
| VDA 5050 3.0 | Factsheet, state, pose, battery, safety | Mapping and lease required | AGV route execution and safety |
| Open-RMF | Fleet and participant state | Mapping and lease required | Traffic and facility scheduling |
| ROS 2 | UMP awareness topics | High-level assignment action | Vendor autonomy and safety |
| OPC UA Robotics 1.02 | Client import and filtered server view | No actuator methods | Plant model and controller |

Check optional runtimes and validate configuration before connecting:

```sh
ump-integration runtime vda5050
ump-integration runtime open_rmf
ump-integration runtime ros2
ump-integration runtime opc_ua
ump-integration validate integration.json
```

Integrations default to `read_only`. Task mode still requires an explicit
capability mapping, matching advertised capability, valid owner lease, correct
units and frames, and native controller acceptance. Missing data remains
unknown and is never guessed.

Run dependency-free mapping examples:

```sh
python3 examples/standards_mapping_demo.py massrobotics
python3 examples/standards_mapping_demo.py vda5050
python3 examples/standards_mapping_demo.py open_rmf
python3 examples/standards_mapping_demo.py ros2
python3 examples/standards_mapping_demo.py opc_ua
```

These prove conversion semantics, not a broker, ROS graph, RMF deployment,
vendor SDK, or physical robot.

## Connect a real robot

Physical integration is deliberately staged:

1. Start from `examples/read_only_adapter.py`; implement only `manifest()` and
   `state()` from bounded manufacturer-native APIs.
2. Advertise no capabilities and reject all assignments.
3. Run adapter conformance and bind evidence to the implementation.
4. Enroll one owner-issued identity per robot and configure strict disclosure.
5. Compare live identity, timestamps, units, frames, health, battery, safety,
   and freshness against the manufacturer's native tools.
6. Exercise disconnect, restart, stale state, replay, credential rotation, and
   storage recovery.
7. Add one bounded high-level capability first in the manufacturer's supported
   simulator.
8. Grant a short capability-specific authority lease and conduct the first
   physical assignment under operator and emergency-stop supervision.

```sh
ump-adapter-conformance inspect \
  --adapter manufacturer_package.adapter:create_adapter \
  --adapter-config /etc/manufacturer/robot.json \
  --output adapter-conformance.json
ump-adapter-conformance verify adapter-conformance.json \
  --implementation manufacturer_package/adapter.py
```

Follow [`docs/ROBOT_DEPLOYMENT.md`](docs/ROBOT_DEPLOYMENT.md) for certificates,
network preflight, authority leases, coordinator submission, and rollout.

## Troubleshooting

**The inspector is empty:** Confirm the node is running and the database path
matches its configuration. Opening `src/ump/inspector_static/index.html`
directly shows the frontend shell but does not connect to UMP data.

**A port is already in use:** Stop old profiles with
`docker compose -f docker-compose.lab.yml down`, then check for another local
service using that port.

**`IntegrationUnavailableError`:** Install `.[lab]` for Python probes. ROS 2,
Open-RMF, and Gazebo require their supported runtime or the Docker profiles.

**An assignment is rejected:** Check mapping, capability, lease scope and
expiry, identity, frame, units, health, battery, safety, freshness, and the
native rejection reason. UMP fails closed.

**The first Gazebo build appears stuck:** The image installs a large ROS 2 and
Gazebo dependency set. Watch Docker output and allow it to finish; later runs
reuse cached layers.

## Repository map

```text
src/ump/                 protocol, runtime, coordinator, inspector, and lab
src/ump/integrations/    standards adapters
schemas/                 public versioned JSON Schemas
conformance/v0.1/        valid and invalid protocol vectors
examples/                adapter and integration examples
config/                  network and fault configuration examples
ros2_interfaces/         separately buildable ROS 2 interfaces
gazebo/                  reference warehouse world
tests/                   unit, integration, transport, and readiness tests
docs/                    normative and operational documentation
```

## Documentation

The publishable Mintlify site lives in [`mintlify/`](mintlify/). Its maintainer
guide explains local validation and repository-based deployment.

- [`docs/PRD.md`](docs/PRD.md): product definition and requirements.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): boundaries and data flow.
- [`docs/PROTOCOL.md`](docs/PROTOCOL.md): normative UMP 0.1 behavior.
- [`docs/MANUFACTURER_ADAPTER.md`](docs/MANUFACTURER_ADAPTER.md): adapter contract.
- [`docs/NETWORK_PROFILE.md`](docs/NETWORK_PROFILE.md): secure network profile.
- [`docs/ROBOT_DEPLOYMENT.md`](docs/ROBOT_DEPLOYMENT.md): physical integration.
- [`docs/INSPECTOR.md`](docs/INSPECTOR.md): read-only operational UI.
- [`docs/STANDARDS_INTEGRATIONS.md`](docs/STANDARDS_INTEGRATIONS.md): mappings.
- [`docs/HARDWARE_READINESS.md`](docs/HARDWARE_READINESS.md): qualification.
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md): production software runbook.
- [`REFERENCE.md`](REFERENCE.md): standards, reuse policy, and UMP gaps.
- [`SECURITY.md`](SECURITY.md): security policy and reporting.
- [`CONTRIBUTING.md`](CONTRIBUTING.md): repository conventions.

## Contributing and license

Use lowercase `snake_case.py`, versioned kebab-case schema names, and
Conventional Commits. Keep optional robotics runtimes lazy-loaded and preserve
the UMP 0.1 safety boundary. See [`LICENSE`](LICENSE) and
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
