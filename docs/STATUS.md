# Implementation Status

UMP is an alpha reference implementation of a manufacturer-neutral semantic
awareness and high-level collaboration protocol.

## Implemented core

- Canonical versioned messages with bounded validation and conformance vectors.
- Robot manifests, capabilities, semantic activity, intent, progress, spatial
  context, safety, operational health, and optional battery telemetry.
- Manufacturer adapter contract with read-only and high-level assignment paths.
- In-memory and mutual-TLS transports, peer discovery, authentication, replay
  protection, message disclosure policy, retry handling, and diagnostics.
- Expiring peer registry and persistent inspection records.
- Shared goals, replaceable planners, validated dependency plans, assignments,
  acknowledgements, outcomes, cancellation, reconciliation, and durable stores.
- Robot-local scoped authority leases and credential lifecycle support.
- Local awareness-network generation and a read-only inspector UI.
- Standards mappings for MassRobotics, VDA 5050, Open-RMF, ROS 2, and OPC UA.
- Deterministic mixed-fleet controller emulators, signed readiness reports,
  fault/load/soak probes, live transport profiles, and a Gazebo reference world.

## Deliberately outside the core

- Motor, joint, navigation, manipulation, and emergency-stop control.
- Vendor autonomy, collision avoidance, and native safety systems.
- Facility traffic management and fleet scheduling.
- A built-in reasoning model.
- Vendor-specific SDK code and external standards implementations.

Those capabilities connect through adapters. Related standards and intended
integration boundaries are recorded in the repository `REFERENCE.md`.

## Next engineering work

1. Complete a recorded eight-hour soak on the designated self-hosted runner.
2. Exercise independently written adapters in manufacturer-supported simulators.
3. Conduct supervised read-only hardware tests with a robot owner.
4. Qualify one bounded native capability at a time.

The current implementation is not a certification and is not suitable for
unsupervised physical robot operation.
