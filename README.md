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

Phase 0 infrastructure and the first deterministic protocol slice are under development. The repository contains Protocol Buffers schemas, a Rust runtime kernel, and the S0 two-machine simulation.

## Run the first simulation

Rust is the only required local toolchain; the Protocol Buffers compiler is bundled by the build.

```sh
cargo test --workspace
cargo run -p ump-sim --bin s0
```

The S0 command prints a machine-readable JSON report proving mutual protocol negotiation and deterministic peer expiry.

## Core principle

UMP coordinates machines; it does not replace their native autonomy, motion-control, or certified safety systems. A UMP task can request `move_payload`, while the robot's own controller remains responsible for trajectories, obstacle avoidance, actuator limits, and emergency stopping.
