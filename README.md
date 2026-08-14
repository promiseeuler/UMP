# Universal Machine Protocol (UMP)

UMP is an open, vendor-neutral interoperability protocol for autonomous machines. It lets robots, drones, vehicles, industrial equipment, and software agents discover one another, advertise capabilities, coordinate shared work, transfer responsibility, and communicate safety state across manufacturer and operating-system boundaries.

UMP is software-first. A machine needs only one supported integration path:

1. Run the UMP runtime directly on its onboard computer.
2. Install the UMP ROS 2 package.
3. Connect UMP through an approved vendor SDK or controller bridge.

Machines that cannot host or expose software can use an external UMP gateway as a fallback. No additional hardware is required when any of the first three paths is available.

## Project documents

- [Product Requirements Document](./docs/PRD.md)
- [Engineering Roadmap](./docs/ROADMAP.md)
- [Implementation Status](./docs/STATUS.md)

## Status

Phases 0 through 3 are complete. UMP now has authenticated discovery and state exchange; durable tasks and authority; atomic shared-resource reservations; validated spatial context; and ownership-safe machine-to-machine handoffs. Phase 4 is awaiting native Gazebo evidence. Phase 5 native installation work has started with a Debian package, hardened service unit, and rootless container profile.

## Run the first simulation

Rust is the only required local toolchain; the Protocol Buffers compiler is bundled by the build.

```sh
cargo test --workspace
cargo run -p ump-sim --bin s0
cargo run --release -p ump-sim --bin s1
cargo run --release -p ump-sim --bin s2
cargo run --release -p ump-sim --bin s3
make sim-s4-protocol
```

The S0 command proves mutual protocol negotiation and deterministic peer expiry. S1 exercises heterogeneous discovery, protected metadata, replay and revision checks, credential failure, peer disappearance, and telemetry backpressure.
S2 exercises authority, cancellation, bounded retries, crashes, partitions, reconciliation, conflicting issuers, and uncertain physical outcomes.
S3 exercises exclusive transfer zones, spatial freshness, bilateral handoff evidence, ownership commit, crashes, timeouts, aborts, and contradictory evidence.
The architecture-neutral S4 checkpoint runs the six-task warehouse mission
through independent QUIC runtimes and covers active cancellation, runtime
restart, lease expiry, and an impaired network path. On native Ubuntu 24.04,
`make ros2-smoke` runs the Gazebo plugin gate and `make ros2-s4` runs the full
embodied mission.

## Local runtime

Initialize and validate a development-only machine identity:

```sh
cargo run -p ump-cli --bin ump -- --data-dir .ump init \
  --machine-id ump:machine:my-robot \
  --machine-class mobile_base
cargo run -p ump-cli --bin ump -- --data-dir .ump doctor
cargo run -p ump-cli --bin ump -- --data-dir .ump register-capability \
  --capability org.ump.logistics.deliver --interruptible
cargo run -p ump-cli --bin ump -- --data-dir .ump run --once
```

`umpd` runs the same QUIC runtime continuously. Peer certificates must be enrolled with `ump trust`; envelope identity is checked against the enrolled certificate fingerprint before protocol processing.

```sh
cargo run -p ump-cli --bin ump -- --data-dir .ump trust \
  --machine-id ump:machine:peer \
  --certificate peer-cert.der \
  --root-certificate peer-root.der \
  --allow-tasks
```

Metadata, task issuance, and coordination are denied independently by default. Add `--allow-metadata`, `--allow-tasks`, or `--allow-coordination` only for the corresponding authorized peer. Before accepting work, register the local capability and grant a time-bounded lease with `ump grant-lease`. Shared zones and tools are registered with `ump register-resource`. The generated credentials are for local development, not production enrollment.

Linux deployment instructions for the Debian service and rootless container are
in [the native installation guide](./docs/guides/native-installation.md).

## Core principle

UMP coordinates machines; it does not replace their native autonomy, motion-control, or certified safety systems. A UMP task can request `move_payload`, while the robot's own controller remains responsible for trajectories, obstacle avoidance, actuator limits, and emergency stopping.
