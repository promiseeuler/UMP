# Universal Machine Protocol

Universal Machine Protocol (UMP) is a lightweight, manufacturer-neutral
communication layer for shared robot awareness and collaboration.

UMP lets robots describe their capabilities, current activity, intent, and
state in a common format. It can carry shared goals and validated microtask
plans, but it never controls actuators or replaces a robot's native autonomy
and safety systems.

## Run the reference demo

UMP v0 requires Python 3.11 or newer and has a dependency-light runtime.

```sh
PYTHONPATH=src python3 -m ump.demo
python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m ump.cli conformance conformance/v0.1
PYTHONPATH=src python3 -m ump.cli benchmark
```

For two-host deployment measurements, see [docs/BENCHMARKS.md](docs/BENCHMARKS.md).

Manufacturers can begin with the documented [adapter contract](docs/MANUFACTURER_ADAPTER.md)
and the runnable read-only example in `examples/read_only_adapter.py`.

The demo connects three simulated robots from different manufacturers through
an in-memory transport. A replaceable planner assigns route inspection,
transport, and placement steps from one shared goal.

## Repository map

- `docs/PRD.md`: product requirements and delivery milestones.
- `docs/ARCHITECTURE.md`: component boundaries and data flow.
- `docs/PROTOCOL.md`: normative v0.1 encoding and lifecycle rules.
- `docs/NETWORK_PROFILE.md`: mutual-TLS and discovery alpha profile.
- `docs/AUTHORITY.md`: robot-local assignment leases and owner CLI.
- `docs/CREDENTIALS.md`: issued-certificate enrollment, rotation, and revocation.
- `docs/COORDINATOR.md`: owner goal submission and durable run status workflow.
- `docs/NODE.md`: owner-facing long-running participant service.
- `docs/PLANNER.md`: reasoning-provider contract and validation boundary.
- `docs/RECONCILIATION.md`: evidence-based resolution of uncertain native work.
- `docs/ROS2_GAZEBO.md`: manufacturer ROS action adapter and simulator profile.
- `docs/CONFORMANCE.md`: golden vectors and safe adapter validation workflow.
- `docs/INTEROPERABILITY.md`: SI unit and coordinate-frame schema profile.
- `docs/VOCABULARY.md`: versioned standard high-level capability contracts.
- `docs/BENCHMARKS.md`: local and two-host benchmarks and evidence validation.
- `docs/READINESS.md`: functional traceability and production qualification policy.
- `docs/VERSIONING.md`: package versioning and release-artifact verification.
- `docs/INSPECTOR.md`: read-only local protocol inspector setup.
- `docs/HARDWARE_PILOT.md`: physical-pilot evidence profile and verifier.
- `docs/STATUS.md`: implemented behavior and explicit production gaps.
- `conformance/v0.1`: byte-exact valid and invalid protocol vectors.
- `schemas/ump-v0.schema.json`: canonical JSON Schema for wire messages.
- `src/ump`: dependency-light reference implementation.
- `tests`: protocol and collaboration tests.

The reference runtime is transport-neutral. The repository includes a mutual-TLS
network profile and a ROS 2/Gazebo conformance adapter; Isaac Sim, Webots, and
physical robot adapters remain later layers over the same contracts.

This repository is currently a reference foundation. It is not yet suitable for
unsupervised physical robot operation; see `docs/STATUS.md` for the exact gap list.
