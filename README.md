# Universal Machine Protocol

Universal Machine Protocol (UMP) is a manufacturer-neutral
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
PYTHONPATH=src python3 -m ump.cli simulate run --project-root .
PYTHONPATH=src python3 -m ump.cli visual-sim --port 8766
```

For two-host deployment measurements, see [docs/BENCHMARKS.md](docs/BENCHMARKS.md).

Manufacturers can begin with the documented [adapter contract](docs/MANUFACTURER_ADAPTER.md)
and the runnable read-only example in `examples/read_only_adapter.py`.
Robot owners can follow the staged [deployment path](docs/ROBOT_DEPLOYMENT.md),
starting with simulation and awareness-only operation before granting work.
Hardware teams joining the alpha should use the
[design-partner process](docs/DESIGN_PARTNER_PROGRAM.md).

The demo connects three simulated robots from different manufacturers through
an in-memory transport. A replaceable planner assigns route inspection,
transport, and placement steps from one shared goal.

## Install UMP

UMP v0 requires Python 3.11 or newer. Until the first signed release is
published, install the reviewed checkout into an isolated environment:

```sh
git clone https://github.com/promiseeuler/UMP.git
cd UMP
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install .
ump-demo
```

Install the runtime on each robot's onboard or companion computer, not inside a
motor controller or safety PLC. UMP does not replace native autonomy, emergency
stops, or manufacturer safety systems. Physical robots require a reviewed
manufacturer adapter and owner-issued certificates; follow
[`docs/ROBOT_DEPLOYMENT.md`](docs/ROBOT_DEPLOYMENT.md).

## Run a local robot network

Create a three-robot, awareness-only localhost lab with no external services:

```sh
ump-deployment quickstart --output ump-local-lab
ump-deployment verify-local ump-local-lab
```

`quickstart` generates unique short-lived development certificates,
fingerprint-pinned mutual-TLS peer configurations, isolated durable stores,
read-only adapters, preflight scripts, and launch scripts. `verify-local` starts
all generated TLS endpoints and succeeds only when every robot receives every
peer manifest and semantic state.

Run preflight for each generated node:

```sh
ump-local-lab/nodes/robot-humanoid-1/preflight.sh
ump-local-lab/nodes/robot-quadruped-2/preflight.sh
ump-local-lab/nodes/robot-mobile-arm-3/preflight.sh
```

Then start each node in a separate terminal:

```sh
ump-local-lab/nodes/robot-humanoid-1/run.sh
ump-local-lab/nodes/robot-quadruped-2/run.sh
ump-local-lab/nodes/robot-mobile-arm-3/run.sh
```

Each node now publishes identity and semantic state over mutual TLS and observes
the other two nodes. The generated adapters advertise no capabilities, so the
network cannot assign physical work. Inspect one node's local view with:

```sh
ump-inspector \
  --database ump-local-lab/nodes/robot-humanoid-1/state/inspector.sqlite3 \
  --port 8765
```

Use the interactive setup when choosing your own identities and robot classes:

```sh
ump-deployment wizard --output my-ump-lab
```

For repeatable automation, edit [`config/local-lab.example.json`](config/local-lab.example.json)
and run:

```sh
ump-deployment generate \
  --topology config/local-lab.example.json \
  --output my-ump-lab \
  --development-pki
```

Development PKI bundles are marked `production_eligible: false` and must never
be installed on physical deployments. Replace them with the owner's PKI and the
staged credential process in [`docs/CREDENTIALS.md`](docs/CREDENTIALS.md).

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
- `docs/WEBOTS_ROS2.md`: primary Webots and ROS 2 visual warehouse profile.
- `docs/WEBOTS_VM.md`: compatible VM setup for the live Webots 3D demonstration.
- `docs/ROS2_GAZEBO.md`: manufacturer ROS action adapter and simulator profile.
- `docs/SIMULATED_QUALIFICATION.md`: repeatable non-production simulation report.
- `docs/ROBOT_DEPLOYMENT.md`: staged owner and manufacturer deployment path.
- `docs/CONFORMANCE.md`: golden vectors and safe adapter validation workflow.
- `docs/INTEROPERABILITY.md`: SI unit and coordinate-frame schema profile.
- `docs/VOCABULARY.md`: versioned standard high-level capability contracts.
- `docs/BENCHMARKS.md`: local and two-host benchmarks and evidence validation.
- `docs/READINESS.md`: functional traceability and production qualification policy.
- `docs/VERSIONING.md`: package versioning and release-artifact verification.
- `docs/PUBLIC_RELEASE.md`: public-visibility audit and GitHub publication checklist.
- `docs/INSPECTOR.md`: read-only local protocol inspector setup.
- `docs/HARDWARE_PILOT.md`: physical-pilot evidence profile and verifier.
- `docs/INDEPENDENT_REVIEWS.md`: external review evidence and finding policy.
- `docs/DESIGN_PARTNER_PROGRAM.md`: partner intake, staged tests, and evidence process.
- `docs/STATUS.md`: implemented behavior and explicit production gaps.
- `conformance/v0.1`: byte-exact valid and invalid protocol vectors.
- `schemas/ump-v0.schema.json`: canonical JSON Schema for wire messages.
- `schemas/ump-network-config-v1.schema.json`: strict deployment configuration.
- `schemas/ump-deployment-topology-v1.schema.json`: local-lab topology contract.
- `schemas/ump-shared-goal-v1.schema.json`: strict owner goal document contract.
- `schemas/ump-shared-goal-batch-v1.schema.json`: bounded atomic goal-batch contract.
- `schemas/ump-release-evidence-v1.schema.json`: retained release qualification bundle.
- `src/ump`: dependency-light reference implementation.
- `tests`: protocol and collaboration tests.

The reference runtime is transport-neutral. The repository includes a mutual-TLS
network profile, a Webots visual warehouse integration, and a ROS 2/Gazebo
conformance adapter. Isaac Sim and physical robot adapters remain later layers
over the same contracts.

This repository is currently a reference foundation. It is not yet suitable for
unsupervised physical robot operation; see `docs/STATUS.md` for the exact gap list.
