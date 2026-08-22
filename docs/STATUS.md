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

## Deliberately outside the core

- Motor, joint, navigation, manipulation, and emergency-stop control.
- Vendor autonomy, collision avoidance, and native safety systems.
- Facility traffic management and fleet scheduling.
- A built-in reasoning model.
- A bundled professional simulator or digital-twin UI.
- Vendor-specific SDK code and external standards implementations.

Those capabilities connect through adapters. Related standards and intended
integration boundaries are recorded in the repository `REFERENCE.md`.

## Next engineering work

1. Stabilize the canonical awareness schema and protocol versioning rules.
2. Exercise two independently written adapters on a real local network.
3. Add field-level compatibility matrices, beginning with MassRobotics.
4. Implement one optional standards adapter without adding vendor assumptions to
   the core.
5. Conduct supervised read-only hardware tests before enabling assignments.

The current implementation is not a certification and is not suitable for
unsupervised physical robot operation.
