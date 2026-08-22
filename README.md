# Universal Machine Protocol

Universal Machine Protocol (UMP) is a manufacturer-neutral semantic layer for
shared robot awareness and high-level collaboration.

Robots use a common schema to publish identity, capabilities, activity, intent,
progress, availability, safety, health, battery, and spatial context. UMP may
carry shared goals and validated assignments, but it never issues actuator,
navigation, manipulation, or emergency-stop commands. Those remain inside each
robot's native controller and safety system.

## Current core

- Versioned protocol models and canonical encoding.
- Manufacturer adapter contract with a read-only starting mode.
- In-memory and mutual-TLS network transports.
- Peer discovery, authentication, replay protection, and disclosure controls.
- Robot registry and expiring semantic state.
- Shared goals, planner boundary, assignments, outcomes, and cancellation.
- Durable coordinator, authority, credential, and assignment stores.
- Conformance vectors and adapter validation.
- Read-only inspector that displays only recorded UMP traffic.
- Local awareness-network generator for development.

## Install

UMP requires Python 3.11 or newer.

```sh
git clone https://github.com/promiseeuler/UMP.git
cd UMP
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install .
```

## Run the core checks

```sh
python3 -m unittest discover -s tests -v
ump-demo
ump-conformance conformance/v0.1
```

## Run a local awareness network

```sh
ump-deployment quickstart --output ump-local-lab
ump-deployment verify-local ump-local-lab
```

The generated nodes use development-only certificates and read-only adapters.
They advertise no executable capabilities and must not be used as a physical
robot deployment.

Start the generated nodes, then inspect one node's local view:

```sh
ump-local-lab/nodes/robot-humanoid-1/run.sh
ump-inspector \
  --database ump-local-lab/nodes/robot-humanoid-1/state/inspector.sqlite3 \
  --port 8765
```

The inspector starts empty when no node has published into its selected
database. It does not invent robots or telemetry.

## Documentation

- [`docs/PRD.md`](docs/PRD.md): product definition and requirements.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): boundaries and data flow.
- [`docs/PROTOCOL.md`](docs/PROTOCOL.md): normative protocol behavior.
- [`docs/MANUFACTURER_ADAPTER.md`](docs/MANUFACTURER_ADAPTER.md): native adapter contract.
- [`docs/NETWORK_PROFILE.md`](docs/NETWORK_PROFILE.md): secure network profile.
- [`docs/ROBOT_DEPLOYMENT.md`](docs/ROBOT_DEPLOYMENT.md): staged robot integration.
- [`docs/INSPECTOR.md`](docs/INSPECTOR.md): read-only operational UI.
- [`docs/STANDARDS_INTEGRATIONS.md`](docs/STANDARDS_INTEGRATIONS.md): standards mappings and setup.
- [`REFERENCE.md`](REFERENCE.md): related standards, reuse policy, and UMP gaps.
- [`CONTRIBUTING.md`](CONTRIBUTING.md): commit and filename conventions.

## Project status

UMP is an alpha reference implementation. Physical deployments require a
manufacturer adapter, robot-owner authorization, independent safety review, and
the robot's existing native safety controls.

Run a dependency-free standards mapping fixture with:

```sh
python3 examples/standards_mapping_demo.py massrobotics
ump-integration schema
```
