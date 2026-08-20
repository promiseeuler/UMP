# Universal Machine Protocol

Universal Machine Protocol (UMP) is a lightweight, manufacturer-neutral
communication layer for shared robot awareness and collaboration.

UMP lets robots describe their capabilities, current activity, intent, and
state in a common format. It can carry shared goals and validated microtask
plans, but it never controls actuators or replaces a robot's native autonomy
and safety systems.

## Run the reference demo

UMP v0 requires Python 3.11 or newer and has no runtime dependencies.

```sh
PYTHONPATH=src python3 -m ump.demo
python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m ump.cli conformance conformance/v0.1
PYTHONPATH=src python3 -m ump.cli benchmark
```

For two-host deployment measurements, see [docs/BENCHMARKS.md](docs/BENCHMARKS.md).

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
- `docs/RECONCILIATION.md`: evidence-based resolution of uncertain native work.
- `docs/ROS2_GAZEBO.md`: manufacturer ROS action adapter and simulator profile.
- `docs/CONFORMANCE.md`: golden vectors and safe adapter validation workflow.
- `docs/INTEROPERABILITY.md`: SI unit and coordinate-frame schema profile.
- `docs/VOCABULARY.md`: versioned standard high-level capability contracts.
- `docs/BENCHMARKS.md`: reproducible reference-runtime quality measurements.
- `docs/READINESS.md`: executable PRD requirement traceability policy.
- `docs/VERSIONING.md`: package versioning and release-artifact verification.
- `docs/INSPECTOR.md`: read-only local protocol inspector setup.
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
