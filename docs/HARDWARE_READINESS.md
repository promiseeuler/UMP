# Hardware-Readiness Laboratory

The UMP laboratory tests the manufacturer-neutral boundary without claiming
that software simulation certifies arbitrary physical robots. It isolates the
remaining hardware risk to a reviewed manufacturer adapter, native controller,
firmware, safety system, and operating environment.

## What the laboratory proves

The reference scenario uses three independent controller emulators:

- `mobile-1` transports a payload to a workcell.
- `inspector-1` reports whether the workcell is ready.
- `manipulator-1` places the payload only after both dependencies succeed.

Every assignment passes through UMP plan validation, a robot-local scoped
authority lease, the `RobotAdapter` boundary, and native-style controller
acceptance. The controller rejects unsupported capabilities, incorrect frames,
low battery, faulted health, unsafe state, and disconnection. Unknown battery
telemetry remains absent instead of being guessed.

## Local commands

Install the core or the optional transport dependencies:

```sh
python3 -m pip install .
python3 -m pip install '.[lab]'
```

Run the deterministic scenario and all bounded load profiles:

```sh
ump-lab run --output .ump-lab/scenario.json
ump-lab load --participants 3 --cycles 10
ump-lab load --participants 25 --cycles 10
ump-lab load --participants 100 --cycles 10
ump-lab load --participants 250 --cycles 10
ump-lab fault config/fault-baseline.json --output .ump-lab/faults.json
```

Generate and verify integrity-bound Ed25519 evidence:

```sh
ump-readiness report \
  --fault-profile config/fault-baseline.json \
  --output .ump-lab/readiness.json
ump-readiness verify .ump-lab/readiness.json
```

When `--signing-key` is omitted, the report uses an ephemeral key and embeds
the public key. This proves report integrity, not operator identity. A release
or hardware trial must provide an owner-controlled Ed25519 private key and
retain its public-key identity through the owner's evidence process.

## Docker profiles

Docker Compose is the repeatable macOS and Linux entry point:

```sh
ump-lab up --profile core
ump-lab status --profile core
ump-lab down --profile core

docker compose -f docker-compose.lab.yml --profile core run --build --rm core
docker compose -f docker-compose.lab.yml --profile standards up -d --wait mqtt
docker compose -f docker-compose.lab.yml --profile standards run --build --rm standards
docker compose -f docker-compose.lab.yml --profile standards run --rm standards python -m ump.lab_services opc_ua
docker compose -f docker-compose.lab.yml --profile standards run --build --rm ros2
docker compose -f docker-compose.lab.yml --profile fault run --build --rm fault-test
docker compose -f docker-compose.lab.yml --profile visual run --build --rm visual
```

The standards probes exchange real messages over WebSocket, MQTT, OPC UA, and
ROS 2 transports. Open-RMF remains responsible for traffic and facility
scheduling; its UMP bridge is validated through mapping and ROS 2 task fixtures
until an RMF deployment is selected. MassRobotics WebSocket lifecycle remains
owner-controlled in production.

The `visual` profile loads `gazebo/warehouse.sdf` headlessly with Gazebo Harmonic
through ROS 2 Jazzy. Its model names match the UMP robot identities. The world
does not create a simulator-only UMP control path: future model motion must
enter the same ROS 2 or manufacturer adapter used by hardware.

## Faults, load, and soak

`FaultProfile` produces deterministic decisions for latency, jitter, loss,
duplication, reordering, partitions, and broker restart checkpoints. The Docker
fault profile also routes MQTT through Toxiproxy to prove a real delayed socket
path. Existing UMP tests cover replay, stale state, retries, durable restart,
reconciliation, cancellation, credential rotation, and identity conflicts.

The production soak command runs for real wall-clock time:

```sh
ump-lab soak --duration-s 28800 --participants 25 --state-hz 2
```

The weekly workflow requires a self-hosted Linux runner labeled `ump-soak`.
GitHub-hosted jobs cannot provide one continuous eight-hour qualification run.
A queued or absent soak job is not passing evidence.

## Evidence interpretation

A readiness report records runtime versions, canonical configuration hash,
scenario outcome, latency fields, delivery counts, fault decisions, mapping
warnings, artifact hashes, public key, and signature. A valid signature proves
that the report has not changed. It does not prove that unlisted middleware,
vendor firmware, or physical equipment behaves identically.

The software gate passes when the full test suite, protocol conformance,
container scenario, live transport probes, load profiles, Gazebo smoke test,
and completed eight-hour soak all pass for the same reviewed revision.

## Hardware handoff

After this gate, recruit one robot owner and begin with a read-only manufacturer
adapter. Compare live identity, timestamps, units, coordinate frames, health,
battery, safety, activity, and freshness against native tooling. Only then add
one bounded capability with a short owner-issued authority lease in a controlled
area under physical emergency-stop supervision. Repeat qualification for every
adapter, firmware, configuration, and capability change.
